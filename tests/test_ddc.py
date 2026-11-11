"""Tests for the ``ddcutil`` output parsers.

Fixtures in ``fixtures/ddcutil/`` are verbatim output captured from the reference machine on
2026-11-09 (ddcutil 1.4.1, HP Z38c on bus 16). Everything else is inline synthetic output
covering versions and panels this machine cannot produce.

The two cases that matter most, because the project's design turns on them:

* ``getvcp 60`` returning ``Invalid value (sl=0x13)`` — not an error, ``0x13`` is the real
  current input.
* ``capabilities`` listing ``1f: Unrecognized value`` while omitting ``0x13`` altogether.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from monswitch.core.ddc import (
    INPUT_SOURCE_FEATURE,
    CapabilityInput,
    DdcutilParseError,
    DetectedDisplay,
    parse_capabilities_inputs,
    parse_detect,
    parse_getvcp_input,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ddcutil"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


class GetvcpTest(unittest.TestCase):
    def test_parses_vendor_code_from_verbose_invalid_value(self) -> None:
        """The headline quirk: 'Invalid value' is ddcutil's opinion, 0x13 is the fact."""
        self.assertEqual(parse_getvcp_input(fixture("getvcp60")), 0x13)

    def test_parses_vendor_code_from_terse(self) -> None:
        self.assertEqual(parse_getvcp_input(fixture("getvcp60-terse")), 0x13)

    def test_both_formats_agree(self) -> None:
        self.assertEqual(
            parse_getvcp_input(fixture("getvcp60")),
            parse_getvcp_input(fixture("getvcp60-terse")),
        )

    def test_parses_a_recognised_value(self) -> None:
        text = "VCP code 0x60 (Input Source                  ): DisplayPort-1 (sl=0x0f)"
        self.assertEqual(parse_getvcp_input(text), 0x0F)

    def test_parses_single_digit_hex(self) -> None:
        self.assertEqual(parse_getvcp_input("VCP code 0x60 (Input Source): HDMI (sl=0x3)"), 0x3)

    def test_terse_ignores_other_features(self) -> None:
        """A terse dump of several features must yield feature 60's value, not the first one."""
        text = "VCP 10 C 50 100\nVCP 60 SNC x11\nVCP 12 C 75 100\n"
        self.assertEqual(parse_getvcp_input(text), 0x11)

    def test_raises_when_no_value_present(self) -> None:
        with self.assertRaises(DdcutilParseError):
            parse_getvcp_input("VCP code 0x60 (Input Source): no value\n")

    def test_raises_on_empty_output(self) -> None:
        with self.assertRaises(DdcutilParseError):
            parse_getvcp_input("")

    def test_raises_when_only_other_features_are_terse_listed(self) -> None:
        with self.assertRaises(DdcutilParseError):
            parse_getvcp_input("VCP 10 C 50 100\n")

    def test_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(DdcutilParseError, ValueError))

    def test_feature_constant(self) -> None:
        self.assertEqual(INPUT_SOURCE_FEATURE, 0x60)


class CapabilitiesTest(unittest.TestCase):
    def test_parses_reference_panel_inputs(self) -> None:
        """Exactly what the HP Z38c admits to: two named inputs and one junk entry."""
        inputs = parse_capabilities_inputs(fixture("capabilities"))

        self.assertEqual(
            inputs,
            (
                CapabilityInput(code=0x0F, name="DisplayPort-1"),
                CapabilityInput(code=0x11, name="HDMI-1"),
                CapabilityInput(code=0x1F, name=None),
            ),
        )

    def test_unrecognized_value_is_reported_without_a_name(self) -> None:
        """1f must survive parsing but carry no name -- ADR-0009 forbids inventing one."""
        inputs = parse_capabilities_inputs(fixture("capabilities"))
        junk = [i for i in inputs if i.code == 0x1F]

        self.assertEqual(len(junk), 1)
        self.assertIsNone(junk[0].name)
        self.assertFalse(junk[0].is_named)

    def test_live_input_is_absent_from_capabilities(self) -> None:
        """The fact the whole provenance model exists for: 0x13 is simply not in the list."""
        codes = {i.code for i in parse_capabilities_inputs(fixture("capabilities"))}

        self.assertNotIn(0x13, codes)

    def test_ignores_values_of_other_features(self) -> None:
        text = (
            "VCP Features:\n"
            "   Feature: 52 (Active control)\n"
            "      Values: 00 01 02 03 (interpretation unavailable)\n"
            "   Feature: 60 (Input Source)\n"
            "      Values:\n"
            "         0f: DisplayPort-1\n"
            "   Feature: 86 (Display Scaling)\n"
            "      Values:\n"
            "         02: Max image\n"
        )
        self.assertEqual(
            parse_capabilities_inputs(text),
            (CapabilityInput(code=0x0F, name="DisplayPort-1"),),
        )

    def test_inline_hex_value_list(self) -> None:
        """Some firmware yields a bare list with no names at all."""
        text = (
            "   Feature: 60 (Input Source)\n      Values: 0f 11 13 (interpretation unavailable)\n"
        )
        self.assertEqual(
            parse_capabilities_inputs(text),
            (
                CapabilityInput(code=0x0F, name=None),
                CapabilityInput(code=0x11, name=None),
                CapabilityInput(code=0x13, name=None),
            ),
        )

    def test_no_input_source_feature_is_not_an_error(self) -> None:
        """A monitor that reports no feature 60 is legitimate; discovery falls back."""
        text = "VCP Features:\n   Feature: 10 (Brightness)\n      Values:\n         00: x\n"
        self.assertEqual(parse_capabilities_inputs(text), ())

    def test_empty_output(self) -> None:
        self.assertEqual(parse_capabilities_inputs(""), ())

    def test_uppercase_hex_is_accepted(self) -> None:
        text = "   Feature: 60 (Input Source)\n      Values:\n         0F: DisplayPort-1\n"
        self.assertEqual(parse_capabilities_inputs(text)[0].code, 0x0F)

    def test_value_without_a_name_is_unnamed(self) -> None:
        text = "   Feature: 60 (Input Source)\n      Values:\n         13:\n"
        self.assertEqual(parse_capabilities_inputs(text), (CapabilityInput(code=0x13, name=None),))


class DetectTerseTest(unittest.TestCase):
    def test_parses_both_blocks(self) -> None:
        displays = parse_detect(fixture("detect-terse"))

        self.assertEqual(len(displays), 2)

    def test_invalid_display_is_kept_and_marked(self) -> None:
        """The eDP panel: ddcutil can see it but it has no DDC/CI."""
        edp = parse_detect(fixture("detect-terse"))[0]

        self.assertFalse(edp.usable)
        self.assertIsNone(edp.display_number)
        self.assertEqual(edp.bus, 15)
        self.assertEqual(edp.connector, "card1-eDP-1")
        self.assertEqual(edp.manufacturer, "LGD")
        self.assertIsNone(edp.model)
        self.assertIsNone(edp.serial)

    def test_parses_usable_display(self) -> None:
        hp = parse_detect(fixture("detect-terse"))[1]

        self.assertTrue(hp.usable)
        self.assertEqual(hp.display_number, 1)
        self.assertEqual(hp.bus, 16)
        self.assertEqual(hp.connector, "card1-DP-1")
        self.assertEqual(hp.manufacturer, "HPN")
        self.assertEqual(hp.model, "HP Z38c")
        self.assertEqual(hp.serial, "DEMOSERIAL")

    def test_model_containing_spaces_survives(self) -> None:
        self.assertEqual(parse_detect(fixture("detect-terse"))[1].model, "HP Z38c")


class DetectVerboseTest(unittest.TestCase):
    def test_parses_verbose_edid_synopsis(self) -> None:
        hp = parse_detect(fixture("detect"))[1]

        self.assertTrue(hp.usable)
        self.assertEqual(hp.bus, 16)
        self.assertEqual(hp.connector, "card1-DP-1")
        self.assertEqual(hp.manufacturer, "HPN")
        self.assertEqual(hp.model, "HP Z38c")
        self.assertEqual(hp.serial, "DEMOSERIAL")

    def test_strips_unknown_pnp_suffix(self) -> None:
        """Verbose output writes 'Mfg id: HPN - UNK'; the suffix is ddcutil's, not the EDID's."""
        self.assertEqual(parse_detect(fixture("detect"))[1].manufacturer, "HPN")

    def test_empty_verbose_fields_become_none(self) -> None:
        edp = parse_detect(fixture("detect"))[0]

        self.assertFalse(edp.usable)
        self.assertIsNone(edp.model)
        self.assertIsNone(edp.serial)

    def test_both_formats_agree_on_the_usable_display(self) -> None:
        terse = parse_detect(fixture("detect-terse"))[1]
        verbose = parse_detect(fixture("detect"))[1]

        self.assertEqual(
            (terse.bus, terse.connector, terse.manufacturer, terse.model, terse.serial),
            (
                verbose.bus,
                verbose.connector,
                verbose.manufacturer,
                verbose.model,
                verbose.serial,
            ),
        )

    def test_placeholder_binary_serial_is_not_mistaken_for_the_serial(self) -> None:
        """Verbose output prints both; the string field is the real one (ADR-0013)."""
        self.assertEqual(parse_detect(fixture("detect"))[1].serial, "DEMOSERIAL")


class DetectEdgeCaseTest(unittest.TestCase):
    def test_empty_output(self) -> None:
        self.assertEqual(parse_detect(""), ())

    def test_no_displays_found(self) -> None:
        self.assertEqual(parse_detect("ddcutil: No displays found\n"), ())

    def test_bus_without_dev_prefix(self) -> None:
        text = "Display 1\n   I2C bus:  6\n   Monitor:  ABC:Thing:S1\n"
        self.assertEqual(parse_detect(text)[0].bus, 6)

    def test_block_missing_fields_yields_nones(self) -> None:
        self.assertEqual(
            parse_detect("Display 2\n"),
            (
                DetectedDisplay(
                    bus=None,
                    connector=None,
                    manufacturer=None,
                    model=None,
                    serial=None,
                    display_number=2,
                    usable=True,
                ),
            ),
        )

    def test_several_displays(self) -> None:
        text = (
            "Display 1\n   I2C bus:  /dev/i2c-6\n   Monitor:  AAA:One:S1\n"
            "\n"
            "Display 2\n   I2C bus:  /dev/i2c-7\n   Monitor:  BBB:Two:S2\n"
        )
        displays = parse_detect(text)

        self.assertEqual([d.bus for d in displays], [6, 7])
        self.assertEqual([d.serial for d in displays], ["S1", "S2"])

    def test_monitor_triple_with_missing_trailing_fields(self) -> None:
        text = "Display 1\n   Monitor:  ABC\n"
        display = parse_detect(text)[0]

        self.assertEqual(display.manufacturer, "ABC")
        self.assertIsNone(display.model)
        self.assertIsNone(display.serial)

    def test_results_are_immutable(self) -> None:
        display = parse_detect(fixture("detect-terse"))[0]
        with self.assertRaises(Exception):
            display.bus = 99  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
