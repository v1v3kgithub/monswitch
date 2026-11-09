"""Tests for the write path and its confirmation gate.

**No test in this file issues a real ``setvcp``.** Every one drives a fake runner that records
the command line and returns a canned result; a runner that would actually execute something
is never constructed. The project's standing rule is that ``setvcp 60`` is only ever issued
with a human agreeing in the moment, and a test suite cannot supply that.

The assertions divide in two:

* the **gate** — that a switch is refused, or asked about, exactly when the ADRs require, and
  that nothing is written when it is refused;
* the **command** — that when a write does happen it is the one intended, on the right bus,
  with the right code.

The second matters because this is the only function in the project that can change hardware,
and the first because the thing it changes may not be changeable back from software.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from monswitch.core.ddc import (
    CommandResult,
    I2cPermissionDenied,
    SetvcpFailed,
    WriteCapableDdcutil,
    _set_input_argv,
)
from monswitch.core.edid import EdidIdentity
from monswitch.core.interaction import Confirmation, Declined, Narrowing, Strict
from monswitch.core.models import InputSource, Monitor, Provenance
from monswitch.core.switching import SwitchRefused, switch_input

HP = EdidIdentity(manufacturer="HPN", product_code=13327, model="HP Z38c", serial="DEMOSERIAL")


class RecordingRunner:
    """Records commands instead of running them."""

    def __init__(self, returncode: int = 0, output: str = "") -> None:
        self.returncode = returncode
        self.output = output
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> CommandResult:
        self.calls.append(tuple(argv))
        return CommandResult(
            argv=tuple(argv),
            returncode=self.returncode,
            stdout=self.output,
            stderr="",
        )

    @property
    def wrote(self) -> bool:
        return any("setvcp" in argv for argv in self.calls)


class Agreeing:
    """A strategy that says yes to everything. Only ever paired with a fake runner."""

    def __init__(self) -> None:
        self.prompts: list[tuple[str, Confirmation]] = []

    def choose(self, query: str, choices: Sequence[object]) -> object:
        raise AssertionError("choose should not be called by switch_input")

    def confirm(self, prompt: str, *, confirmation: Confirmation) -> bool:
        self.prompts.append((prompt, confirmation))
        return True


class Refusing(Agreeing):
    """A strategy that says no."""

    def confirm(self, prompt: str, *, confirmation: Confirmation) -> bool:
        self.prompts.append((prompt, confirmation))
        return False


def source(
    code: int, name: str | None = None, provenance: Provenance = Provenance.LIVE
) -> InputSource:
    return InputSource(code=code, name=name, provenance=provenance)


def monitor(*inputs: InputSource, host_input: int | None = 0x13) -> Monitor:
    return Monitor(
        identity=HP,
        bus=16,
        connector="DP-1",
        inputs=inputs or (source(0x13, "USB-C"),),
        host_input=host_input,
    )


def writer(runner: RecordingRunner) -> WriteCapableDdcutil:
    return WriteCapableDdcutil(executable="ddcutil", timeout=1.0, runner=runner)


class ArgvTest(unittest.TestCase):
    """The exact command, built by a function that cannot execute anything."""

    def test_builds_the_expected_command(self) -> None:
        self.assertEqual(
            _set_input_argv("ddcutil", 16, 0x0F),
            ("ddcutil", "--bus", "16", "setvcp", "60", "0x0f"),
        )

    def test_code_is_two_digit_hex(self) -> None:
        self.assertEqual(_set_input_argv("ddcutil", 1, 0x3)[-1], "0x03")

    def test_writes_only_feature_60(self) -> None:
        """Other VCP features are out of scope and must never be touched."""
        self.assertEqual(_set_input_argv("ddcutil", 1, 0x11)[-2], "60")

    def test_rejects_an_out_of_range_code(self) -> None:
        for code in (-1, 256, 999):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    _set_input_argv("ddcutil", 1, code)


class BlockedTest(unittest.TestCase):
    """ADR-0013: an unknown Host Input means no switch, whatever the flags say."""

    def test_refused_without_a_host_input(self) -> None:
        runner = RecordingRunner()

        with self.assertRaises(SwitchRefused) as caught:
            switch_input(
                monitor(source(0x0F, "DP-1"), host_input=None),
                source(0x0F, "DP-1"),
                ddcutil=writer(runner),
                strategy=Agreeing(),
            )

        self.assertEqual(caught.exception.confirmation, Confirmation.BLOCKED)
        self.assertFalse(runner.wrote)

    def test_force_cannot_override_blocked(self) -> None:
        """Fail closed: the risk is unknown, so no flag may wave it through."""
        runner = RecordingRunner()

        with self.assertRaises(SwitchRefused):
            switch_input(
                monitor(source(0x0F, "DP-1"), host_input=None),
                source(0x0F, "DP-1"),
                ddcutil=writer(runner),
                strategy=Agreeing(),
                assume_yes=True,
                force=True,
                confirm_switch=False,
            )

        self.assertFalse(runner.wrote)

    def test_refusal_explains_itself(self) -> None:
        with self.assertRaises(SwitchRefused) as caught:
            switch_input(
                monitor(source(0x0F), host_input=None),
                source(0x0F),
                ddcutil=writer(RecordingRunner()),
                strategy=Agreeing(),
            )

        self.assertIn("could not be read", str(caught.exception))
        self.assertIn("DDC/CI", str(caught.exception))


class NoOpTest(unittest.TestCase):
    def test_switching_to_the_current_input_writes_nothing(self) -> None:
        runner = RecordingRunner()
        usbc = source(0x13, "USB-C")

        outcome = switch_input(
            monitor(usbc), usbc, ddcutil=writer(runner), strategy=Agreeing()
        )

        self.assertFalse(outcome.changed)
        self.assertFalse(outcome.channel_lost)
        self.assertFalse(runner.wrote)

    def test_no_op_is_not_an_error(self) -> None:
        usbc = source(0x13, "USB-C")

        outcome = switch_input(
            monitor(usbc), usbc, ddcutil=writer(RecordingRunner()), strategy=Agreeing()
        )

        self.assertEqual(outcome.confirmation, Confirmation.NONE)

    def test_no_op_does_not_prompt(self) -> None:
        strategy = Agreeing()
        usbc = source(0x13, "USB-C")

        switch_input(
            monitor(usbc), usbc, ddcutil=writer(RecordingRunner()), strategy=strategy
        )

        self.assertEqual(strategy.prompts, [])


class LeavingHostTest(unittest.TestCase):
    """ADR-0002: the unrecoverable direction needs agreement."""

    def setUp(self) -> None:
        self.destination = source(0x0F, "Desktop PC", Provenance.CONFIRMED)
        self.subject = monitor(source(0x13, "USB-C"), self.destination)

    def test_prompts_before_leaving_the_host(self) -> None:
        strategy = Agreeing()
        runner = RecordingRunner()

        outcome = switch_input(
            self.subject, self.destination, ddcutil=writer(runner), strategy=strategy
        )

        self.assertEqual(len(strategy.prompts), 1)
        self.assertEqual(strategy.prompts[0][1], Confirmation.LEAVING_HOST)
        self.assertTrue(outcome.changed)
        self.assertTrue(outcome.channel_lost)

    def test_prompt_states_the_consequence(self) -> None:
        strategy = Agreeing()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(RecordingRunner()),
            strategy=strategy,
        )

        prompt = strategy.prompts[0][0]

        self.assertIn("stop showing this machine", prompt)
        self.assertIn("cannot switch it back", prompt)

    def test_declining_writes_nothing(self) -> None:
        runner = RecordingRunner()

        with self.assertRaises(SwitchRefused) as caught:
            switch_input(
                self.subject,
                self.destination,
                ddcutil=writer(runner),
                strategy=Refusing(),
            )

        self.assertIn("cancelled", str(caught.exception))
        self.assertFalse(runner.wrote)

    def test_issues_the_right_command_when_agreed(self) -> None:
        runner = RecordingRunner()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(runner),
            strategy=Agreeing(),
        )

        self.assertEqual(
            runner.calls[-1], ("ddcutil", "--bus", "16", "setvcp", "60", "0x0f")
        )

    def test_yes_skips_the_prompt(self) -> None:
        strategy = Agreeing()
        runner = RecordingRunner()

        outcome = switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(runner),
            strategy=strategy,
            assume_yes=True,
        )

        self.assertEqual(strategy.prompts, [])
        self.assertTrue(outcome.changed)
        self.assertTrue(runner.wrote)

    def test_confirm_switch_false_skips_the_prompt(self) -> None:
        strategy = Agreeing()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(RecordingRunner()),
            strategy=strategy,
            confirm_switch=False,
        )

        self.assertEqual(strategy.prompts, [])

    def test_strict_strategy_refuses_rather_than_switching(self) -> None:
        """A script with no terminal must not switch unasked."""
        runner = RecordingRunner()

        with self.assertRaises(SwitchRefused):
            switch_input(
                self.subject,
                self.destination,
                ddcutil=writer(runner),
                strategy=Strict(),
            )

        self.assertFalse(runner.wrote)

    def test_strict_with_yes_proceeds(self) -> None:
        runner = RecordingRunner()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(runner),
            strategy=Strict(),
            assume_yes=True,
        )

        self.assertTrue(runner.wrote)


class UnverifiedDestinationTest(unittest.TestCase):
    """ADR-0009: no ordinary opt-out covers a code that may not exist."""

    def setUp(self) -> None:
        self.destination = source(0x1F, None, Provenance.GUESSED)
        self.subject = monitor(source(0x13, "USB-C"), self.destination)

    def test_prompts_with_the_stronger_warning(self) -> None:
        strategy = Agreeing()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(RecordingRunner()),
            strategy=strategy,
        )

        self.assertEqual(strategy.prompts[0][1], Confirmation.UNVERIFIED_DESTINATION)
        prompt = strategy.prompts[0][0]
        self.assertIn("inferred, not observed", prompt)
        self.assertIn("physical buttons", prompt)

    def test_yes_does_not_cover_it(self) -> None:
        strategy = Agreeing()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(RecordingRunner()),
            strategy=strategy,
            assume_yes=True,
        )

        self.assertEqual(len(strategy.prompts), 1)

    def test_confirm_switch_false_does_not_cover_it(self) -> None:
        strategy = Agreeing()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(RecordingRunner()),
            strategy=strategy,
            confirm_switch=False,
        )

        self.assertEqual(len(strategy.prompts), 1)

    def test_force_covers_it(self) -> None:
        strategy = Agreeing()
        runner = RecordingRunner()

        switch_input(
            self.subject,
            self.destination,
            ddcutil=writer(runner),
            strategy=strategy,
            force=True,
        )

        self.assertEqual(strategy.prompts, [])
        self.assertTrue(runner.wrote)

    def test_declining_writes_nothing(self) -> None:
        runner = RecordingRunner()

        with self.assertRaises(SwitchRefused):
            switch_input(
                self.subject,
                self.destination,
                ddcutil=writer(runner),
                strategy=Refusing(),
            )

        self.assertFalse(runner.wrote)

    def test_a_reported_destination_is_also_unverified(self) -> None:
        reported = source(0x11, "HDMI-1", Provenance.REPORTED)
        subject = monitor(source(0x13, "USB-C"), reported)
        strategy = Agreeing()

        switch_input(
            subject, reported, ddcutil=writer(RecordingRunner()), strategy=strategy
        )

        self.assertEqual(strategy.prompts[0][1], Confirmation.UNVERIFIED_DESTINATION)


class ChannelLossTest(unittest.TestCase):
    def test_no_read_back_after_writing(self) -> None:
        """Success that leaves the host destroys the channel, so a read-back proves nothing."""
        runner = RecordingRunner()
        destination = source(0x0F, "Desktop", Provenance.CONFIRMED)

        switch_input(
            monitor(source(0x13, "USB-C"), destination),
            destination,
            ddcutil=writer(runner),
            strategy=Agreeing(),
        )

        self.assertEqual(len(runner.calls), 1)
        self.assertNotIn("getvcp", runner.calls[0])

    def test_channel_loss_is_reported(self) -> None:
        destination = source(0x0F, "Desktop", Provenance.CONFIRMED)

        outcome = switch_input(
            monitor(source(0x13, "USB-C"), destination),
            destination,
            ddcutil=writer(RecordingRunner()),
            strategy=Agreeing(),
        )

        self.assertTrue(outcome.channel_lost)


class WriteFailureTest(unittest.TestCase):
    def test_nonzero_exit_raises(self) -> None:
        runner = RecordingRunner(returncode=1, output="DDC communication failed")
        destination = source(0x0F, "Desktop", Provenance.CONFIRMED)

        with self.assertRaises(SetvcpFailed) as caught:
            switch_input(
                monitor(source(0x13, "USB-C"), destination),
                destination,
                ddcutil=writer(runner),
                strategy=Agreeing(),
            )

        self.assertIn("may not have switched", str(caught.exception))

    def test_permission_failure_is_classified(self) -> None:
        runner = RecordingRunner(
            returncode=1,
            output="Open failed for /dev/i2c-16: errno=EACCES(13): Permission denied",
        )
        destination = source(0x0F, "Desktop", Provenance.CONFIRMED)

        with self.assertRaises(I2cPermissionDenied):
            switch_input(
                monitor(source(0x13, "USB-C"), destination),
                destination,
                ddcutil=writer(runner),
                strategy=Agreeing(),
            )


class NarrowingStrategyTest(unittest.TestCase):
    def test_graphical_strategy_hands_the_question_back(self) -> None:
        """The picker renders its own confirmation; it must not switch behind the UI."""
        runner = RecordingRunner()
        destination = source(0x0F, "Desktop", Provenance.CONFIRMED)

        with self.assertRaises(SwitchRefused):
            switch_input(
                monitor(source(0x13, "USB-C"), destination),
                destination,
                ddcutil=writer(runner),
                strategy=Narrowing(),
            )

        self.assertFalse(runner.wrote)


class TypeGateTest(unittest.TestCase):
    def test_read_only_wrapper_has_no_write_method(self) -> None:
        """Holding a plain Ddcutil cannot reach the write path."""
        from monswitch.core.ddc import Ddcutil

        self.assertFalse(hasattr(Ddcutil, "set_input"))
        self.assertTrue(hasattr(WriteCapableDdcutil, "set_input"))

    def test_declined_is_not_a_write_failure(self) -> None:
        """Cancelling and failing are different outcomes and must not be conflated."""
        self.assertFalse(issubclass(SwitchRefused, SetvcpFailed))
        self.assertFalse(issubclass(Declined, SetvcpFailed))


if __name__ == "__main__":
    unittest.main()
