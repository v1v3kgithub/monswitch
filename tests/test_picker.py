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


class ActivationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = PickerModel([keyed()])

    def test_active_input_switches_immediately(self) -> None:
        """Choosing the current input is a no-op; nothing to confirm."""
        self.model.select(0)

        activation = self.model.activate()

        self.assertEqual(activation.kind, ActivationKind.SWITCH)
        self.assertEqual(activation.row.source.code, 0x13)

    def test_confirmed_input_arms_first(self) -> None:
        """ADR-0002: leaving this machine needs a deliberate second act."""
        self.model.select(1)

        first = self.model.activate()

        self.assertEqual(first.kind, ActivationKind.ARM)
        self.assertIn("Press Enter again", first.message)
        self.assertEqual(self.model.armed_row, self.model.rows[1])

    def test_second_enter_switches(self) -> None:
        self.model.select(1)
        self.model.activate()

        second = self.model.activate()

        self.assertEqual(second.kind, ActivationKind.SWITCH)
        self.assertIsNone(self.model.armed_row)

    def test_unverified_input_gets_a_specific_warning(self) -> None:
        model = PickerModel([keyed(HP, USBC, JUNK)])
        model.select(1)

        activation = model.activate()

        self.assertEqual(activation.kind, ActivationKind.ARM)
        self.assertIn("unverified", activation.message)
        self.assertIn("go blank", activation.message)

    def test_warnings_lead_with_the_action_not_the_risk(self) -> None:
        """A prompt that opens with the danger reads as a refusal; the user hit exactly that."""
        model = PickerModel([keyed(HP, USBC, DESKTOP, JUNK)])

        for index in (1, 2):
            with self.subTest(index=index):
                model.select(index)
                message = model.activate().message

                self.assertTrue(
                    message.startswith("Press Enter again to switch"), message
                )
                self.assertNotIn("try anyway", message)

    def test_blocked_monitor_never_arms_or_switches(self) -> None:
        model = PickerModel([keyed(HP, USBC, DESKTOP, host_input=None)])
        model.select(1)

        activation = model.activate()

        self.assertEqual(activation.kind, ActivationKind.BLOCKED)
        self.assertIsNone(model.armed_row)
        self.assertIn("could not be read", activation.message)

    def test_blocked_stays_blocked_on_a_second_enter(self) -> None:
        model = PickerModel([keyed(HP, USBC, DESKTOP, host_input=None)])
        model.select(1)

        model.activate()
        again = model.activate()

        self.assertEqual(again.kind, ActivationKind.BLOCKED)

    def test_activating_nothing_is_harmless(self) -> None:
        self.model.set_query("zzzz")

        self.assertEqual(self.model.activate().kind, ActivationKind.NOTHING)

    def test_opt_out_removes_the_arming_step_for_verified_inputs(self) -> None:
        model = PickerModel([keyed()], confirm_switch=False)
        model.select(1)

        self.assertEqual(model.activate().kind, ActivationKind.SWITCH)

    def test_opt_out_does_not_remove_it_for_unverified_inputs(self) -> None:
        """ADR-0009: confirm_switch = false never covers a guessed code."""
        model = PickerModel([keyed(HP, USBC, JUNK)], confirm_switch=False)
        model.select(1)

        self.assertEqual(model.activate().kind, ActivationKind.ARM)


class DisarmingTest(unittest.TestCase):
    """When a pending confirmation must be dropped.

    Each case is a way the user could otherwise press Enter and switch a row they never armed.
    """

    def setUp(self) -> None:
        self.model = PickerModel([keyed()])
        self.model.select(1)
        self.model.activate()
        self.assertIsNotNone(self.model.armed_row)

    def test_moving_disarms(self) -> None:
        self.model.move(1)

        self.assertIsNone(self.model.armed_row)

    def test_moving_back_does_not_restore_arming(self) -> None:
        self.model.move(1)
        self.model.move(-1)

        self.assertIsNone(self.model.armed_row)
        self.assertEqual(self.model.activate().kind, ActivationKind.ARM)

    def test_filtering_disarms(self) -> None:
        self.model.set_query("desktop")

        self.assertIsNone(self.model.armed_row)

    def test_clicking_another_row_disarms(self) -> None:
        self.model.select(2)

        self.assertIsNone(self.model.armed_row)

    def test_explicit_disarm(self) -> None:
        self.model.disarm()

        self.assertIsNone(self.model.armed_row)

    def test_disarmed_row_needs_arming_again(self) -> None:
        self.model.disarm()

        self.assertEqual(self.model.activate().kind, ActivationKind.ARM)

    def test_only_the_armed_row_is_armed(self) -> None:
        armed = self.model.armed_row

        for row in self.model.rows:
            with self.subTest(code=row.source.code):
                self.assertEqual(self.model.is_armed(row), row == armed)

    def test_warning_is_shown_only_for_the_armed_row(self) -> None:
        warnings = [warning for _row, _sel, warning in self.model.rows_with_state()]

        self.assertEqual(len([w for w in warnings if w]), 1)


class RenderingTest(unittest.TestCase):
    def test_rows_with_state_marks_the_selection(self) -> None:
        model = PickerModel([keyed()])
        model.select(1)

        states = model.rows_with_state()

        self.assertEqual([selected for _row, selected, _w in states], [False, True, False])

    def test_confirmation_preference_can_be_changed(self) -> None:
        model = PickerModel([keyed()]).with_confirmation_preference(False)
        model.select(1)

        self.assertEqual(model.activate().kind, ActivationKind.SWITCH)

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
