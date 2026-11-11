"""Tests for the picker's logic, with no GTK involved.

The arming state machine carries the weight. In a popup there is no modal dialog, so the
second Enter *is* the confirmation for an irreversible switch — which means a stale armed row
is a way to switch a monitor the user did not mean to switch. Most of these tests exist to pin
down when arming is dropped.

No test here constructs a GTK widget or opens a window.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import unittest

from monswitch.core.edid import EdidIdentity
from monswitch.core.models import (
    Confirmation,
    InputSource,
    KeyedMonitor,
    Monitor,
    Provenance,
)
from monswitch.core.picker import ActivationKind, PickerModel

HP = EdidIdentity(manufacturer="HPN", product_code=13327, model="HP Z38c", serial="DEMOSERIAL")
DELL = EdidIdentity(manufacturer="DEL", product_code=111, model="Dell U24", serial="DELL1")


def source(
    code: int, name: str | None, provenance: Provenance = Provenance.REPORTED
) -> InputSource:
    return InputSource(code=code, name=name, provenance=provenance)


USBC = source(0x13, "USB-C", Provenance.LIVE)
DESKTOP = source(0x0F, "Desktop PC", Provenance.CONFIRMED)
HDMI = source(0x11, "HDMI-1", Provenance.REPORTED)
JUNK = source(0x1F, None, Provenance.GUESSED)


def keyed(
    identity: EdidIdentity = HP,
    *inputs: InputSource,
    bus: int = 16,
    connector: str = "DP-1",
    host_input: int | None = 0x13,
    key: str = "DEMOSERIAL",
) -> KeyedMonitor:
    monitor = Monitor(
        identity=identity,
        bus=bus,
        connector=connector,
        inputs=inputs or (USBC, DESKTOP, HDMI),
        host_input=host_input,
    )
    return KeyedMonitor(monitor=monitor, key=key)


class RowBuildingTest(unittest.TestCase):
    def test_one_row_per_input(self) -> None:
        model = PickerModel([keyed()])

        self.assertEqual(len(model.rows), 3)

    def test_no_badge_with_a_single_monitor(self) -> None:
        """A badge on every row of a one-monitor list is noise."""
        model = PickerModel([keyed()])

        self.assertTrue(all(row.badge is None for row in model.rows))
        self.assertEqual(model.display_text(model.rows[0]), "USB-C")

    def test_badges_appear_with_several_monitors(self) -> None:
        """ADR-0004's flat presentation: one list, each row naming its monitor."""
        model = PickerModel(
            [keyed(), keyed(DELL, USBC, DESKTOP, bus=17, connector="DP-2", key="DELL1")]
        )

        self.assertEqual(len(model.rows), 5)
        self.assertTrue(all(row.badge for row in model.rows))
        self.assertTrue(model.display_text(model.rows[0]).startswith("[HP Z38c]"))

    def test_active_row_is_marked(self) -> None:
        model = PickerModel([keyed()])

        active = [row for row in model.rows if row.is_active]

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].source.code, 0x13)
        self.assertEqual(active[0].status_note(), "current")

    def test_unverified_rows_are_noted(self) -> None:
        model = PickerModel([keyed()])

        hdmi = next(row for row in model.rows if row.source.code == 0x11)

        self.assertEqual(hdmi.status_note(), "unverified")

    def test_verified_non_active_row_has_no_note(self) -> None:
        model = PickerModel([keyed()])

        desktop = next(row for row in model.rows if row.source.code == 0x0F)

        self.assertIsNone(desktop.status_note())

    def test_unreadable_monitor_rows_are_blocked(self) -> None:
        model = PickerModel([keyed(HP, USBC, DESKTOP, host_input=None)])

        self.assertTrue(all(row.is_blocked for row in model.rows))
        self.assertEqual(model.rows[0].status_note(), "unavailable")


class FilteringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = PickerModel(
            [keyed(), keyed(DELL, USBC, HDMI, bus=17, connector="DP-2", key="DELL1")]
        )

    def test_empty_query_shows_everything(self) -> None:
        self.model.set_query("")

        self.assertEqual(len(self.model.rows), 5)

    def test_filters_by_input_name(self) -> None:
        self.model.set_query("hdmi")

        self.assertTrue(all(row.source.code == 0x11 for row in self.model.rows))

    def test_filters_by_monitor_name(self) -> None:
        self.model.set_query("dell")

        self.assertEqual(len(self.model.rows), 2)
        self.assertTrue(all(row.monitor.identity.serial == "DELL1" for row in self.model.rows))

    def test_single_query_matches_monitor_and_input_together(self) -> None:
        """ADR-0004's reason for a flat list: one query narrows both axes."""
        self.model.set_query("dell hdmi")

        self.assertEqual(len(self.model.rows), 1)
        self.assertEqual(self.model.rows[0].source.code, 0x11)
        self.assertEqual(self.model.rows[0].monitor.identity.serial, "DELL1")

    def test_filters_by_hex_code(self) -> None:
        self.model.set_query("0x1f")

        self.assertEqual(self.model.rows, ())

    def test_unnamed_input_is_findable_by_code(self) -> None:
        model = PickerModel([keyed(HP, USBC, JUNK)])
        model.set_query("0x1f")

        self.assertEqual(len(model.rows), 1)

    def test_filter_is_case_insensitive(self) -> None:
        self.model.set_query("HDMI")

        self.assertTrue(self.model.rows)

    def test_no_match_is_empty_not_an_error(self) -> None:
        self.model.set_query("zzzz")

        self.assertTrue(self.model.is_empty)
        self.assertIsNone(self.model.selected)

    def test_filtering_resets_the_selection(self) -> None:
        self.model.move(3)
        self.model.set_query("hdmi")

        self.assertEqual(self.model.selected_index, 0)


class SelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = PickerModel([keyed()])

    def test_starts_on_the_first_row(self) -> None:
        self.assertEqual(self.model.selected_index, 0)

    def test_moves_down_and_up(self) -> None:
        self.model.move(1)
        self.assertEqual(self.model.selected_index, 1)
        self.model.move(-1)
        self.assertEqual(self.model.selected_index, 0)

    def test_clamps_at_the_ends(self) -> None:
        self.model.move(-5)
        self.assertEqual(self.model.selected_index, 0)
        self.model.move(99)
        self.assertEqual(self.model.selected_index, len(self.model.rows) - 1)

    def test_select_by_index(self) -> None:
        self.model.select(2)

        self.assertEqual(self.model.selected_index, 2)

    def test_select_ignores_out_of_range(self) -> None:
        self.model.select(99)

        self.assertEqual(self.model.selected_index, 0)

    def test_move_on_an_empty_list_is_safe(self) -> None:
        self.model.set_query("nothing")
        self.model.move(1)

        self.assertEqual(self.model.selected_index, 0)


class RenderingTest(unittest.TestCase):
    def test_row_confirmation_matches_the_policy(self) -> None:
        model = PickerModel([keyed()])

        by_code = {row.source.code: row.confirmation for row in model.rows}

        self.assertEqual(by_code[0x13], Confirmation.NONE)
        self.assertEqual(by_code[0x0F], Confirmation.LEAVING_HOST)
        self.assertEqual(by_code[0x11], Confirmation.UNVERIFIED_DESTINATION)


class EmptyTest(unittest.TestCase):
    def test_no_monitors(self) -> None:
        model = PickerModel([])

        self.assertTrue(model.is_empty)
        self.assertIsNone(model.selected)
        self.assertEqual(model.activate().kind, ActivationKind.NOTHING)


if __name__ == "__main__":
    unittest.main()


class ActivationTest(unittest.TestCase):
    """Enter switches (ADR-0017), and the arming that used to precede it is gone (ADR-0018)."""

    def setUp(self) -> None:
        self.model = PickerModel([keyed()])

    def test_switches_on_the_first_activation(self) -> None:
        self.model.select(1)

        activation = self.model.activate()

        self.assertEqual(activation.kind, ActivationKind.SWITCH)
        self.assertEqual(activation.row.source.code, 0x0F)

    def test_every_row_switches_regardless_of_provenance(self) -> None:
        """A confirmed, a live and an unverified input all behave the same way now."""
        subject = PickerModel([keyed(HP, USBC, DESKTOP, HDMI, JUNK)])

        for index in range(len(subject.rows)):
            with self.subTest(code=subject.rows[index].source.hex_code):
                subject.select(index)
                self.assertEqual(subject.activate().kind, ActivationKind.SWITCH)

    def test_repeated_activation_keeps_switching(self) -> None:
        """No state accumulates between presses, so the second is not a different answer."""
        self.model.select(1)

        self.assertEqual(self.model.activate().kind, ActivationKind.SWITCH)
        self.assertEqual(self.model.activate().kind, ActivationKind.SWITCH)

    def test_an_unreadable_monitor_is_still_refused(self) -> None:
        """Fail-closed is not a confirmation step, so removing confirmation kept it."""
        subject = PickerModel([keyed(HP, USBC, DESKTOP, host_input=None)])
        subject.select(1)

        activation = subject.activate()

        self.assertEqual(activation.kind, ActivationKind.BLOCKED)
        self.assertIn("could not be read", activation.message)

    def test_blocked_stays_blocked_when_pressed_again(self) -> None:
        subject = PickerModel([keyed(HP, USBC, DESKTOP, host_input=None)])
        subject.select(1)

        subject.activate()

        self.assertEqual(subject.activate().kind, ActivationKind.BLOCKED)

    def test_activating_nothing_is_harmless(self) -> None:
        self.model.set_query("zzzz")

        self.assertEqual(self.model.activate().kind, ActivationKind.NOTHING)

    def test_no_arm_kind_exists(self) -> None:
        """ADR-0018: the kind itself is gone, so no caller can branch on it."""
        self.assertEqual({kind.value for kind in ActivationKind}, {"switch", "blocked", "nothing"})

    def test_the_verdict_is_still_computed(self) -> None:
        """The policy still knows the switch is irreversible; the picker just does not ask.

        It is what the row's note is drawn from, and what the CLI still acts on.
        """
        by_code = {row.source.code: row.confirmation for row in self.model.rows}

        self.assertEqual(by_code[0x13], Confirmation.NONE)
        self.assertEqual(by_code[0x0F], Confirmation.LEAVING_HOST)
        self.assertEqual(by_code[0x11], Confirmation.UNVERIFIED_DESTINATION)

    def test_provenance_is_still_reported(self) -> None:
        notes = [row.status_note() for row in self.model.rows]

        self.assertIn("current", notes)
        self.assertIn("unverified", notes)


class RowStateTest(unittest.TestCase):
    def test_rows_with_state_marks_the_selection(self) -> None:
        subject = PickerModel([keyed()])
        subject.select(1)

        states = subject.rows_with_state()

        self.assertEqual([selected for _row, selected in states], [False, True, False])

    def test_rows_with_state_is_a_pair_per_row(self) -> None:
        """The third element was the armed warning and no longer exists."""
        subject = PickerModel([keyed()])

        self.assertTrue(all(len(entry) == 2 for entry in subject.rows_with_state()))

    def test_confirmation_preference_still_changes_the_verdicts(self) -> None:
        subject = PickerModel([keyed()]).with_confirmation_preference(False)

        self.assertEqual(subject.rows[1].confirmation, Confirmation.NONE)
