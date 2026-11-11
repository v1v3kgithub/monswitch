"""Tests for the optional config layer.

Everything here is written to a tmpdir; nothing reads the real
``~/.config/monswitch/config.toml``, since a developer's own config must not change whether
the suite passes.

Two behaviours carry most of the weight:

* a **missing** file is a normal outcome, not an error (ADR-0001 is zero-config), and
* a **broken** file must still let ``monswitch list`` run, because that is how the user finds
  the identity key that will fix it (ADR-0011).

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from monswitch.core.config import (
    Config,
    ConfigError,
    MonitorConfig,
    config_path,
    load,
    load_tolerant,
    render,
    render_snippet,
    save,
    with_confirmed_input,
)

REFERENCE_TOML = """
confirm_switch = true

[monitors."DEMOSERIAL"]
name = "HP UltraWide"

[monitors."DEMOSERIAL".inputs]
"0x13" = "Work Laptop"
"0x0f" = "Desktop PC"
"""


class ConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, text: str, name: str = "config.toml") -> Path:
        path = self.dir / name
        path.write_text(text)
        return path


class MissingFileTest(ConfigTestCase):
    def test_missing_file_yields_defaults(self) -> None:
        """Zero-config is the design, so absence is silent (ADR-0001)."""
        config = load(self.dir / "nope.toml")

        self.assertTrue(config.confirm_switch)
        self.assertEqual(dict(config.monitors), {})
        self.assertFalse(config.exists)

    def test_missing_file_produces_no_warnings(self) -> None:
        config, warnings = load_tolerant(self.dir / "nope.toml")

        self.assertEqual(warnings, ())
        self.assertFalse(config.exists)

    def test_defaults_confirm_switching(self) -> None:
        """Safe by default: ADR-0002 confirms unless the user opts out."""
        self.assertTrue(Config().confirm_switch)


class ReferenceSchemaTest(ConfigTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = load(self.write(REFERENCE_TOML))

    def test_reads_confirm_switch(self) -> None:
        self.assertTrue(self.config.confirm_switch)

    def test_reads_monitor_name(self) -> None:
        self.assertEqual(self.config.for_monitor("DEMOSERIAL").name, "HP UltraWide")

    def test_parses_hex_input_codes(self) -> None:
        inputs = self.config.for_monitor("DEMOSERIAL").inputs

        self.assertEqual(dict(inputs), {0x13: "Work Laptop", 0x0F: "Desktop PC"})

    def test_exposes_confirmed_inputs_for_discovery(self) -> None:
        confirmed = self.config.confirmed_inputs()

        self.assertEqual(dict(confirmed["DEMOSERIAL"]), {0x13: "Work Laptop", 0x0F: "Desktop PC"})

    def test_exposes_name_overrides(self) -> None:
        self.assertEqual(dict(self.config.name_overrides()), {"DEMOSERIAL": "HP UltraWide"})

    def test_unknown_monitor_gets_empty_defaults(self) -> None:
        self.assertEqual(self.config.for_monitor("WHO-KNOWS"), MonitorConfig())

    def test_records_that_the_file_exists(self) -> None:
        self.assertTrue(self.config.exists)


class CodeParsingTest(ConfigTestCase):
    def test_accepts_bare_hex(self) -> None:
        """capabilities prints codes bare ('0f:'), so users will copy them that way."""
        config = load(self.write('[monitors.X.inputs]\n"13" = "a"\n"0f" = "b"\n'))

        self.assertEqual(dict(config.for_monitor("X").inputs), {0x13: "a", 0x0F: "b"})

    def test_prefixed_and_bare_are_the_same_code(self) -> None:
        config = load(self.write('[monitors.X.inputs]\n"0x13" = "a"\n'))
        bare = load(self.write('[monitors.Y.inputs]\n"13" = "a"\n', name="b.toml"))

        self.assertEqual(dict(config.for_monitor("X").inputs), dict(bare.for_monitor("Y").inputs))

    def test_case_insensitive(self) -> None:
        config = load(self.write('[monitors.X.inputs]\n"0X1B" = "a"\n'))

        self.assertEqual(dict(config.for_monitor("X").inputs), {0x1B: "a"})

    def test_rejects_out_of_range_code(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load(self.write('[monitors.X.inputs]\n"0x1234" = "a"\n'))

        self.assertIn("not a hex input code", str(caught.exception))

    def test_rejects_non_hex_code(self) -> None:
        with self.assertRaises(ConfigError):
            load(self.write('[monitors.X.inputs]\n"zz" = "a"\n'))

    def test_rejects_empty_code(self) -> None:
        with self.assertRaises(ConfigError):
            load(self.write('[monitors.X.inputs]\n"" = "a"\n'))


class ValidationTest(ConfigTestCase):
    def test_strict_load_rejects_unknown_root_key(self) -> None:
        """Typo protection: confirm_swich would otherwise silently do nothing."""
        with self.assertRaises(ConfigError) as caught:
            load(self.write("confirm_swich = false\n"))

        self.assertIn("confirm_swich", str(caught.exception))

    def test_strict_load_rejects_unknown_monitor_key(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load(self.write('[monitors.X]\nhost_input = "0x13"\n'))

        self.assertIn("host_input", str(caught.exception))

    def test_host_input_is_not_part_of_the_schema(self) -> None:
        """ADR-0013 removed it; a leftover from an older config should be flagged, not used."""
        config, warnings = load_tolerant(self.write('[monitors.X]\nhost_input = "0x13"\n'))

        self.assertTrue(any("host_input" in w for w in warnings))
        self.assertFalse(hasattr(config.for_monitor("X"), "host_input"))

    def test_rejects_non_boolean_confirm_switch(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load(self.write('confirm_switch = "yes"\n'))

        self.assertIn("true or false", str(caught.exception))

    def test_rejects_empty_input_name(self) -> None:
        with self.assertRaises(ConfigError):
            load(self.write('[monitors.X.inputs]\n"0x13" = ""\n'))

    def test_rejects_non_string_input_name(self) -> None:
        with self.assertRaises(ConfigError):
            load(self.write('[monitors.X.inputs]\n"0x13" = 42\n'))

    def test_rejects_invalid_toml(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load(self.write("this is not toml ===\n"))

        self.assertIn("not valid TOML", str(caught.exception))

    def test_confirm_switch_false_is_honoured(self) -> None:
        self.assertFalse(load(self.write("confirm_switch = false\n")).confirm_switch)


class TolerantLoadTest(ConfigTestCase):
    def test_broken_toml_degrades_to_defaults(self) -> None:
        """A broken file must not stop `monswitch list` from running (ADR-0011)."""
        config, warnings = load_tolerant(self.write("nonsense ===\n"))

        self.assertTrue(config.confirm_switch)
        self.assertEqual(dict(config.monitors), {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("ignoring", warnings[0])

    def test_one_bad_code_does_not_discard_the_rest(self) -> None:
        config, warnings = load_tolerant(
            self.write('[monitors.X.inputs]\n"0x13" = "good"\n"zz" = "bad"\n')
        )

        self.assertEqual(dict(config.for_monitor("X").inputs), {0x13: "good"})
        self.assertEqual(len(warnings), 1)

    def test_one_bad_monitor_does_not_discard_another(self) -> None:
        config, warnings = load_tolerant(
            self.write('[monitors.GOOD]\nname = "Fine"\n[monitors.BAD]\nname = 42\n')
        )

        self.assertEqual(config.for_monitor("GOOD").name, "Fine")
        self.assertIsNone(config.for_monitor("BAD").name)
        self.assertTrue(warnings)

    def test_bad_confirm_switch_falls_back_to_safe_default(self) -> None:
        """An unreadable preference must fail safe, i.e. keep confirming."""
        config, warnings = load_tolerant(self.write('confirm_switch = "maybe"\n'))

        self.assertTrue(config.confirm_switch)
        self.assertTrue(warnings)

    def test_monitors_not_a_table(self) -> None:
        config, warnings = load_tolerant(self.write("monitors = 5\n"))

        self.assertEqual(dict(config.monitors), {})
        self.assertTrue(any("must be a table" in w for w in warnings))

    def test_unreadable_file_is_a_warning(self) -> None:
        unreadable = self.write("confirm_switch = false\n", name="locked.toml")
        unreadable.chmod(0o000)
        self.addCleanup(unreadable.chmod, 0o644)

        config, warnings = load_tolerant(unreadable)

        # Running as root defeats the permission bit, so only assert when it took effect.
        if warnings:
            self.assertTrue(config.confirm_switch)
            self.assertIn("could not read", warnings[0])


class ConfigPathTest(unittest.TestCase):
    def test_honours_xdg_config_home(self) -> None:
        import os

        original = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg-test"
        try:
            self.assertEqual(config_path(), Path("/tmp/xdg-test/monswitch/config.toml"))
        finally:
            if original is None:
                del os.environ["XDG_CONFIG_HOME"]
            else:
                os.environ["XDG_CONFIG_HOME"] = original

    def test_falls_back_to_dot_config(self) -> None:
        import os

        original = os.environ.pop("XDG_CONFIG_HOME", None)
        try:
            self.assertEqual(config_path(), Path.home() / ".config/monswitch/config.toml")
        finally:
            if original is not None:
                os.environ["XDG_CONFIG_HOME"] = original


class ImmutabilityTest(ConfigTestCase):
    def test_config_is_frozen(self) -> None:
        config = load(self.write(REFERENCE_TOML))

        with self.assertRaises(Exception):
            config.confirm_switch = False  # type: ignore[misc]

    def test_mappings_are_read_only(self) -> None:
        config = load(self.write(REFERENCE_TOML))

        with self.assertRaises(TypeError):
            config.for_monitor("DEMOSERIAL").inputs[0x99] = "nope"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()


class WriteTest(ConfigTestCase):
    """Rendering and saving. `tomllib` only reads, so this TOML is emitted by hand."""

    def test_round_trips_through_load(self) -> None:
        config = with_confirmed_input(Config(), "DEMOSERIAL", 0x0F, "Desktop PC")
        config = with_confirmed_input(config, "DEMOSERIAL", 0x13, "This Laptop")
        path = self.write(render(config), name="out.toml")

        reloaded = load(path)

        self.assertEqual(
            dict(reloaded.for_monitor("DEMOSERIAL").inputs),
            {0x0F: "Desktop PC", 0x13: "This Laptop"},
        )

    def test_rendering_is_stable(self) -> None:
        config = with_confirmed_input(Config(), "A", 0x11, "TV")
        path = self.write(render(config), name="stable.toml")

        self.assertEqual(render(load(path)), render(config))

    def test_escapes_quotes_and_backslashes(self) -> None:
        """A name is free text, so it can contain anything TOML needs escaped."""
        config = with_confirmed_input(Config(), "K", 0x0F, 'He said "hi" \\ bye')
        path = self.write(render(config), name="esc.toml")

        self.assertEqual(load(path).for_monitor("K").inputs[0x0F], 'He said "hi" \\ bye')

    def test_quotes_keys_containing_colons(self) -> None:
        """The fallback identity key is mfg:model:connector (ADR-0013)."""
        config = with_confirmed_input(Config(), "HPN:HP Z38c:DP-1", 0x11, "TV")
        path = self.write(render(config), name="key.toml")

        self.assertIn("HPN:HP Z38c:DP-1", load(path).monitors)

    def test_preserves_an_existing_name(self) -> None:
        config = Config(monitors=MappingProxyType({"K": MonitorConfig(name="Desk", inputs={})}))

        updated = with_confirmed_input(config, "K", 0x0F, "DP")

        self.assertEqual(updated.for_monitor("K").name, "Desk")
        self.assertEqual(dict(updated.for_monitor("K").inputs), {0x0F: "DP"})

    def test_confirming_twice_overwrites_the_label(self) -> None:
        config = with_confirmed_input(Config(), "K", 0x0F, "Old")

        updated = with_confirmed_input(config, "K", 0x0F, "New")

        self.assertEqual(dict(updated.for_monitor("K").inputs), {0x0F: "New"})

    def test_unnamed_confirmation_falls_back_to_the_code(self) -> None:
        config = with_confirmed_input(Config(), "K", 0x1B)

        self.assertEqual(config.for_monitor("K").inputs[0x1B], "0x1b")

    def test_rejects_an_out_of_range_code(self) -> None:
        for code in (-1, 256):
            with self.subTest(code=code), self.assertRaises(ConfigError):
                with_confirmed_input(Config(), "K", code)

    def test_rejects_an_empty_key(self) -> None:
        with self.assertRaises(ConfigError):
            with_confirmed_input(Config(), "", 0x0F)

    def test_does_not_mutate_the_original(self) -> None:
        original = with_confirmed_input(Config(), "K", 0x0F, "DP")

        with_confirmed_input(original, "K", 0x11, "HDMI")

        self.assertEqual(dict(original.for_monitor("K").inputs), {0x0F: "DP"})

    def test_snippet_is_loadable_on_its_own(self) -> None:
        path = self.write(render_snippet("K", 0x0F, "DP"), name="snip.toml")

        self.assertEqual(load(path).for_monitor("K").inputs[0x0F], "DP")

    def test_save_creates_the_file_and_parent(self) -> None:
        target = self.dir / "nested" / "deep" / "config.toml"

        save(with_confirmed_input(Config(), "K", 0x0F, "DP"), target)

        self.assertTrue(target.exists())
        self.assertEqual(load(target).for_monitor("K").inputs[0x0F], "DP")

    def test_save_keeps_a_backup_of_the_previous_file(self) -> None:
        """Rendering cannot preserve comments, so the old file is not simply replaced."""
        target = self.write("confirm_switch = false  # a comment\n", name="existing.toml")

        backup = save(with_confirmed_input(load(target), "K", 0x0F, "DP"), target)

        self.assertNotEqual(backup, target)
        self.assertIn("# a comment", backup.read_text())
        self.assertNotIn("# a comment", target.read_text())

    def test_save_reports_the_target_when_there_was_nothing_to_back_up(self) -> None:
        target = self.dir / "fresh.toml"

        self.assertEqual(save(Config(), target), target)

    def test_save_preserves_confirm_switch(self) -> None:
        target = self.write("confirm_switch = false\n", name="pref.toml")

        save(load(target), target)

        self.assertFalse(load(target).confirm_switch)
