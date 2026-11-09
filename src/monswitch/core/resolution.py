"""Resolving a user's words into one input on one monitor.

Implements ADR-0006's lookup priority — nicknames, then built-in slugs, then raw codes — and
its rule that ambiguity is never resolved by guessing. Picking the wrong input here means
switching a monitor to the wrong port, which may be unrecoverable from software, so a query
that could mean two things resolves to *both* and lets the caller decide (ADR-0011).

The passes are tried in order and the first that matches anything wins, so a nickname always
beats a built-in slug of the same spelling. A user who named an input "dp" meant their input,
not DisplayPort-1.

Deliberately free of :func:`sys.stdin.isatty`. How to present a choice is the front-end's
business; this module only says what the possibilities are.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .models import InputSource, Monitor

__all__ = [
    "BUILTIN_SLUGS",
    "InputQueryResult",
    "resolve_input",
]


# Slugs the user can type, mapped to the codes they could mean. A family slug such as "dp"
# maps to several codes on purpose: ADR-0006 requires "dp" to be ambiguous when a monitor has
# both DisplayPort inputs, rather than silently picking the lower-numbered one.
BUILTIN_SLUGS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "dp": (0x0F, 0x10),
        "displayport": (0x0F, 0x10),
        "dp1": (0x0F,),
        "displayport1": (0x0F,),
        "dp2": (0x10,),
        "displayport2": (0x10,),
        "hdmi": (0x11, 0x12),
        "hdmi1": (0x11,),
        "hdmi2": (0x12,),
        "usbc": (0x13, 0x1B),
        "typec": (0x13, 0x1B),
        "usbc1": (0x13,),
        "usbc2": (0x1B,),
        "thunderbolt": (0x1B,),
        "tb": (0x1B,),
    }
)


@dataclass(frozen=True, slots=True)
class InputQueryResult:
    """What a query could have meant.

    ``matches`` is empty when nothing matched, holds one entry when the query is unambiguous,
    and holds several when the caller must choose. ``matched_by`` names the pass that produced
    it, so a front-end can explain *why* something matched.
    """

    query: str
    matches: tuple[InputSource, ...] = ()
    matched_by: str | None = None

    @property
    def is_unique(self) -> bool:
        return len(self.matches) == 1

    @property
    def is_ambiguous(self) -> bool:
        return len(self.matches) > 1

    @property
    def is_empty(self) -> bool:
        return not self.matches

    @property
    def only(self) -> InputSource:
        """The single match. Raises if there is not exactly one."""
        if not self.is_unique:
            raise ValueError(
                f"{self.query!r} matched {len(self.matches)} inputs, expected exactly one"
            )
        return self.matches[0]


def resolve_input(monitor: Monitor, query: str) -> InputQueryResult:
    """Find the input(s) on ``monitor`` that ``query`` could mean.

    Passes, in ADR-0006's order:

    1. **Nickname** — an exact match on an input's name, normalised. Covers both config
       nicknames and the names ``capabilities`` supplied.
    2. **Built-in slug** — ``dp1``, ``hdmi``, ``usbc`` and friends, filtered to codes this
       monitor actually offers.
    3. **Raw code** — ``0x13``, ``13``, or a decimal ``19``.
    4. **Prefix** — a partial nickname, as a convenience for launcher typing.

    Only codes present on the monitor are ever returned, so a query can never resolve to an
    input this panel does not offer.
    """
    needle = _normalise(query)
    if not needle:
        return InputQueryResult(query=query)

    for name, finder in (
        ("nickname", _by_nickname),
        ("slug", _by_slug),
        ("code", _by_code),
        ("prefix", _by_prefix),
    ):
        matches = finder(monitor.inputs, needle, query)
        if matches:
            return InputQueryResult(
                query=query, matches=tuple(matches), matched_by=name
            )

    return InputQueryResult(query=query)


def _normalise(text: str) -> str:
    """Lowercase and strip everything that is not a letter or digit.

    So ``"USB-C"``, ``"usb c"`` and ``"usbc"`` are the same query, and ``"DisplayPort-1"``
    normalises to ``"displayport1"``.
    """
    return re.sub(r"[^a-z0-9]", "", text.strip().lower())


def _by_nickname(
    inputs: Sequence[InputSource], needle: str, _raw: str
) -> list[InputSource]:
    return [
        source
        for source in inputs
        if source.name and _normalise(source.name) == needle
    ]


def _by_slug(inputs: Sequence[InputSource], needle: str, _raw: str) -> list[InputSource]:
    codes = BUILTIN_SLUGS.get(needle)
    if not codes:
        return []
    return [source for source in inputs if source.code in codes]


def _by_code(inputs: Sequence[InputSource], needle: str, raw: str) -> list[InputSource]:
    code = _parse_code(raw)
    if code is None:
        return []
    return [source for source in inputs if source.code == code]


def _by_prefix(inputs: Sequence[InputSource], needle: str, _raw: str) -> list[InputSource]:
    return [
        source
        for source in inputs
        if source.name and _normalise(source.name).startswith(needle)
    ]


def _parse_code(text: str) -> int | None:
    """Parse a user-typed input code.

    ``0x13`` is hex. A bare ``13`` is also hex, because that is how ``ddcutil capabilities``
    and this project's docs print codes, and it is what a user will copy. A decimal reading is
    attempted only when the text cannot be hex at all, which in practice means nothing, so
    the rule stays predictable: **codes are hex**.
    """
    cleaned = text.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if not cleaned or len(cleaned) > 2:
        return None
    try:
        value = int(cleaned, 16)
    except ValueError:
        return None
    return value if 0 <= value <= 0xFF else None
