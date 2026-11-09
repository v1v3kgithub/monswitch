"""Tests for input resolution and the interaction strategies.

The ambiguity cases carry the weight here. Guessing between two inputs means potentially
switching a monitor to the wrong port, which may be unrecoverable from software, so "resolves
to several" has to be a first-class outcome rather than an error to paper over.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import io
import unittest

from monswitch.core.edid import EdidIdentity
from monswitch.core.interaction import (
    Ambiguous,
    Choice,
    Declined,
    Narrowing,
    NeedsChoice,
    Prompting,
    Strict,
)
from monswitch.core.models import Confirmation, InputSource, Monitor, Provenance
from monswitch.core.resolution import BUILTIN_SLUGS, resolve_input

HP = EdidIdentity(manufacturer="HPN", product_code=13327, model="HP Z38c", serial="DEMOSERIAL")


def source(code: int, name: str | None, provenance: Provenance = Provenance.REPORTED) -> InputSource:
    return InputSource(code=code, name=name, provenance=provenance)


def monitor(*inputs: InputSource, host_input: int | None = 0x13) -> Monitor:
    return Monitor(
        identity=HP, bus=16, connector="DP-1", inputs=inputs, host_input=host_input
    )


REFERENCE = monitor(
    source(0x0F, "DisplayPort-1"),
    source(0x10, "DisplayPort-2"),
    source(0x11, "HDMI-1"),
    source(0x13, "USB-C", Provenance.LIVE),
)


class NicknameTest(unittest.TestCase):
    def test_exact_nickname(self) -> None:
        result = resolve_input(REFERENCE, "HDMI-1")

        self.assertTrue(result.is_unique)
        self.assertEqual(result.only.code, 0x11)
        self.assertEqual(result.matched_by, "nickname")

    def test_nickname_is_case_and_punctuation_insensitive(self) -> None:
        for query in ("usb-c", "USB C", "usbC", "usb_c"):
            with self.subTest(query=query):
                self.assertEqual(resolve_input(REFERENCE, query).only.code, 0x13)

    def test_config_nickname_wins_over_a_builtin_slug(self) -> None:
        """Someone who named an input "dp" meant their input, not DisplayPort-1."""
        subject = monitor(
            source(0x0F, "DisplayPort-1"),
            source(0x11, "dp", Provenance.CONFIRMED),
        )

        result = resolve_input(subject, "dp")

        self.assertTrue(result.is_unique)
        self.assertEqual(result.only.code, 0x11)
        self.assertEqual(result.matched_by, "nickname")


class SlugTest(unittest.TestCase):
    def test_specific_slug_resolves(self) -> None:
        result = resolve_input(REFERENCE, "dp1")

        self.assertTrue(result.is_unique)
        self.assertEqual(result.only.code, 0x0F)
        self.assertEqual(result.matched_by, "slug")

    def test_family_slug_is_ambiguous_when_both_exist(self) -> None:
        """ADR-0006's own example: "dp" must not silently pick the lower-numbered input."""
        result = resolve_input(REFERENCE, "dp")

        self.assertTrue(result.is_ambiguous)
        self.assertEqual({m.code for m in result.matches}, {0x0F, 0x10})

    def test_family_slug_is_unique_when_only_one_exists(self) -> None:
        result = resolve_input(REFERENCE, "hdmi")

        self.assertTrue(result.is_unique)
        self.assertEqual(result.only.code, 0x11)

    def test_slug_never_matches_an_absent_input(self) -> None:
        """A query can never resolve to a code this panel does not offer."""
        result = resolve_input(REFERENCE, "hdmi2")

        self.assertTrue(result.is_empty)

    def test_thunderbolt_aliases(self) -> None:
        subject = monitor(source(0x1B, None))

        for query in ("tb", "thunderbolt", "usbc2"):
            with self.subTest(query=query):
                self.assertEqual(resolve_input(subject, query).only.code, 0x1B)

    def test_slug_table_codes_are_in_range(self) -> None:
        for slug, codes in BUILTIN_SLUGS.items():
            with self.subTest(slug=slug):
                self.assertTrue(all(0 <= code <= 0xFF for code in codes))


class CodeTest(unittest.TestCase):
    def test_prefixed_hex(self) -> None:
        result = resolve_input(REFERENCE, "0x13")

        self.assertTrue(result.is_unique)
        self.assertEqual(result.only.code, 0x13)
        self.assertEqual(result.matched_by, "code")

    def test_bare_hex(self) -> None:
        """capabilities prints codes bare, so users will type them that way."""
        self.assertEqual(resolve_input(REFERENCE, "13").only.code, 0x13)

    def test_bare_hex_is_hex_not_decimal(self) -> None:
        """'13' means 0x13, never decimal 13 (= 0x0d). Codes are hex, predictably."""
        result = resolve_input(REFERENCE, "13")

        self.assertEqual(result.only.code, 0x13)
        self.assertNotEqual(result.only.code, 13)

    def test_unnamed_input_is_reachable_by_code(self) -> None:
        """0x1f has no name by design, so its code is the only way to ask for it."""
        subject = monitor(source(0x1F, None, Provenance.GUESSED))

        self.assertEqual(resolve_input(subject, "0x1f").only.code, 0x1F)

    def test_absent_code_matches_nothing(self) -> None:
        self.assertTrue(resolve_input(REFERENCE, "0x99").is_empty)


class PrefixTest(unittest.TestCase):
    def test_prefix_of_a_name(self) -> None:
        result = resolve_input(REFERENCE, "hdmi-")

        self.assertTrue(result.is_unique)
        self.assertEqual(result.only.code, 0x11)

    def test_ambiguous_prefix(self) -> None:
        result = resolve_input(REFERENCE, "displayport")

        self.assertTrue(result.is_ambiguous)
        self.assertEqual(len(result.matches), 2)


class EmptyQueryTest(unittest.TestCase):
    def test_blank_query_matches_nothing(self) -> None:
        for query in ("", "   ", "---"):
            with self.subTest(query=query):
                self.assertTrue(resolve_input(REFERENCE, query).is_empty)

    def test_only_raises_when_not_unique(self) -> None:
        with self.assertRaises(ValueError):
            resolve_input(REFERENCE, "dp").only


class StrictStrategyTest(unittest.TestCase):
    def test_refuses_to_choose(self) -> None:
        """Scripts must be deterministic: the same command never picks differently."""
        with self.assertRaises(Ambiguous) as caught:
            Strict().choose("dp", [Choice(1, "DisplayPort-1"), Choice(2, "DisplayPort-2")])

        self.assertIn("dp", str(caught.exception))
        self.assertIn("DisplayPort-1", str(caught.exception))

    def test_refuses_to_confirm(self) -> None:
        with self.assertRaises(Declined) as caught:
            Strict().confirm("switch away?", confirmation=Confirmation.LEAVING_HOST)

        self.assertIn("--yes", str(caught.exception))

    def test_ambiguous_is_a_declined(self) -> None:
        self.assertTrue(issubclass(Ambiguous, Declined))


class NarrowingStrategyTest(unittest.TestCase):
    def test_hands_candidates_back(self) -> None:
        """The picker re-renders its list rather than closing with nothing done."""
        with self.assertRaises(NeedsChoice) as caught:
            Narrowing().choose("dp", [Choice("a", "A"), Choice("b", "B")])

        self.assertEqual(caught.exception.candidates, ("a", "b"))

    def test_confirmation_also_goes_back_to_the_frontend(self) -> None:
        with self.assertRaises(NeedsChoice):
            Narrowing().confirm("switch?", confirmation=Confirmation.LEAVING_HOST)


class PromptingStrategyTest(unittest.TestCase):
    def strategy(self, typed: str) -> tuple[Prompting, io.StringIO]:
        output = io.StringIO()
        return Prompting(input_stream=io.StringIO(typed), output_stream=output), output

    def test_numbered_choice(self) -> None:
        strategy, output = self.strategy("2\n")

        chosen = strategy.choose("dp", [Choice("a", "DisplayPort-1"), Choice("b", "DisplayPort-2")])

        self.assertEqual(chosen, "b")
        self.assertIn("1) DisplayPort-1", output.getvalue())
        self.assertIn("2) DisplayPort-2", output.getvalue())

    def test_unverified_candidates_are_flagged_in_the_list(self) -> None:
        strategy, output = self.strategy("1\n")

        strategy.choose("dp", [Choice("a", "DisplayPort-1", note="unverified")])

        self.assertIn("unverified", output.getvalue())

    def test_out_of_range_choice_cancels(self) -> None:
        strategy, _ = self.strategy("9\n")

        with self.assertRaises(Declined):
            strategy.choose("dp", [Choice("a", "A")])

    def test_non_numeric_choice_cancels(self) -> None:
        strategy, _ = self.strategy("banana\n")

        with self.assertRaises(Declined):
            strategy.choose("dp", [Choice("a", "A")])

    def test_confirmation_defaults_to_no(self) -> None:
        """A bare Enter must never switch a monitor."""
        strategy, _ = self.strategy("\n")

        self.assertFalse(
            strategy.confirm("switch away?", confirmation=Confirmation.LEAVING_HOST)
        )

    def test_y_confirms_an_ordinary_switch(self) -> None:
        for answer in ("y\n", "Y\n", "yes\n"):
            with self.subTest(answer=answer):
                strategy, _ = self.strategy(answer)

                self.assertTrue(
                    strategy.confirm("switch?", confirmation=Confirmation.LEAVING_HOST)
                )

    def test_unverified_destination_demands_the_full_word(self) -> None:
        """The gravest case gets the highest friction (ADR-0009)."""
        strategy, _ = self.strategy("y\n")

        self.assertFalse(
            strategy.confirm(
                "switch?", confirmation=Confirmation.UNVERIFIED_DESTINATION
            )
        )

    def test_unverified_destination_accepts_yes(self) -> None:
        strategy, output = self.strategy("yes\n")

        self.assertTrue(
            strategy.confirm(
                "switch?", confirmation=Confirmation.UNVERIFIED_DESTINATION
            )
        )
        self.assertIn("Type 'yes'", output.getvalue())


if __name__ == "__main__":
    unittest.main()
