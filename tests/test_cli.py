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

from unittest import mock

from monswitch.cli import EXIT_ERROR, EXIT_OK, main
from monswitch.core.ddc import (
    DetectedDisplay,
    I2cPermissionDenied,
    NoMonitorOnBus,
    WriteCapableDdcutil,
)
from monswitch.gtk_picker import PickerResult

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
        self, *argv: str, ddcutil: FakeDdcutil | None = None, with_config: bool = True
    ) -> tuple[int, str, str]:
        """Run the CLI against the synthetic tree.

        ``with_config`` passes --config pointing at this test's own file. It defaults to true
        so tests exercise the config path, but no test may read the developer's real config:
        the default path is never used.
        """
        prefix = ["--config", str(self.config)] if with_config else []
        out, err = io.StringIO(), io.StringIO()
        code = main(
            [*prefix, *argv],
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


class ConfigOptInTest(CliTestCase):
    """ADR-0014: no config is read unless asked for."""

    def setUp(self) -> None:
        super().setUp()
        self.one_hp()
        self.config.write_text(
            '[monitors."DEMOSERIAL"]\nname = "Should not be applied"\n'
        )

    def test_no_config_flag_reads_nothing(self) -> None:
        _, out, _ = self.run_cli(
            "list", ddcutil=self.reference_ddcutil(), with_config=False
        )

        self.assertIn("HP Z38c", out)
        self.assertNotIn("Should not be applied", out)

    def test_config_flag_applies_the_file(self) -> None:
        _, out, _ = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertIn("Should not be applied", out)

    def test_missing_config_file_is_reported_not_ignored(self) -> None:
        code, _, err = self.run_cli(
            "--config", str(self.dir / "absent.toml"), "list",
            ddcutil=self.reference_ddcutil(),
            with_config=False,
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("does not exist", err)

    def test_use_default_config_flag_exists(self) -> None:
        """A separate flag, so neither it nor --config can swallow the subcommand."""
        from monswitch.cli import build_parser

        options = {
            option
            for action in build_parser()._actions  # noqa: SLF001 - our own parser
            for option in action.option_strings
        }

        self.assertIn("--use-default-config", options)


class SwallowedSubcommandTest(CliTestCase):
    """Regression: an option consuming the subcommand must not open the picker.

    `monswitch --config list` parsed as "config=list, no subcommand", fell through to the
    no-arguments branch and opened a GTK window the user never asked for, in place of the
    listing they did ask for. It blocked in Gtk.main() until killed.
    """

    def setUp(self) -> None:
        super().setUp()
        self.one_hp()

    def test_config_swallowing_list_is_a_usage_error(self) -> None:
        with mock.patch("monswitch.gtk_picker.run_picker") as picker:
            code, _, err = self.run_cli(
                "--config", "list", ddcutil=self.reference_ddcutil(), with_config=False
            )

        picker.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn("read as the value of an option", err)

    def test_monitor_flag_swallowing_set_is_a_usage_error(self) -> None:
        with mock.patch("monswitch.gtk_picker.run_picker") as picker:
            code, _, err = self.run_cli(
                "-m", "set", ddcutil=self.reference_ddcutil(), with_config=False
            )

        picker.assert_not_called()
        self.assertEqual(code, 2)

    def test_the_error_says_how_to_fix_it(self) -> None:
        _, _, err = self.run_cli(
            "--config", "list", ddcutil=self.reference_ddcutil(), with_config=False
        )

        self.assertIn("monswitch list", err)

    def test_a_genuine_no_argument_run_still_opens_the_picker(self) -> None:
        """The guard must not break the actual no-arguments case."""
        with mock.patch(
            "monswitch.gtk_picker.run_picker", return_value=PickerResult(cancelled=True)
        ) as picker:
            code, _, _ = self.run_cli(
                ddcutil=self.reference_ddcutil(), with_config=False
            )

        picker.assert_called_once()
        self.assertEqual(code, EXIT_OK)


class FlagPositionTest(CliTestCase):
    """Shared flags must work on either side of the subcommand.

    Regression: they were defined only on the main parser, so the natural
    `monswitch list -v` failed with "unrecognized arguments: -v". Attaching the same
    flags to the subparsers then risked the opposite bug -- argparse overwriting a value
    set before the subcommand with the subparser's default -- so they use SUPPRESS and are
    filled in after parsing.
    """

    def setUp(self) -> None:
        super().setUp()
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)
        self.one_hp()

    def test_verbose_after_the_subcommand(self) -> None:
        code, _, err = self.run_cli("list", "-v", ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        self.assertIn("internal panel", err)

    def test_verbose_before_the_subcommand(self) -> None:
        code, _, err = self.run_cli("-v", "list", ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        self.assertIn("internal panel", err)

    def test_both_orders_agree(self) -> None:
        _, _, before = self.run_cli("-v", "list", ddcutil=self.reference_ddcutil())
        _, _, after = self.run_cli("list", "-v", ddcutil=self.reference_ddcutil())

        self.assertEqual(before, after)

    def test_verbose_absent_stays_quiet(self) -> None:
        _, _, err = self.run_cli("list", ddcutil=self.reference_ddcutil())

        self.assertNotIn("internal panel", err)

    def test_monitor_filter_after_the_subcommand(self) -> None:
        _, out, _ = self.run_cli("list", "-m", "Z38c", ddcutil=self.reference_ddcutil())

        self.assertIn("HP Z38c", out)

    def test_monitor_filter_before_the_subcommand(self) -> None:
        _, out, _ = self.run_cli("-m", "Z38c", "list", ddcutil=self.reference_ddcutil())

        self.assertIn("HP Z38c", out)

    def test_mixed_with_a_subcommand_only_flag(self) -> None:
        ddcutil = self.reference_ddcutil()

        code, _, err = self.run_cli(
            "list", "--no-capabilities", "-v", ddcutil=ddcutil
        )

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(ddcutil.capability_calls, [])
        self.assertIn("internal panel", err)

    def test_set_accepts_dry_run_either_side_of_its_target(self) -> None:
        code, out, _ = self.run_cli(
            "set", "--dry-run", "usbc", ddcutil=self.reference_ddcutil()
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("0x13", out)


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
    """No arguments opens the picker (ADR-0008).

    `run_picker` is patched throughout: these tests must never put a real window on the
    developer's screen, and a GTK main loop in a test suite would hang it.
    """

    def test_no_monitors_fails_before_opening_a_window(self) -> None:
        """An empty popup explains nothing, so report on the terminal instead."""
        with mock.patch("monswitch.gtk_picker.run_picker") as picker:
            code, _, err = self.run_cli()

        picker.assert_not_called()
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("no external monitor", err)

    def test_opens_the_picker_with_the_discovered_monitors(self) -> None:
        self.one_hp()

        with mock.patch(
            "monswitch.gtk_picker.run_picker", return_value=PickerResult(cancelled=True)
        ) as picker:
            code, _, _ = self.run_cli(ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        picker.assert_called_once()
        monitors = picker.call_args.args[0]
        self.assertEqual(len(monitors), 1)
        self.assertEqual(monitors[0].monitor.display_name, "HP Z38c")

    def test_passes_the_write_capable_wrapper(self) -> None:
        """The picker can switch, so it must be given a wrapper that can write."""
        self.one_hp()

        with mock.patch(
            "monswitch.gtk_picker.run_picker", return_value=PickerResult(cancelled=True)
        ) as picker:
            self.run_cli(ddcutil=self.reference_ddcutil())

        self.assertIsInstance(picker.call_args.kwargs["ddcutil"], WriteCapableDdcutil)

    def test_carries_warnings_into_the_window(self) -> None:
        """There is no terminal in the user's attention once a popup is open (ADR-0011)."""
        self.one_hp()

        with mock.patch(
            "monswitch.gtk_picker.run_picker", return_value=PickerResult(cancelled=True)
        ) as picker:
            self.run_cli(
                ddcutil=FakeDdcutil(live_errors={16: NoMonitorOnBus("no answer")})
            )

        self.assertIsNotNone(picker.call_args.kwargs["message"])

    def test_reports_a_switch(self) -> None:
        self.one_hp()

        with mock.patch(
            "monswitch.gtk_picker.run_picker", return_value=PickerResult(switched=True)
        ):
            code, out, _ = self.run_cli(ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        self.assertIn("switched", out)

    def test_cancelling_is_not_an_error(self) -> None:
        self.one_hp()

        with mock.patch(
            "monswitch.gtk_picker.run_picker", return_value=PickerResult(cancelled=True)
        ):
            code, out, _ = self.run_cli(ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_OK)
        self.assertNotIn("switched", out)

    def test_picker_error_is_reported(self) -> None:
        self.one_hp()

        with mock.patch(
            "monswitch.gtk_picker.run_picker",
            return_value=PickerResult(error="GTK exploded"),
        ):
            code, _, err = self.run_cli(ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("GTK exploded", err)


class WriteSurfaceTest(CliTestCase):
    """`set` can resolve a target, but still cannot change anything."""

    def setUp(self) -> None:
        super().setUp()
        self.one_hp()

    def test_unverified_switch_refuses_without_a_terminal(self) -> None:
        """No tty means nothing can be asked, so the risky switch does not happen."""
        code, _, err = self.run_cli("set", "dp1", ddcutil=self.reference_ddcutil())

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("inferred, not observed", err)
        self.assertIn("refusing", err)

    def test_refusal_names_the_flag_that_would_work(self) -> None:
        """--yes does not cover an unverified input, so suggesting it would loop the user."""
        _, _, err = self.run_cli("set", "dp1", ddcutil=self.reference_ddcutil())

        self.assertIn("--force", err)
        self.assertNotIn("Pass --yes", err)

    def test_yes_does_not_cover_an_unverified_destination(self) -> None:
        """ADR-0009: the opt-out covers daily-use inputs, not guessed codes."""
        code, _, err = self.run_cli(
            "set", "-y", "dp1", ddcutil=self.reference_ddcutil()
        )

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("--force", err)

    def test_dry_run_says_it_changed_nothing(self) -> None:
        code, out, _ = self.run_cli(
            "set", "--dry-run", "dp1", ddcutil=self.reference_ddcutil()
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("nothing was changed", out)

    def test_read_only_wrapper_still_exposes_no_write_method(self) -> None:
        """The default Ddcutil must stay read-only; writing requires asking for the subclass."""
        from monswitch.core.ddc import Ddcutil

        self.assertFalse([name for name in dir(Ddcutil) if "set_input" in name])


if __name__ == "__main__":
    unittest.main()
