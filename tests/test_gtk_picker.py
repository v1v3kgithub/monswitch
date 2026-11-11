"""Tests for the GTK layer's wiring — not its drawing.

**No test here opens a window or starts a main loop.** ``Gtk.main()`` blocks, which would hang
the suite, and the window would appear on whoever is running the tests.

What is worth testing at this level is the part that broke in practice: typelib version
pinning. Ubuntu ships GTK 3 and GTK 4 side by side, so an unversioned ``from gi.repository
import Gdk`` loads Gdk 4.0, and Gtk 3.0's own requirement for Gdk 3.0 then fails with
"Requiring namespace 'Gdk' version '3.0', but '4.0' is already loaded". Pinning only ``Gtk``
is not enough, and nothing in the pure-logic tests can catch it because they never import
``gi``.

The version check runs in a subprocess, because namespace loading is global and permanent
within a process: once some other test has pulled in a version, an in-process assertion would
be measuring that instead.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import monswitch.gtk_picker as gtk_picker

NEWLINE = chr(10)
"""Built at runtime so these test sources carry no escape sequences."""

SOURCE = Path(gtk_picker.__file__)
SRC_ROOT = SOURCE.parent.parent


def _subprocess_check(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(SRC_ROOT.parent),
        env={"PYTHONPATH": str(SRC_ROOT), "PATH": "/usr/bin:/bin"},
        check=False,
        timeout=60,
    )


class VersionPinningTest(unittest.TestCase):
    """Regression tests for the Gdk 3.0 / 4.0 clash."""

    def test_source_pins_both_namespaces(self) -> None:
        """A textual check, so it holds even where gi is not installed at all."""
        text = SOURCE.read_text()

        self.assertIn('gi.require_version("Gtk", "3.0")', text)
        self.assertIn('gi.require_version("Gdk", "3.0")', text)

    def test_pins_come_before_any_repository_import(self) -> None:
        """Parsed, not grepped: the comments explaining this bug mention the import too."""
        import ast

        tree = ast.parse(SOURCE.read_text())

        pins = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "require_version"
        ]
        imports = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("gi.repository")
        ]

        self.assertTrue(pins, "no gi.require_version call found")
        self.assertTrue(imports, "no gi.repository import found")
        self.assertLess(max(pins), min(imports))

    def test_both_namespaces_are_pinned_in_code(self) -> None:
        import ast

        tree = ast.parse(SOURCE.read_text())
        pinned = {
            node.args[0].value: node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "require_version"
            and len(node.args) == 2
            and all(isinstance(arg, ast.Constant) for arg in node.args)
        }

        self.assertEqual(pinned, {"Gtk": "3.0", "Gdk": "3.0"})

    @unittest.skipUnless(gtk_picker.GTK_AVAILABLE, "PyGObject/GTK not available")
    def test_importing_the_module_then_gdk_yields_gtk3(self) -> None:
        """The exact failure the user hit: `monswitch` died before the window appeared."""
        result = _subprocess_check(
            "import monswitch.gtk_picker\n"
            "from gi.repository import Gtk, Gdk\n"
            "print(Gtk._version, Gdk._version)\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3.0 3.0")

    @unittest.skipUnless(gtk_picker.GTK_AVAILABLE, "PyGObject/GTK not available")
    def test_no_pygi_version_warning(self) -> None:
        """The warning preceded the crash and is the signal that a pin is missing.

        Asserts on the specific warning rather than turning all warnings into errors. An
        earlier version used ``simplefilter('error')``, which passed locally and failed in CI
        because PyGObject emits unrelated deprecation warnings there -- promoting those to
        errors made the import fail and said nothing about version pinning.
        """
        code = NEWLINE.join(
            [
                "import warnings",
                "warnings.simplefilter('always')",
                "import monswitch.gtk_picker",
                "from gi.repository import Gtk, Gdk",
                "print('ok')",
            ]
        )

        result = _subprocess_check(code)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("PyGIWarning", result.stderr)
        self.assertNotIn("without specifying a version", result.stderr)

    @unittest.skipUnless(gtk_picker.GTK_AVAILABLE, "PyGObject/GTK not available")
    def test_the_gtk3_only_api_the_picker_uses_exists(self) -> None:
        """Gdk.Screen is GTK3-only; if a pin ever slips to 4.0 this is what breaks."""
        result = _subprocess_check(
            "import monswitch.gtk_picker\n"
            "from gi.repository import Gdk, Gtk\n"
            "assert hasattr(Gdk, 'Screen'), 'Gdk.Screen missing - wrong Gdk version'\n"
            "assert hasattr(Gtk, 'main'), 'Gtk.main missing - wrong Gtk version'\n"
            "assert hasattr(Gtk.WindowType, 'POPUP')\n"
            "print('ok')\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


class WindowBehaviourTest(unittest.TestCase):
    """Source-level guards for the focus bug, which no logic test can reach.

    The picker first shipped with Gtk.WindowType.POPUP. A POPUP is override-redirect: the
    window manager does not manage it and never gives it keyboard focus, so no key events
    arrived -- taking Escape and type-to-filter with them. These assertions are coarse, but
    they pin the specific mistake, and the alternative is no coverage at all short of driving
    a real X server.
    """

    def setUp(self) -> None:
        self.text = SOURCE.read_text()

    def test_uses_a_focusable_toplevel_not_a_popup(self) -> None:
        self.assertIn("Gtk.WindowType.TOPLEVEL", self.text)
        self.assertNotIn("Gtk.WindowType.POPUP", self.text)

    def test_stays_frameless(self) -> None:
        """ADR-0008 wants no frame; TOPLEVEL alone would draw titlebar decorations."""
        self.assertIn("set_decorated(False)", self.text)

    def test_asks_the_window_manager_for_focus(self) -> None:
        """show_all() does not request focus; present() does."""
        self.assertIn("present_with_time", self.text)

    def test_no_arming_machinery_remains(self) -> None:
        """ADR-0018 removed it; this stops it reappearing in the front-end by hand."""
        for gone in ("armed", "disarm", "monswitch-warning", "require_confirmation"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.text)

    def test_binds_escape(self) -> None:
        self.assertIn("Gdk.KEY_Escape", self.text)

    def test_has_a_close_affordance(self) -> None:
        self.assertIn("close_button", self.text)
        self.assertIn('close_button.connect("clicked"', self.text)

    def test_key_handler_is_bound_to_both_window_and_entry(self) -> None:
        """The entry holds focus, so a theme or input method could consume keys first."""
        self.assertIn('window.connect("key-press-event", on_key)', self.text)
        self.assertIn('entry.connect("key-press-event", on_key)', self.text)

    def test_focus_loss_dismissal_is_guarded(self) -> None:
        """Closing on focus-out before ever holding focus makes the picker never appear."""
        self.assertIn("had_focus", self.text)

    @unittest.skipUnless(gtk_picker.GTK_AVAILABLE, "PyGObject/GTK not available")
    def test_every_gtk_api_used_exists(self) -> None:
        """Catches a typo'd or GTK4-only call without constructing a window."""
        code = NEWLINE.join(
            [
                "import monswitch.gtk_picker",
                "from gi.repository import Gtk, Gdk",
                "names = [",
                "    (Gtk.WindowType, 'TOPLEVEL'), (Gdk.WindowTypeHint, 'DIALOG'),",
                "    (Gtk.ReliefStyle, 'NONE'), (Gdk, 'CURRENT_TIME'),",
                "    (Gtk.Window, 'present_with_time'), (Gtk.Window, 'set_accept_focus'),",
                "    (Gtk.Window, 'set_focus_on_map'), (Gtk.Window, 'set_decorated'),",
                "    (Gtk.Window, 'set_skip_taskbar_hint'), (Gtk.Button, 'set_relief'),",
                "    (Gtk, 'SearchEntry'),",
                "]",
                "missing = [n for obj, n in names if not hasattr(obj, n)]",
                "assert not missing, missing",
                "print('ok')",
            ]
        )

        result = _subprocess_check(code)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    @unittest.skipUnless(gtk_picker.GTK_AVAILABLE, "PyGObject/GTK not available")
    def test_css_parses(self) -> None:
        code = NEWLINE.join(
            [
                "import monswitch.gtk_picker as gp",
                "from gi.repository import Gtk",
                "Gtk.CssProvider().load_from_data(gp.CSS)",
                "print('ok')",
            ]
        )

        result = _subprocess_check(code)

        self.assertEqual(result.returncode, 0, result.stderr)


class AvailabilityTest(unittest.TestCase):
    def test_reports_availability_without_raising(self) -> None:
        """Importing this module must never fail, so the CLI can report GTK's absence."""
        self.assertIsInstance(gtk_picker.GTK_AVAILABLE, bool)

    def test_error_is_none_when_available(self) -> None:
        if gtk_picker.GTK_AVAILABLE:
            self.assertIsNone(gtk_picker.gtk_import_error())
        else:
            self.assertIsInstance(gtk_picker.gtk_import_error(), str)

    def test_module_imports_without_a_display(self) -> None:
        """Import must not need DISPLAY; only run_picker does."""
        result = _subprocess_check(
            "import monswitch.gtk_picker as gp\nprint(bool(gp.GTK_AVAILABLE))\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)


class ConfirmationStrategyTest(unittest.TestCase):
    """The picker confirms through its own two-step Enter, so the strategy must never be used."""

    def test_strategy_refuses_to_be_asked(self) -> None:
        strategy = gtk_picker._AlreadyConfirmed()

        with self.assertRaises(AssertionError):
            strategy.confirm("switch?", confirmation=None)

    def test_strategy_refuses_to_choose(self) -> None:
        strategy = gtk_picker._AlreadyConfirmed()

        with self.assertRaises(AssertionError):
            strategy.choose("dp", [])


if __name__ == "__main__":
    unittest.main()
