"""Tests for the ``monswitch`` command-line front-end.

``main`` is driven with an injected fake ``ddcutil``, a synthetic sysfs tree and captured
streams, so nothing here touches hardware or the developer's own config.

Exit codes matter as much as the text: a launcher or script reads them, and ADR-0011 forbids
the silent-success-that-did-nothing failure mode.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monswitch.cli import EXIT_ERROR, EXIT_OK, main
from monswitch.core.ddc import DetectedDisplay, I2cPermissionDenied, NoMonitorOnBus

from test_discovery import REFERENCE_CAPS, FakeDdcutil
from test_sysfs import HP_EDID, LGD_EDID, make_connector


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.root = self.dir / "drm"
        self.root.mkdir()
        self.addCleanup(self._tmp.cleanup)
        # An empty file, so no test ever reads the developer's real config.
        self.config = self.dir / "config.toml"
        self.config.write_text("")

    def one_hp(self, bus: int = 16, connector: str = "card1-DP-1") -> None:
        make_connector(self.root, connector, bus=bus, edid=HP_EDID)

    def run_cli(
        self, *argv: str, ddcutil: FakeDdcutil | None = None
    ) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["--config", str(self.config), *argv],
            ddcutil=ddcutil or FakeDdcutil(),
            drm_root=self.root,
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    def reference_ddcutil(self) -> FakeDdcutil:
        return FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS})


class ListTest(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.one_hp()

    def test_succeeds_and_names_the_monitor(self) -> None:
        code, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        self.assertIn("HP Z38c", out)

    def test_shows_bus_connector_and_config_key(self) -> None:
        """The key is printed because it is what the user must put in their config."""
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertIn("bus 16", out)
        self.assertIn("DP-1", out)
        self.assertIn("DEMOSERIAL", out)

    def test_marks_the_current_input(self) -> None:
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        current = [line for line in out.splitlines() if "0x13" in line]

        self.assertEqual(len(current), 1)
        self.assertIn("*", current[0])
        self.assertIn("current input", current[0])

    def test_marks_unverified_inputs(self) -> None:
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        dp = next(line for line in out.splitlines() if "0x0f" in line)

        self.assertIn("unverified", dp)
        self.assertIn("reported", dp)

    def test_does_not_mark_the_live_input_unverified(self) -> None:
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        current = next(line for line in out.splitlines() if "0x13" in line)

        self.assertNotIn("unverified", current)

    def test_firmware_unnamed_code_shows_its_code_not_a_name(self) -> None:
        """ADR-0009: never show an invented name for a disclaimed value."""
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        junk = next(line for line in out.splitlines() if "0x1f" in line)

        self.assertIn("guessed", junk)
        self.assertNotIn("USB-C", junk)

    def test_explains_the_unverified_marker(self) -> None:
        """A marker nobody understands is not a warning. Say what it means."""
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertIn("inferred, not observed", out)
        self.assertIn("monswitch learn", out)

    def test_header_does_not_repeat_the_model(self) -> None:
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertIn("HP Z38c  (HPN)", out)

    def test_no_capabilities_flag_skips_them(self) -> None:
        ddcutil = self.reference_ddcutil()

        self.run_cli("list", "--no-capabilities", ddcutil=ddcutil)

        self.assertEqual(ddcutil.capability_calls, [])

    def test_capabilities_are_consulted_by_default_for_list(self) -> None:
        """`list` is a deliberate command, so paying a few seconds for a fuller list is fine."""
        ddcutil = self.reference_ddcutil()

        self.run_cli("list", ddcutil=ddcutil)

        self.assertEqual(ddcutil.capability_calls, [16])


class ConfigIntegrationTest(CliTestCase):
    def test_configured_name_and_confirmed_inputs_are_applied(self) -> None:
        self.one_hp()
        self.config.write_text(
            '[monitors."DEMOSERIAL"]\n'
            'name = "Desk Ultrawide"\n'
            '[monitors."DEMOSERIAL".inputs]\n'
            '"0x0f" = "Desktop PC"\n'
        )

        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertIn("Desk Ultrawide", out)
        dp = next(line for line in out.splitlines() if "0x0f" in line)
        self.assertIn("Desktop PC", dp)
        self.assertIn("confirmed", dp)
        self.assertNotIn("unverified", dp)

    def test_broken_config_warns_but_still_lists(self) -> None:
        """The user may need this listing to find the key that fixes their config."""
        self.one_hp()
        self.config.write_text("this is not toml ===\n")

        code, out, err = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        self.assertIn("HP Z38c", out)
        self.assertIn("config:", err)


class NoMonitorTest(CliTestCase):
    def test_empty_result_is_an_error_with_advice(self) -> None:
        code, _, err = self.run_cli("list")

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("no external monitor", err)
        self.assertIn("on-screen menu", err)
        self.assertIn("i2c-dev", err)

    def test_mentions_skipped_connectors_and_how_to_see_them(self) -> None:
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)

        _, _, err = self.run_cli("list")

        self.assertIn("skipped", err)
        self.assertIn("-v", err)

    def test_verbose_lists_skip_reasons(self) -> None:
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)

        _, _, err = self.run_cli("-v", "list")

        self.assertIn("internal panel", err)

    def test_explains_that_internal_panels_are_excluded(self) -> None:
        """Otherwise a laptop user reasonably concludes the tool is broken."""
        _, _, err = self.run_cli("list")

        self.assertIn("internal panels", err.lower())


class FailedReadTest(CliTestCase):
    def test_unreadable_monitor_is_listed_as_unswitchable(self) -> None:
        self.one_hp()

        code, out, err = self.run_cli(
            "list",
            ddcutil=FakeDdcutil(live_errors={16: NoMonitorOnBus("nothing answered")}),
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("HP Z38c", out)
        self.assertIn("switching unavailable", out)
        self.assertIn("switching is disabled", err)

    def test_permission_error_surfaces_the_fix(self) -> None:
        self.one_hp()

        _, _, err = self.run_cli(
            "list", ddcutil=FakeDdcutil(live_errors={16: I2cPermissionDenied("denied")})
        )

        self.assertIn("denied", err)


class SelectionTest(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.one_hp(bus=16, connector="card1-DP-1")
        make_connector(self.root, "card1-DP-2", bus=17, edid=LGD_EDID)
        self.ddcutil = FakeDdcutil(live={16: 0x13, 17: 0x0F})

    def test_lists_every_monitor_by_default(self) -> None:
        _, out, _ = self.run_cli("list", ddcutil=self.ddcutil)

        self.assertIn("HP Z38c", out)
        self.assertIn("LGD", out)

    def test_filters_by_model(self) -> None:
        _, out, _ = self.run_cli("-m", "Z38c", "list", ddcutil=self.ddcutil)

        self.assertIn("HP Z38c", out)
        self.assertNotIn("LGD", out)

    def test_filters_by_serial(self) -> None:
        _, out, _ = self.run_cli("-m", "DEMOSERIAL", "list", ddcutil=self.ddcutil)

        self.assertIn("HP Z38c", out)

    def test_filter_is_case_insensitive(self) -> None:
        _, out, _ = self.run_cli("-m", "z38C", "list", ddcutil=self.ddcutil)

        self.assertIn("HP Z38c", out)

    def test_unmatched_filter_is_an_error(self) -> None:
        code, _, err = self.run_cli("-m", "nonexistent", "list", ddcutil=self.ddcutil)

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("no monitor matches", err)


class DetectFallbackTest(CliTestCase):
    def test_says_when_it_fell_back_to_detect(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=None, edid=HP_EDID)
        ddcutil = FakeDdcutil(
            live={16: 0x13},
            detect_result=[
                DetectedDisplay(
                    bus=16,
                    connector="card1-DP-1",
                    manufacturer="HPN",
                    model="HP Z38c",
                    serial="DEMOSERIAL",
                    display_number=1,
                    usable=True,
                )
            ],
        )

        code, out, _ = self.run_cli("list", ddcutil=ddcutil)

        self.assertEqual(code, EXIT_OK)
        self.assertIn("ddcutil detect", out)


class NoArgumentsTest(CliTestCase):
    def test_reports_that_the_picker_is_not_built_yet(self) -> None:
        """ADR-0008 reserves no-args for the picker; until then, be explicit."""
        code, out, _ = self.run_cli()

        self.assertEqual(code, EXIT_OK)
        self.assertIn("not implemented yet", out)
        self.assertIn("monswitch list", out)

    def test_shows_usage(self) -> None:
        _, out, _ = self.run_cli()

        self.assertIn("usage:", out)


class WriteSurfaceTest(unittest.TestCase):
    def test_cli_offers_no_set_command_yet(self) -> None:
        """The write path arrives with its confirmation flow, not before."""
        from monswitch.cli import build_parser

        subcommands = {
            choice
            for action in build_parser()._actions  # noqa: SLF001 - our own parser
            if action.choices
            for choice in action.choices
        }

        self.assertEqual(subcommands, {"list"})

    def test_set_is_rejected_as_an_unknown_command(self) -> None:
        from monswitch.cli import main

        with self.assertRaises(SystemExit) as caught:
            main(["set", "dp1"], out=io.StringIO(), err=io.StringIO())

        self.assertNotEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
