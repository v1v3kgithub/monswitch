"""Tests for the ``ddcutil`` subprocess wrapper.

Driven through an injected runner, so the whole error matrix is exercised without hardware
and without running ddcutil. Error fixtures are verbatim captures from the reference machine:
the permission case comes from a genuinely root-only bus (``/dev/i2c-0``, which has no
``uaccess`` ACL), not from a hand-written string.

One class runs the real binary, skipped when it is absent. Those tests are read-only —
``detect``, ``capabilities`` and ``getvcp`` only. There is no ``setvcp`` anywhere in this
module, and the wrapper exposes no method that could issue one.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from collections.abc import Sequence
from pathlib import Path

from monswitch.core.ddc import (
    BusNotFound,
    CommandResult,
    Ddcutil,
    DdcutilFailed,
    DdcutilNotInstalled,
    DdcutilParseError,
    DdcutilTimeout,
    I2cPermissionDenied,
    NoMonitorOnBus,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ddcutil"
HAVE_DDCUTIL = shutil.which("ddcutil") is not None


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


class FakeRunner:
    """A runner that replays canned output and records what it was asked to run."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> CommandResult:
        self.calls.append(tuple(argv))
        self.timeouts.append(timeout)
        return CommandResult(
            argv=tuple(argv),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class WriteSurfaceTest(unittest.TestCase):
    """The wrapper must offer no way to change hardware state yet."""

    def test_exposes_no_setvcp_method(self) -> None:
        """A write path that does not exist cannot be called by accident or by a test."""
        self.assertFalse([name for name in dir(Ddcutil) if "setvcp" in name.lower()])
        self.assertFalse([name for name in dir(Ddcutil) if "set_vcp" in name.lower()])

    def test_no_command_ever_contains_setvcp(self) -> None:
        runner = FakeRunner(stdout="VCP 60 SNC x13")
        ddcutil = Ddcutil(runner=runner)

        ddcutil.getvcp_input(16)
        runner.stdout = fixture("capabilities")
        ddcutil.capabilities_inputs(16)
        runner.stdout = fixture("detect-terse")
        ddcutil.detect()

        for argv in runner.calls:
            self.assertNotIn("setvcp", argv)


class GetvcpTest(unittest.TestCase):
    def test_builds_a_bus_scoped_terse_command(self) -> None:
        """--bus is what avoids the slow full probe; --terse is the cleaner parse target."""
        runner = FakeRunner(stdout="VCP 60 SNC x13")

        Ddcutil(runner=runner).getvcp_input(16)

        self.assertEqual(runner.calls[0], ("ddcutil", "--bus", "16", "getvcp", "60", "--terse"))

    def test_returns_the_parsed_code(self) -> None:
        runner = FakeRunner(stdout=fixture("getvcp60-terse"))

        self.assertEqual(Ddcutil(runner=runner).getvcp_input(16), 0x13)

    def test_accepts_the_verbose_invalid_value_form(self) -> None:
        runner = FakeRunner(stdout=fixture("getvcp60"))

        self.assertEqual(Ddcutil(runner=runner).getvcp_input(16), 0x13)

    def test_honours_the_configured_executable(self) -> None:
        runner = FakeRunner(stdout="VCP 60 SNC x13")

        Ddcutil(executable="/usr/bin/ddcutil", runner=runner).getvcp_input(16)

        self.assertEqual(runner.calls[0][0], "/usr/bin/ddcutil")

    def test_passes_the_timeout_through(self) -> None:
        runner = FakeRunner(stdout="VCP 60 SNC x13")

        Ddcutil(timeout=2.5, runner=runner).getvcp_input(16)

        self.assertEqual(runner.timeouts[0], 2.5)

    def test_unparseable_success_raises_parse_error_not_command_error(self) -> None:
        runner = FakeRunner(stdout="something unexpected\n", returncode=0)

        with self.assertRaises(DdcutilParseError):
            Ddcutil(runner=runner).getvcp_input(16)


class ErrorClassificationTest(unittest.TestCase):
    """ddcutil 1.4.1 exits 1 for everything, so the message is the only discriminator."""

    def test_permission_denied(self) -> None:
        runner = FakeRunner(stdout=fixture("err-permission"), returncode=1)

        with self.assertRaises(I2cPermissionDenied) as caught:
            Ddcutil(runner=runner).getvcp_input(0)

        self.assertIn("bus 0", str(caught.exception))
        self.assertIn("non-root access", str(caught.exception))

    def test_permission_wins_over_no_monitor(self) -> None:
        """The real permission output contains BOTH lines; misordering hides the real cause."""
        self.assertIn("No monitor detected", fixture("err-permission"))
        runner = FakeRunner(stdout=fixture("err-permission"), returncode=1)

        with self.assertRaises(I2cPermissionDenied):
            Ddcutil(runner=runner).getvcp_input(0)

    def test_bus_not_found(self) -> None:
        runner = FakeRunner(stdout=fixture("err-bus-not-found"), returncode=1)

        with self.assertRaises(BusNotFound) as caught:
            Ddcutil(runner=runner).getvcp_input(99)

        self.assertIn("re-resolve", str(caught.exception))

    def test_no_monitor_on_bus(self) -> None:
        runner = FakeRunner(stdout=fixture("err-no-monitor"), returncode=1)

        with self.assertRaises(NoMonitorOnBus) as caught:
            Ddcutil(runner=runner).getvcp_input(7)

        self.assertIn("on-screen menu", str(caught.exception))

    def test_unrecognised_failure_falls_back(self) -> None:
        runner = FakeRunner(stdout="something went sideways", returncode=3)

        with self.assertRaises(DdcutilFailed) as caught:
            Ddcutil(runner=runner).getvcp_input(16)

        self.assertIn("exited 3", str(caught.exception))

    def test_classification_reads_stderr_too(self) -> None:
        """ddcutil splits diagnostics across both streams depending on the message."""
        runner = FakeRunner(stdout="", stderr=fixture("err-permission"), returncode=1)

        with self.assertRaises(I2cPermissionDenied):
            Ddcutil(runner=runner).getvcp_input(0)

    def test_errors_share_a_base(self) -> None:
        from monswitch.core.ddc import DdcutilError

        for error in (
            I2cPermissionDenied,
            BusNotFound,
            NoMonitorOnBus,
            DdcutilFailed,
            DdcutilNotInstalled,
            DdcutilTimeout,
        ):
            with self.subTest(error=error.__name__):
                self.assertTrue(issubclass(error, DdcutilError))

    def test_parse_error_is_not_a_command_error(self) -> None:
        """Unrecognisable output and a failed command are different problems."""
        from monswitch.core.ddc import DdcutilError

        self.assertFalse(issubclass(DdcutilParseError, DdcutilError))


class CapabilitiesTest(unittest.TestCase):
    def test_builds_the_command(self) -> None:
        runner = FakeRunner(stdout=fixture("capabilities"))

        Ddcutil(runner=runner).capabilities_inputs(16)

        self.assertEqual(runner.calls[0], ("ddcutil", "--bus", "16", "capabilities"))

    def test_parses_inputs(self) -> None:
        runner = FakeRunner(stdout=fixture("capabilities"))

        inputs = Ddcutil(runner=runner).capabilities_inputs(16)

        self.assertEqual([i.code for i in inputs], [0x0F, 0x11, 0x1F])

    def test_failure_is_classified(self) -> None:
        runner = FakeRunner(stdout=fixture("err-permission"), returncode=1)

        with self.assertRaises(I2cPermissionDenied):
            Ddcutil(runner=runner).capabilities_inputs(0)


class DetectTest(unittest.TestCase):
    def test_terse_by_default(self) -> None:
        runner = FakeRunner(stdout=fixture("detect-terse"))

        Ddcutil(runner=runner).detect()

        self.assertEqual(runner.calls[0], ("ddcutil", "detect", "--terse"))

    def test_verbose_on_request(self) -> None:
        runner = FakeRunner(stdout=fixture("detect"))

        Ddcutil(runner=runner).detect(terse=False)

        self.assertEqual(runner.calls[0], ("ddcutil", "detect"))

    def test_parses_displays(self) -> None:
        runner = FakeRunner(stdout=fixture("detect-terse"))

        displays = Ddcutil(runner=runner).detect()

        self.assertEqual(len(displays), 2)
        self.assertEqual(displays[1].serial, "DEMOSERIAL")

    def test_finding_nothing_is_not_an_error(self) -> None:
        """detect exits 0 with no displays; that is an answer, not a failure."""
        runner = FakeRunner(stdout="ddcutil: No displays found\n", returncode=0)

        self.assertEqual(Ddcutil(runner=runner).detect(), ())


class SubprocessRunnerTest(unittest.TestCase):
    """The default runner's own behaviour, tested without invoking ddcutil."""

    def test_missing_executable_raises_not_installed(self) -> None:
        ddcutil = Ddcutil(executable="definitely-not-a-real-binary-xyz")

        with self.assertRaises(DdcutilNotInstalled) as caught:
            ddcutil.getvcp_input(16)

        self.assertIn("not found on PATH", str(caught.exception))

    def test_timeout_raises_timeout(self) -> None:
        """A wedged DDC/CI channel must not hang a launcher indefinitely."""
        with self.assertRaises(DdcutilTimeout) as caught:
            Ddcutil().runner(["sleep", "5"], 0.05)

        self.assertIn("did not finish", str(caught.exception))

    def test_non_shell_invocation(self) -> None:
        """Arguments must never be interpreted by a shell."""
        result = Ddcutil().runner(["printf", "%s", "a b"], 5.0)

        self.assertEqual(result.stdout, "a b")
        self.assertEqual(result.returncode, 0)

    def test_combines_streams(self) -> None:
        result = CommandResult(argv=("x",), returncode=1, stdout="out", stderr="err")

        self.assertIn("out", result.output)
        self.assertIn("err", result.output)


@unittest.skipUnless(HAVE_DDCUTIL, "ddcutil not installed")
class RealDdcutilTest(unittest.TestCase):
    """Against the real binary. Read-only: detect, capabilities, getvcp only."""

    def test_detect_runs_and_parses(self) -> None:
        displays = Ddcutil().detect()

        for display in displays:
            if display.usable:
                self.assertIsNotNone(display.bus)

    def test_permission_denied_on_a_root_only_bus(self) -> None:
        """/dev/i2c-0 has no uaccess ACL, so this is a genuine EACCES, not a simulation.

        Skipped if the user does have access to bus 0 (e.g. running as root), since then
        there is nothing to assert.
        """
        probe = subprocess.run(
            ["ddcutil", "--bus", "0", "getvcp", "60", "--terse"],
            capture_output=True,
            text=True,
            check=False,
        )
        combined = f"{probe.stdout}{probe.stderr}".lower()
        if "permission denied" not in combined:
            self.skipTest("bus 0 is accessible on this machine")

        with self.assertRaises(I2cPermissionDenied):
            Ddcutil().getvcp_input(0)

    def test_nonexistent_bus(self) -> None:
        with self.assertRaises((BusNotFound, NoMonitorOnBus)):
            Ddcutil().getvcp_input(99)


if __name__ == "__main__":
    unittest.main()
