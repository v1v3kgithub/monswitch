"""The picker's logic, with no toolkit attached.

ADR-0008 specifies a Spotlight-style popup: type to filter, arrow keys to move, Enter to
choose, and — when the choice would switch the monitor away from this machine — a second Enter
to confirm, with the row itself turning into the warning.

All of that is state, not drawing, so it lives here and is tested without a display. The GTK
layer is left holding widgets and key events, which keeps the front-end thin as the unified-core
convention requires, and means the Ulauncher extension can reuse the same filtering and the
same confirmation behaviour rather than reimplementing them.

Presentation is flat, per ADR-0004: every input of every monitor is one row, badged with its
monitor when more than one is connected, so a single query can match both.

The arming mechanic is the picker's form of confirmation. It exists because a popup cannot show
a modal dialog without ceasing to be a popup, and because ADR-0002 and ADR-0009 both require a
deliberate second act before an irreversible switch. Arming is per row and is dropped the
moment anything else happens — a keystroke, a filter change, a move — so a stale "press Enter
again" can never be satisfied by an Enter the user meant for something else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum, unique

from .models import Confirmation, InputSource, KeyedMonitor, Monitor, switch_confirmation

__all__ = [
    "Activation",
    "ActivationKind",
    "PickerModel",
    "PickerRow",
]


@dataclass(frozen=True, slots=True)
class PickerRow:
    """One selectable line: an input on a monitor."""

    monitor: Monitor
    source: InputSource
    badge: str | None
    confirmation: Confirmation

    @property
    def is_active(self) -> bool:
        """Whether this is the input the monitor is currently showing from this machine."""
        return self.source.code == self.monitor.host_input

    @property
    def is_verified(self) -> bool:
        return self.source.is_verified

    @property
    def label(self) -> str:
        return self.source.label

    @property
    def needs_confirmation(self) -> bool:
        return self.confirmation.requires_prompt

    @property
    def is_blocked(self) -> bool:
        return self.confirmation is Confirmation.BLOCKED

    @property
    def search_text(self) -> str:
        """Everything a query may match: badge, name, code, and the monitor's own names."""
        identity = self.monitor.identity
        parts = [
            self.badge or "",
            self.source.name or "",
            self.source.hex_code,
            self.monitor.display_name,
            identity.model or "",
            identity.manufacturer,
        ]
        return " ".join(parts).lower()

    def status_note(self) -> str | None:
        """The short trailing note shown on the row."""
        if self.is_active:
            return "current"
        if self.is_blocked:
            return "unavailable"
        if not self.is_verified:
            return "unverified"
        return None


@unique
class ActivationKind(Enum):
    """What pressing Enter on a row means."""

    SWITCH = "switch"
    """Go ahead: either no confirmation was needed, or it has now been given."""

    ARM = "arm"
    """Confirmation needed. The row shows a warning and waits for a second Enter."""

    BLOCKED = "blocked"
    """Refused. The Host Input is unknown, so the risk cannot be assessed (ADR-0013)."""

    NOTHING = "nothing"
    """No row to act on, e.g. a filter that matches nothing."""


@dataclass(frozen=True, slots=True)
class Activation:
    """The outcome of pressing Enter."""

    kind: ActivationKind
    row: PickerRow | None = None
    message: str | None = None

    @property
    def should_switch(self) -> bool:
        return self.kind is ActivationKind.SWITCH


def _warning_for(row: PickerRow) -> str:
    """The text the armed row shows. Names the specific risk, not a generic 'are you sure'."""
    if row.confirmation is Confirmation.UNVERIFIED_DESTINATION:
        return "Unverified input — may not exist. Press Enter again to try anyway"
    return "Press Enter again to switch away from this machine"


class PickerModel:
    """Filtering, selection and confirmation state for the popup picker."""

    def __init__(
        self,
        monitors: Sequence[KeyedMonitor],
        *,
        confirm_switch: bool = True,
    ) -> None:
        self._confirm_switch = confirm_switch
        self._all_rows = self._build_rows(monitors)
        self._query = ""
        self._rows = tuple(self._all_rows)
        self._selected = 0
        self._armed: PickerRow | None = None

    def _build_rows(self, monitors: Sequence[KeyedMonitor]) -> tuple[PickerRow, ...]:
        # Badge only when it carries information: with one monitor it is noise on every row.
        badged = len(monitors) > 1
        rows: list[PickerRow] = []
        for keyed in monitors:
            monitor = keyed.monitor
            for source in monitor.inputs:
                rows.append(
                    PickerRow(
                        monitor=monitor,
                        source=source,
                        badge=monitor.display_name if badged else None,
                        confirmation=switch_confirmation(
                            monitor, source, confirm_switch=self._confirm_switch
                        ),
                    )
                )
        return tuple(rows)

    # -- state ------------------------------------------------------------

    @property
    def query(self) -> str:
        return self._query

    @property
    def rows(self) -> tuple[PickerRow, ...]:
        """The rows currently visible, in order."""
        return self._rows

    @property
    def selected_index(self) -> int:
        return self._selected

    @property
    def selected(self) -> PickerRow | None:
        if not self._rows or not 0 <= self._selected < len(self._rows):
            return None
        return self._rows[self._selected]

    @property
    def armed_row(self) -> PickerRow | None:
        """The row awaiting a second Enter, if any."""
        return self._armed

    def is_armed(self, row: PickerRow) -> bool:
        return self._armed is not None and self._armed == row

    def warning_for(self, row: PickerRow) -> str | None:
        """The warning to display in place of ``row``'s normal text, if it is armed."""
        return _warning_for(row) if self.is_armed(row) else None

    @property
    def is_empty(self) -> bool:
        return not self._rows

    # -- interaction ------------------------------------------------------

    def set_query(self, query: str) -> None:
        """Filter the rows. Every whitespace-separated term must match somewhere.

        Changing the filter disarms: the row under the cursor may now be a different one, and
        an armed confirmation must never carry over to a row the user did not arm.
        """
        self._query = query
        terms = query.lower().split()
        if terms:
            self._rows = tuple(
                row
                for row in self._all_rows
                if all(term in row.search_text for term in terms)
            )
        else:
            self._rows = tuple(self._all_rows)

        self._selected = 0
        self._armed = None

    def move(self, delta: int) -> None:
        """Move the selection, clamped to the list. Disarms, for the same reason as filtering."""
        if not self._rows:
            self._selected = 0
            self._armed = None
            return
        self._selected = max(0, min(len(self._rows) - 1, self._selected + delta))
        self._armed = None

    def select(self, index: int) -> None:
        """Select by index, e.g. from a mouse click. Disarms."""
        if 0 <= index < len(self._rows):
            self._selected = index
            self._armed = None

    def disarm(self) -> None:
        """Drop any pending confirmation."""
        self._armed = None

    def activate(self) -> Activation:
        """Press Enter on the selected row.

        First press on a row that needs confirmation arms it and returns
        :attr:`ActivationKind.ARM`; the second press on the *same* row returns
        :attr:`ActivationKind.SWITCH`. A row needing no confirmation switches immediately.
        """
        row = self.selected
        if row is None:
            return Activation(kind=ActivationKind.NOTHING)

        if row.is_blocked:
            self._armed = None
            return Activation(
                kind=ActivationKind.BLOCKED,
                row=row,
                message=(
                    f"{row.monitor.display_name}: cannot switch, because its current input "
                    "could not be read"
                ),
            )

        if not row.needs_confirmation:
            self._armed = None
            return Activation(kind=ActivationKind.SWITCH, row=row)

        if self.is_armed(row):
            self._armed = None
            return Activation(kind=ActivationKind.SWITCH, row=row)

        self._armed = row
        return Activation(kind=ActivationKind.ARM, row=row, message=_warning_for(row))

    # -- rendering helpers -------------------------------------------------

    def display_text(self, row: PickerRow) -> str:
        """The row's primary text, including its monitor badge when one is needed."""
        if row.badge:
            return f"[{row.badge}] {row.label}"
        return row.label

    def rows_with_state(self) -> tuple[tuple[PickerRow, bool, str | None], ...]:
        """Visible rows paired with whether each is selected, and its armed warning."""
        return tuple(
            (row, index == self._selected, self.warning_for(row))
            for index, row in enumerate(self._rows)
        )

    def without_row(self, row: PickerRow) -> PickerModel:
        """A copy with ``row``'s input dropped. Used after a switch removes the old host."""
        remaining = tuple(r for r in self._all_rows if r != row)
        clone = PickerModel.__new__(PickerModel)
        clone._confirm_switch = self._confirm_switch
        clone._all_rows = remaining
        clone._query = ""
        clone._rows = remaining
        clone._selected = 0
        clone._armed = None
        return clone

    def with_confirmation_preference(self, confirm_switch: bool) -> PickerModel:
        """A copy whose rows are re-evaluated against a different preference."""
        clone = PickerModel.__new__(PickerModel)
        clone._confirm_switch = confirm_switch
        clone._all_rows = tuple(
            replace(
                row,
                confirmation=switch_confirmation(
                    row.monitor, row.source, confirm_switch=confirm_switch
                ),
            )
            for row in self._all_rows
        )
        clone._query = self._query
        clone._selected = 0
        clone._armed = None
        clone.set_query(self._query)
        return clone
