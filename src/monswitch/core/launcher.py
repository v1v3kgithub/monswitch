"""Launcher-facing logic: result rows, actions, and a cached discovery snapshot.

Lives in the core rather than in the Ulauncher extension so that it can be tested without a
running launcher daemon, and so the extension stays the thin wrapper the conventions require
(ADR-0005, ADR-0012). The extension's job is to translate :class:`LauncherItem` into its own
widget type and to hand actions back; everything decided here is decided once.

Two problems are specific to a launcher and solved here.

**The first keystroke must not wait on hardware.** Discovery costs a live DDC/CI read per
monitor — 0.35s measured, and 1.5s if it falls back to ``ddcutil detect``. Typing into a
launcher cannot block on that, so results are rendered from :class:`DiscoverySnapshot`, which
serves the last known state immediately and refreshes behind it.

**A stale snapshot must not make a switch look safe.** The Host Input marker is what the
confirmation policy keys on (ADR-0013), so a snapshot that has gone stale is explicitly
untrusted: its rows are treated as unverified until a fresh read lands (ADR-0012). The cost is
an extra confirmation on a cold cache; the alternative is switching away from the host without
a warning because the cache said something no longer true.

Confirmation itself reuses :class:`~monswitch.core.picker.PickerModel`'s two-step arming, so
the launcher and the GTK picker cannot drift apart on the one behaviour where drifting is
dangerous.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique

from .discovery import Discovery
from .models import Confirmation, KeyedMonitor
from .picker import ActivationKind, PickerModel, PickerRow

__all__ = [
    "DEFAULT_SNAPSHOT_TTL",
    "ActionKind",
    "DiscoverySnapshot",
    "ItemAction",
    "LauncherItem",
    "LauncherSession",
    "build_items",
    "resolve_action",
]

DEFAULT_SNAPSHOT_TTL = 10.0
"""Seconds a discovery result is served before it is considered stale.

Short enough that a monitor unplugged mid-session is noticed quickly, long enough that
repeated keystrokes in one launcher session share a single read.
"""


@unique
class ActionKind(Enum):
    """What activating a row should do."""

    SWITCH = "switch"
    """Perform the switch now. Either harmless, or already confirmed."""

    ARM = "arm"
    """Re-render with this row asking for confirmation."""

    BLOCKED = "blocked"
    """Refuse: the monitor's current input is unknown (ADR-0013)."""

    NOTHING = "nothing"
    """Informational row. Selecting it does nothing."""


@dataclass(frozen=True, slots=True)
class ItemAction:
    """An action attached to a result row.

    Carries the bus and code rather than an index, because the launcher round-trips this
    through its own event system and the row order may have changed by the time it comes
    back — resolving by position could switch an input the user never selected.
    """

    kind: ActionKind
    bus: int | None = None
    code: int | None = None

    def as_data(self) -> dict[str, object]:
        """A plain dict, for a launcher that must serialise the action."""
        return {"kind": self.kind.value, "bus": self.bus, "code": self.code}

    @classmethod
    def from_data(cls, data: object) -> ItemAction:
        """Rebuild from :meth:`as_data`, tolerating anything unexpected.

        Unrecognised input becomes :attr:`ActionKind.NOTHING` rather than raising: this data
        crosses a process boundary, and the safe failure for a malformed switch request is to
        do nothing at all.
        """
        if not isinstance(data, dict):
            return cls(kind=ActionKind.NOTHING)
        try:
            kind = ActionKind(data.get("kind"))
        except ValueError:
            return cls(kind=ActionKind.NOTHING)
        bus = data.get("bus")
        code = data.get("code")
        return cls(
            kind=kind,
            bus=bus if isinstance(bus, int) else None,
            code=code if isinstance(code, int) else None,
        )


@dataclass(frozen=True, slots=True)
class LauncherItem:
    """One row in a launcher's result list, toolkit-agnostic."""

    title: str
    subtitle: str
    action: ItemAction
    is_warning: bool = False

    @property
    def selectable(self) -> bool:
        return self.action.kind is not ActionKind.NOTHING


def _subtitle_for(row: PickerRow, *, armed: bool, snapshot_stale: bool) -> str:
    parts: list[str] = [row.monitor.display_name]

    if armed:
        if row.confirmation is Confirmation.UNVERIFIED_DESTINATION:
            return "Press Enter again to switch — unverified, the screen may go blank"
        return "Press Enter again to switch — this monitor will stop showing this machine"

    if row.is_blocked:
        parts.append("current input unreadable; cannot switch")
        return " · ".join(parts)

    if row.is_active:
        parts.append("current input, this machine")
    elif not row.is_verified:
        parts.append("unverified")

    if snapshot_stale:
        parts.append("checking…")

    parts.append(row.source.hex_code)
    return " · ".join(parts)


def build_items(
    model: PickerModel,
    *,
    snapshot_stale: bool = False,
    empty_message: str | None = None,
) -> tuple[LauncherItem, ...]:
    """Render the model's visible rows as launcher items.

    ``snapshot_stale`` marks that the underlying data has not been confirmed by a fresh read.
    It is surfaced in the subtitle rather than hidden, because the user is about to act on it.
    """
    rows = model.rows
    if not rows:
        return (
            LauncherItem(
                title=empty_message or "No matching input",
                subtitle="Type part of a monitor or input name",
                action=ItemAction(kind=ActionKind.NOTHING),
            ),
        )

    items: list[LauncherItem] = []
    for row in rows:
        armed = model.is_armed(row)
        if row.is_blocked:
            kind = ActionKind.BLOCKED
        elif armed or not row.needs_confirmation or not model.arms_before_switching:
            kind = ActionKind.SWITCH
        else:
            kind = ActionKind.ARM

        items.append(
            LauncherItem(
                title=model.display_text(row),
                subtitle=_subtitle_for(row, armed=armed, snapshot_stale=snapshot_stale),
                action=ItemAction(kind=kind, bus=row.monitor.bus, code=row.source.code),
                is_warning=armed,
            )
        )
    return tuple(items)


def resolve_action(
    model: PickerModel, action: ItemAction
) -> tuple[ActivationKind, PickerRow | None]:
    """Apply a returned action to the model, by bus and code rather than by position.

    Returns what the launcher should do next, mirroring
    :meth:`~monswitch.core.picker.PickerModel.activate` so the arming behaviour is shared
    rather than reimplemented.
    """
    if action.bus is None or action.code is None:
        return ActivationKind.NOTHING, None

    armed = model.armed_row
    for index, row in enumerate(model.rows):
        if row.monitor.bus == action.bus and row.source.code == action.code:
            # Selecting here is a lookup, not a user movement, so it must not clear this
            # row's own arming -- otherwise the second Enter re-arms instead of switching
            # and a confirmed switch is unreachable from a launcher.
            model.select(index, preserve_arm=armed == row)
            activation = model.activate()
            return activation.kind, activation.row

    # The row is gone -- the monitor was unplugged, or discovery refreshed underneath the
    # user. Doing nothing is the only safe answer; switching "the nearest match" is how you
    # change the wrong input.
    return ActivationKind.NOTHING, None


@dataclass
class DiscoverySnapshot:
    """Serves the last discovery result immediately, refreshing when stale.

    The launcher daemon is long-lived, so this holds state across queries. It never refreshes
    at import time (ADR-0012 forbids import-time side effects): the first query pays for the
    first read.
    """

    discover: Callable[[], Discovery]
    ttl: float = DEFAULT_SNAPSHOT_TTL
    clock: Callable[[], float] = time.monotonic
    _result: Discovery | None = field(default=None, init=False, repr=False)
    _fetched_at: float = field(default=0.0, init=False, repr=False)

    @property
    def has_data(self) -> bool:
        return self._result is not None

    @property
    def is_stale(self) -> bool:
        """Whether the held result is older than the TTL, or absent."""
        if self._result is None:
            return True
        return (self.clock() - self._fetched_at) >= self.ttl

    def get(self, *, refresh_if_stale: bool = True) -> Discovery:
        """The current result, refreshing first when stale.

        A failed refresh keeps the previous result rather than discarding it: stale data that
        is honestly labelled is more useful than an empty list, and it is marked stale so the
        confirmation policy stays conservative.
        """
        if refresh_if_stale and self.is_stale:
            try:
                self._result = self.discover()
                self._fetched_at = self.clock()
            except Exception:
                if self._result is None:
                    raise
        assert self._result is not None
        return self._result

    def invalidate(self) -> None:
        """Force the next :meth:`get` to refresh. Call after a switch."""
        self._fetched_at = 0.0

    def model(
        self, *, confirm_switch: bool = True, require_confirmation: bool = True
    ) -> PickerModel:
        """A :class:`PickerModel` over the current result.

        :meth:`get` refreshes first, so data that was stale but refreshed successfully is
        trusted. Data that is *still* stale afterwards -- meaning the refresh failed and the
        previous result is being served -- is not: the model is built with
        ``confirm_switch=True`` regardless of the user's preference, so an opt-out cannot
        combine with unconfirmed data to switch away from the host unwarned (ADR-0012).
        """
        result = self.get()
        monitors: Sequence[KeyedMonitor] = result.monitors
        trusted = not self.is_stale
        return PickerModel(
            monitors,
            confirm_switch=confirm_switch if trusted else True,
            require_confirmation=require_confirmation,
        )


@dataclass
class LauncherSession:
    """Holds a launcher's model across queries and activations.

    This exists because the obvious way to write it is wrong, and was. A launcher handles two
    kinds of event — a query, and an item being activated — and the model must be reached
    differently for each:

    * a **query** applies :meth:`~monswitch.core.picker.PickerModel.set_query`, which drops any
      pending confirmation. That is correct: typing something new should cancel an armed row.
    * an **activation** must *not* touch the query, because ``set_query`` disarms. Routing
      Enter through it meant every press re-armed the row and nothing ever switched — the
      confirmation was unreachable, with the user pressing Enter repeatedly to no effect.

    Keeping both paths here, rather than in the extension, is what makes the distinction
    testable without a launcher daemon.
    """

    snapshot: DiscoverySnapshot
    confirm_switch: bool = True
    require_confirmation: bool = True
    """``False`` makes the first Enter switch, as the user asked for (ADR-0017)."""

    _model: PickerModel | None = field(default=None, init=False, repr=False)

    def _ensure_model(self) -> PickerModel:
        """The current model, rebuilt when the snapshot has moved on.

        A rebuild drops any armed row, which is the safe direction: the confirmation was
        armed against data that has since changed.
        """
        if self._model is None or self.snapshot.is_stale:
            self._model = self.snapshot.model(
                confirm_switch=self.confirm_switch,
                require_confirmation=self.require_confirmation,
            )
        return self._model

    def items_for(self, query: str) -> tuple[LauncherItem, ...]:
        """Rows for a query. Changing the query cancels any pending confirmation."""
        model = self._ensure_model()
        model.set_query(query)
        return build_items(
            model,
            snapshot_stale=self.snapshot.is_stale,
            empty_message=(
                "No external monitor with DDC/CI found" if not model.rows and not query else None
            ),
        )

    def activate(self, action: ItemAction) -> tuple[ActivationKind, PickerRow | None]:
        """Apply an activation, leaving the query and any arming untouched.

        Deliberately does not call :meth:`items_for`: that would disarm the row this
        activation is meant to confirm.
        """
        return resolve_action(self._ensure_model(), action)

    def rerender(self) -> tuple[LauncherItem, ...]:
        """Re-render the current rows without changing the query — e.g. after arming."""
        model = self._ensure_model()
        return build_items(model, snapshot_stale=self.snapshot.is_stale)

    def invalidate(self) -> None:
        """Drop the cached model and force the snapshot to refresh. Call after a switch."""
        self._model = None
        self.snapshot.invalidate()
