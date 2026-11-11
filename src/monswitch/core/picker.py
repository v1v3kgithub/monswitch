"""The picker's logic, with no toolkit attached.

ADR-0008 specifies a Spotlight-style popup: type to filter, arrow keys to move, Enter to
choose. Per ADR-0017 that Enter switches immediately — there is no confirmation step in the
graphical front-ends, and ADR-0018 removed the arming machinery that used to provide one.

All of that is state, not drawing, so it lives here and is tested without a display. The GTK
layer is left holding widgets and key events, which keeps the front-end thin as the
unified-core convention requires, and means the Ulauncher extension reuses the same filtering
rather than reimplementing it.

Presentation is flat, per ADR-0004: every input of every monitor is one row, badged with its
monitor when more than one is connected, so a single query can match both.

Each row still carries its :class:`~monswitch.core.models.Confirmation` verdict. Nothing here
acts on it any more, but it is what the row's "unverified" note is drawn from, and it is what
the CLI — which does still confirm — reads.
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
    """Go ahead and switch."""

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
    def is_empty(self) -> bool:
        return not self._rows

    # -- interaction ------------------------------------------------------

    def set_query(self, query: str) -> None:
        """Filter the rows. Every whitespace-separated term must match somewhere."""
        self._query = query
        terms = query.lower().split()
        if terms:
            self._rows = tuple(
                row for row in self._all_rows if all(term in row.search_text for term in terms)
            )
        else:
            self._rows = tuple(self._all_rows)

        self._selected = 0

    def move(self, delta: int) -> None:
        """Move the selection, clamped to the list."""
        if not self._rows:
            self._selected = 0
            return
        self._selected = max(0, min(len(self._rows) - 1, self._selected + delta))

    def select(self, index: int) -> None:
        """Select by index, e.g. from a mouse click or a launcher resolving a row."""
        if 0 <= index < len(self._rows):
            self._selected = index

    def activate(self) -> Activation:
        """Press Enter on the selected row.

        Switches immediately (ADR-0017). The only refusal left is a monitor whose current
        input could not be read: that is not a confirmation step but an inability to assess
        the consequences at all, so it fails closed (ADR-0013).
        """
        row = self.selected
        if row is None:
            return Activation(kind=ActivationKind.NOTHING)

        if row.is_blocked:
            return Activation(
                kind=ActivationKind.BLOCKED,
                row=row,
                message=(
                    f"{row.monitor.display_name}: cannot switch, because its current input "
                    "could not be read"
                ),
            )

        return Activation(kind=ActivationKind.SWITCH, row=row)

    # -- rendering helpers -------------------------------------------------

    def display_text(self, row: PickerRow) -> str:
        """The row's primary text, including its monitor badge when one is needed."""
        if row.badge:
            return f"[{row.badge}] {row.label}"
        return row.label

    def rows_with_state(self) -> tuple[tuple[PickerRow, bool], ...]:
        """Visible rows paired with whether each is selected."""
        return tuple((row, index == self._selected) for index, row in enumerate(self._rows))

    def without_row(self, row: PickerRow) -> PickerModel:
        """A copy with ``row``'s input dropped. Used after a switch removes the old host."""
        remaining = tuple(r for r in self._all_rows if r != row)
        clone = PickerModel.__new__(PickerModel)
        clone._confirm_switch = self._confirm_switch
        clone._all_rows = remaining
        clone._query = ""
        clone._rows = remaining
        clone._selected = 0
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
        clone.set_query(self._query)
        return clone
