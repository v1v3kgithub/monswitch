"""How the core asks the user something.

ADR-0011's seam. ADR-0006 originally branched on :func:`sys.stdin.isatty`, which conflates two
different questions — *can I prompt?* and *is a human watching?* A GTK popup answers no to the
first and yes to the second, so under that test both graphical front-ends took the scripted
branch and failed silently.

So the caller supplies a strategy instead, and the core never asks about the terminal:

===================  ==========================================================
Strategy             Used by
===================  ==========================================================
:class:`Prompting`   An interactive terminal: ask, numbered, read a line
:class:`Strict`      Scripts and pipes: refuse, deterministically
:class:`Narrowing`   The GTK picker and Ulauncher: hand the choices back
===================  ==========================================================

Strategies decide *interaction*, never policy. Whether a switch needs confirming is
:func:`monswitch.core.models.switch_confirmation`'s decision; a strategy only carries out the
asking. And no strategy can authorise an agent to write: that always needs a human in the
moment, whatever the configuration says.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TextIO, TypeVar

from .models import Confirmation

__all__ = [
    "Ambiguous",
    "Choice",
    "Declined",
    "InteractionStrategy",
    "Narrowing",
    "NeedsChoice",
    "Prompting",
    "Strict",
]

T = TypeVar("T")


class Declined(Exception):
    """The user said no, or the strategy refuses to ask.

    Not an error in the usual sense: nothing went wrong, the action simply does not proceed.
    A front-end reports it as a cancellation, not a failure.
    """


class Ambiguous(Declined):
    """A query matched several candidates and the strategy cannot choose between them."""

    def __init__(self, query: str, candidates: Sequence[object]) -> None:
        self.query = query
        self.candidates = tuple(candidates)
        rendered = ", ".join(str(candidate) for candidate in self.candidates)
        super().__init__(f"{query!r} matches several inputs: {rendered}")


class NeedsChoice(Declined):
    """Raised by :class:`Narrowing` to hand the candidates back to a graphical front-end.

    The picker catches this and re-renders its list filtered to ``candidates``, rather than
    closing with nothing done.
    """

    def __init__(self, query: str, candidates: Sequence[object]) -> None:
        self.query = query
        self.candidates = tuple(candidates)
        super().__init__(f"{query!r} needs a choice between {len(self.candidates)} inputs")


@dataclass(frozen=True, slots=True)
class Choice:
    """One option offered to the user."""

    value: object
    label: str
    note: str | None = None


class InteractionStrategy(Protocol):
    """What the core may ask of whoever is driving it."""

    def choose(self, query: str, choices: Sequence[Choice]) -> object:
        """Pick one of ``choices``, or raise :class:`Declined`."""
        ...

    def confirm(self, prompt: str, *, confirmation: Confirmation) -> bool:
        """Approve a switch, or not. ``confirmation`` says how grave the question is."""
        ...


@dataclass(frozen=True, slots=True)
class Strict:
    """Never asks. For scripts, pipes and anything non-interactive.

    Ambiguity aborts rather than guessing, so a scripted run is deterministic: the same
    command either does the same thing every time or fails every time, and never quietly
    switches a different input because the discovery order changed (ADR-0006).
    """

    def choose(self, query: str, choices: Sequence[Choice]) -> object:
        raise Ambiguous(query, [choice.label for choice in choices])

    def confirm(self, prompt: str, *, confirmation: Confirmation) -> bool:
        # Name the flag that would actually work. --yes does not cover an unverified
        # destination (ADR-0009), so suggesting it there would send the user in a circle.
        flag = "--force" if confirmation is Confirmation.UNVERIFIED_DESTINATION else "--yes"
        raise Declined(
            f"{prompt}\n(refusing: this needs confirmation and there is no terminal to "
            f"ask on. Pass {flag} to proceed without being asked.)"
        )


@dataclass(frozen=True, slots=True)
class Narrowing:
    """Hands choices back to a graphical front-end instead of resolving them.

    Used by the picker and the launcher, where ambiguity is not an error but the ordinary
    state of a list the user has not finished filtering.
    """

    def choose(self, query: str, choices: Sequence[Choice]) -> object:
        raise NeedsChoice(query, [choice.value for choice in choices])

    def confirm(self, prompt: str, *, confirmation: Confirmation) -> bool:
        raise NeedsChoice(prompt, [])


@dataclass
class Prompting:
    """Asks on a terminal: a numbered list to choose from, y/N to confirm.

    Confirmation defaults to *no*, so an accidental Enter never switches a monitor. For the
    gravest case — an unverified destination — a bare "y" is not enough; the user must type
    "yes", because that is the switch most likely to leave them with a dark screen and no way
    back from software (ADR-0009).
    """

    input_stream: TextIO = field(default_factory=lambda: sys.stdin)
    output_stream: TextIO = field(default_factory=lambda: sys.stderr)

    def choose(self, query: str, choices: Sequence[Choice]) -> object:
        print(f"{query!r} could mean:", file=self.output_stream)
        for index, choice in enumerate(choices, start=1):
            note = f"  ({choice.note})" if choice.note else ""
            print(f"  {index}) {choice.label}{note}", file=self.output_stream)
        print(
            f"Choose 1-{len(choices)}, or anything else to cancel: ",
            end="",
            file=self.output_stream,
        )
        self.output_stream.flush()

        answer = self.input_stream.readline().strip()
        try:
            index = int(answer)
        except ValueError as exc:
            raise Declined(f"cancelled: {answer!r} is not a number") from exc

        if not 1 <= index <= len(choices):
            raise Declined(f"cancelled: {index} is not one of the options")
        return choices[index - 1].value

    def confirm(self, prompt: str, *, confirmation: Confirmation) -> bool:
        if confirmation is Confirmation.UNVERIFIED_DESTINATION:
            print(prompt, file=self.output_stream)
            print("Type 'yes' to proceed: ", end="", file=self.output_stream)
            self.output_stream.flush()
            return self.input_stream.readline().strip().lower() == "yes"

        print(f"{prompt} [y/N]: ", end="", file=self.output_stream)
        self.output_stream.flush()
        return self.input_stream.readline().strip().lower() in {"y", "yes"}
