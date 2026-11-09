"""Tests for the domain models, identity keys, and the confirmation policy.

The confirmation tests are the most important in the suite: they are the only thing standing
between a mislabelled input and a monitor the user cannot recover from software. Each one
names the ADR rule it pins.

Includes the two-identical-panels collision case, which the reference machine cannot produce
(it has one external monitor) and which is therefore built synthetically.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import unittest

from monswitch.core.edid import EdidIdentity
from monswitch.core.models import (
    Confirmation,
    InputSource,
    Monitor,
    Provenance,
    assign_keys,
    identity_key,
    merge_inputs,
    switch_confirmation,
)

HP = EdidIdentity(manufacturer="HPN", product_code=13327, model="HP Z38c", serial="DEMOSERIAL")
NO_SERIAL = EdidIdentity(manufacturer="LGD", product_code=1826, model=None, serial=None)
NO_SERIAL_NAMED = EdidIdentity(
    manufacturer="ACM", product_code=999, model="Cheap 24", serial=None
)


def source(code: int, provenance: Provenance, name: str | None = None) -> InputSource:
    return InputSource(code=code, name=name, provenance=provenance)


def monitor(
    *,
    identity: EdidIdentity = HP,
    bus: int = 16,
    connector: str = "DP-1",
    inputs: tuple[InputSource, ...] = (),
    host_input: int | None = 0x13,
) -> Monitor:
    return Monitor(
        identity=identity, bus=bus, connector=connector, inputs=inputs, host_input=host_input
    )


class ProvenanceTest(unittest.TestCase):
    def test_ordering_is_confirmed_live_reported_guessed(self) -> None:
        self.assertGreater(Provenance.CONFIRMED, Provenance.LIVE)
        self.assertGreater(Provenance.LIVE, Provenance.REPORTED)
        self.assertGreater(Provenance.REPORTED, Provenance.GUESSED)

    def test_only_live_and_confirmed_are_verified(self) -> None:
        self.assertTrue(Provenance.CONFIRMED.is_verified)
        self.assertTrue(Provenance.LIVE.is_verified)
        self.assertFalse(Provenance.REPORTED.is_verified)
        self.assertFalse(Provenance.GUESSED.is_verified)

    def test_max_picks_the_strongest(self) -> None:
        self.assertEqual(
            max(Provenance.GUESSED, Provenance.LIVE, Provenance.REPORTED), Provenance.LIVE
        )


class InputSourceTest(unittest.TestCase):
    def test_hex_code_is_two_digit_lowercase(self) -> None:
        self.assertEqual(source(0x0F, Provenance.LIVE).hex_code, "0x0f")
        self.assertEqual(source(0x13, Provenance.LIVE).hex_code, "0x13")

    def test_label_falls_back_to_the_code_when_unnamed(self) -> None:
        """An unnamed input shows its code, never an invented name (ADR-0009)."""
        self.assertEqual(source(0x1F, Provenance.REPORTED).label, "0x1f")
        self.assertEqual(source(0x0F, Provenance.REPORTED, "DisplayPort-1").label, "DisplayPort-1")

    def test_merge_keeps_stronger_provenance(self) -> None:
        reported = source(0x0F, Provenance.REPORTED, "DisplayPort-1")
        live = source(0x0F, Provenance.LIVE, "DisplayPort-1")

        self.assertEqual(reported.merged_with(live).provenance, Provenance.LIVE)
        self.assertEqual(live.merged_with(reported).provenance, Provenance.LIVE)

    def test_merge_lets_an_unnamed_winner_inherit_a_name(self) -> None:
        """0x13 is seen live but unnamed; if capabilities named it, keep that name."""
        live_unnamed = source(0x13, Provenance.LIVE)
        reported_named = source(0x13, Provenance.REPORTED, "USB-C")

        merged = live_unnamed.merged_with(reported_named)

        self.assertEqual(merged.provenance, Provenance.LIVE)
        self.assertEqual(merged.name, "USB-C")

    def test_merge_prefers_the_stronger_name_when_both_have_one(self) -> None:
        confirmed = source(0x13, Provenance.CONFIRMED, "Work Laptop")
        live = source(0x13, Provenance.LIVE, "USB-C")

        self.assertEqual(live.merged_with(confirmed).name, "Work Laptop")

    def test_merge_rejects_different_codes(self) -> None:
        with self.assertRaises(ValueError):
            source(0x0F, Provenance.LIVE).merged_with(source(0x11, Provenance.LIVE))

    def test_is_immutable(self) -> None:
        with self.assertRaises(Exception):
            source(0x0F, Provenance.LIVE).code = 1  # type: ignore[misc]


class MergeInputsTest(unittest.TestCase):
    def test_collapses_duplicates_and_sorts_by_code(self) -> None:
        merged = merge_inputs(
            [
                source(0x11, Provenance.GUESSED, "HDMI-1"),
                source(0x0F, Provenance.REPORTED, "DisplayPort-1"),
                source(0x0F, Provenance.LIVE, "DisplayPort-1"),
            ]
        )

        self.assertEqual([i.code for i in merged], [0x0F, 0x11])
        self.assertEqual(merged[0].provenance, Provenance.LIVE)

    def test_empty(self) -> None:
        self.assertEqual(merge_inputs([]), ())

    def test_order_of_inputs_does_not_change_the_result(self) -> None:
        a = source(0x13, Provenance.LIVE)
        b = source(0x13, Provenance.REPORTED, "USB-C")

        self.assertEqual(merge_inputs([a, b]), merge_inputs([b, a]))


class IdentityKeyTest(unittest.TestCase):
    def test_serial_is_preferred(self) -> None:
        self.assertEqual(identity_key(HP, "DP-1"), "DEMOSERIAL")

    def test_serial_key_is_independent_of_connector(self) -> None:
        """Moving the cable must not change a serialled monitor's key."""
        self.assertEqual(identity_key(HP, "DP-1"), identity_key(HP, "HDMI-A-1"))

    def test_falls_back_to_mfg_model_connector(self) -> None:
        self.assertEqual(identity_key(NO_SERIAL_NAMED, "DP-2"), "ACM:Cheap 24:DP-2")

    def test_falls_back_to_product_code_without_a_model(self) -> None:
        self.assertEqual(identity_key(NO_SERIAL, "eDP-1"), "LGD:product-1826:eDP-1")

    def test_connector_never_appears_alone(self) -> None:
        """A connector-only key would follow the port, not the panel (ADR-0013)."""
        key = identity_key(NO_SERIAL_NAMED, "DP-2")

        self.assertNotEqual(key, "DP-2")
        self.assertIn("ACM", key)


class AssignKeysTest(unittest.TestCase):
    def test_distinct_monitors_keep_their_overrides(self) -> None:
        keyed = assign_keys([monitor(), monitor(identity=NO_SERIAL_NAMED, connector="DP-2")])

        self.assertTrue(all(k.overrides_allowed for k in keyed))
        self.assertFalse(any(k.collides for k in keyed))

    def test_identical_serial_less_panels_on_different_ports_do_not_collide(self) -> None:
        """The connector component is what saves this common case."""
        keyed = assign_keys(
            [
                monitor(identity=NO_SERIAL_NAMED, connector="DP-1", bus=16),
                monitor(identity=NO_SERIAL_NAMED, connector="DP-2", bus=17),
            ]
        )

        self.assertEqual(len({k.key for k in keyed}), 2)
        self.assertTrue(all(k.overrides_allowed for k in keyed))

    def test_collision_withholds_overrides_from_both(self) -> None:
        """Two panels sharing a key: neither may read stored overrides (ADR-0013)."""
        keyed = assign_keys(
            [
                monitor(identity=NO_SERIAL_NAMED, connector="DP-1", bus=16),
                monitor(identity=NO_SERIAL_NAMED, connector="DP-1", bus=22),
            ]
        )

        self.assertEqual(len(keyed), 2)
        self.assertTrue(all(k.collides for k in keyed))

    def test_collision_keeps_both_monitors_listed(self) -> None:
        """Withholding overrides must not hide the hardware."""
        keyed = assign_keys(
            [
                monitor(identity=NO_SERIAL_NAMED, connector="DP-1", bus=16),
                monitor(identity=NO_SERIAL_NAMED, connector="DP-1", bus=22),
            ]
        )

        self.assertEqual([k.monitor.bus for k in keyed], [16, 22])

    def test_duplicate_serial_withholds_overrides_rather_than_sharing_them(self) -> None:
        """One panel cannot be on two connectors, but a cloned/blank-firmware serial can be.

        If it happens, the keys match and overrides are withheld from both, which is the
        fail-safe direction: better no confirmed map than the wrong panel's confirmed map.
        """
        keyed = assign_keys([monitor(connector="DP-1"), monitor(connector="DP-2")])

        self.assertTrue(all(k.collides for k in keyed))
        self.assertEqual(len({k.key for k in keyed}), 1)

    def test_empty(self) -> None:
        self.assertEqual(assign_keys([]), ())


class SwitchConfirmationTest(unittest.TestCase):
    """The safety spine. Each test names the rule it pins."""

    def test_blocked_without_a_known_host_input(self) -> None:
        """ADR-0013: no live read means no switch. Fail closed."""
        result = switch_confirmation(
            monitor(host_input=None), source(0x0F, Provenance.LIVE, "DisplayPort-1")
        )

        self.assertEqual(result, Confirmation.BLOCKED)
        self.assertFalse(result.requires_prompt)

    def test_blocked_outranks_everything_else(self) -> None:
        """Even a confirmed destination and an opt-out cannot proceed without a host read."""
        result = switch_confirmation(
            monitor(host_input=None),
            source(0x0F, Provenance.CONFIRMED, "DisplayPort-1"),
            confirm_switch=False,
        )

        self.assertEqual(result, Confirmation.BLOCKED)

    def test_leaving_host_requires_confirmation(self) -> None:
        """ADR-0002: switching away from the Host Input is the unrecoverable action."""
        result = switch_confirmation(
            monitor(host_input=0x13), source(0x0F, Provenance.LIVE, "DisplayPort-1")
        )

        self.assertEqual(result, Confirmation.LEAVING_HOST)
        self.assertTrue(result.requires_prompt)

    def test_switching_to_the_current_input_is_a_no_op(self) -> None:
        result = switch_confirmation(
            monitor(host_input=0x13), source(0x13, Provenance.LIVE, "USB-C")
        )

        self.assertEqual(result, Confirmation.NONE)

    def test_opt_out_silences_the_leaving_host_prompt(self) -> None:
        """ADR-0002: confirm_switch = false is a legitimate user choice."""
        result = switch_confirmation(
            monitor(host_input=0x13),
            source(0x0F, Provenance.LIVE, "DisplayPort-1"),
            confirm_switch=False,
        )

        self.assertEqual(result, Confirmation.NONE)

    def test_unverified_destination_always_prompts(self) -> None:
        """ADR-0009: the opt-out does not extend to inferred codes."""
        for provenance in (Provenance.REPORTED, Provenance.GUESSED):
            with self.subTest(provenance=provenance):
                result = switch_confirmation(
                    monitor(host_input=0x13),
                    source(0x1F, provenance),
                    confirm_switch=False,
                )

                self.assertEqual(result, Confirmation.UNVERIFIED_DESTINATION)
                self.assertTrue(result.requires_prompt)

    def test_unverified_outranks_leaving_host(self) -> None:
        """The stronger warning wins, so the user hears the more specific risk."""
        result = switch_confirmation(
            monitor(host_input=0x13), source(0x1F, Provenance.GUESSED)
        )

        self.assertEqual(result, Confirmation.UNVERIFIED_DESTINATION)

    def test_unverified_prompts_even_when_it_is_the_current_input(self) -> None:
        """A guessed code equal to the host value is still not a verified destination."""
        result = switch_confirmation(
            monitor(host_input=0x13), source(0x13, Provenance.GUESSED)
        )

        self.assertEqual(result, Confirmation.UNVERIFIED_DESTINATION)

    def test_confirmed_destination_behaves_like_live(self) -> None:
        result = switch_confirmation(
            monitor(host_input=0x13), source(0x0F, Provenance.CONFIRMED, "Desktop")
        )

        self.assertEqual(result, Confirmation.LEAVING_HOST)

    def test_blocked_is_not_a_prompt(self) -> None:
        self.assertFalse(Confirmation.BLOCKED.requires_prompt)
        self.assertFalse(Confirmation.NONE.requires_prompt)


class MonitorTest(unittest.TestCase):
    def test_is_switchable_requires_a_host_input(self) -> None:
        self.assertTrue(monitor(host_input=0x13).is_switchable)
        self.assertFalse(monitor(host_input=None).is_switchable)

    def test_display_name_uses_the_model(self) -> None:
        self.assertEqual(monitor().display_name, "HP Z38c")

    def test_display_name_falls_back_to_manufacturer_and_connector(self) -> None:
        self.assertEqual(
            monitor(identity=NO_SERIAL, connector="DP-3").display_name, "LGD on DP-3"
        )

    def test_input_for_finds_by_code(self) -> None:
        usbc = source(0x13, Provenance.LIVE, "USB-C")
        subject = monitor(inputs=(source(0x0F, Provenance.REPORTED, "DP-1"), usbc))

        self.assertEqual(subject.input_for(0x13), usbc)
        self.assertIsNone(subject.input_for(0x99))

    def test_host_input_source_resolves(self) -> None:
        usbc = source(0x13, Provenance.LIVE, "USB-C")
        subject = monitor(inputs=(usbc,), host_input=0x13)

        self.assertEqual(subject.host_input_source, usbc)

    def test_host_input_source_is_none_without_a_host_input(self) -> None:
        self.assertIsNone(monitor(inputs=(), host_input=None).host_input_source)


if __name__ == "__main__":
    unittest.main()
