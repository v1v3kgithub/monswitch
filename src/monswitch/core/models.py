"""Domain models: monitors, input sources, provenance, and the confirmation policy.

Pure data and pure decisions. No I/O, no subprocess, no config reading — this module is what
the discovery merge, the CLI and both graphical front-ends all agree about.

Three things live here:

**Provenance** (ADR-0009). Because the input list is synthesised from sources of varying
trustworthiness, every :class:`InputSource` records where it came from. The tiers exist so
that the irreversible action can be gated in proportion to how much is actually known about
its destination.

**Identity keys** (ADR-0013). A monitor is keyed on its EDID serial where it has one, and on
``(manufacturer, model, connector)`` where it does not. Two monitors that collide on a key
keep their overrides *withheld* rather than shared, because sharing would apply one panel's
confirmed input map to another and so present guessed codes as verified.

**Confirmation policy** (ADR-0002, ADR-0009, ADR-0013). One pure function decides what a
switch requires. Keeping it here, rather than in each front-end, is what stops the CLI, the
picker and the launcher from drifting into three different ideas of what is safe.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import Enum, IntEnum, unique

from .edid import EdidIdentity

__all__ = [
    "Confirmation",
    "InputSource",
    "KeyedMonitor",
    "Monitor",
    "Provenance",
    "assign_keys",
    "identity_key",
    "merge_inputs",
    "switch_confirmation",
]


@unique
class Provenance(IntEnum):
    """Where an input's existence and name came from, ordered by trustworthiness.

    Ordered so that merging sources is ``max()``: a code seen live outranks one merely
    reported, and a user-confirmed entry outranks everything. The numeric values are an
    implementation detail of that ordering and are not persisted anywhere.
    """

    GUESSED = 0
    """Present only in the built-in fallback code table. May not exist on this panel."""

    REPORTED = 1
    """Listed by ``capabilities`` with a recognised name. Plausible, unverified."""

    LIVE = 2
    """Observed as the current value of ``getvcp 60``. Definitely real."""

    CONFIRMED = 3
    """Confirmed by the user, via ``learn`` or an explicit config entry."""

    @property
    def is_verified(self) -> bool:
        """Whether this tier is strong enough to switch to without an extra warning.

        ``LIVE`` and ``CONFIRMED`` are facts about *this* panel. ``REPORTED`` and ``GUESSED``
        are not, and ADR-0009 requires them to prompt even when the user has opted out of
        confirmation.
        """
        return self >= Provenance.LIVE


@dataclass(frozen=True, slots=True)
class InputSource:
    """One selectable input on a monitor.

    ``name`` is ``None`` when nothing trustworthy supplied one. A ``None`` name must never be
    replaced with a guess for display purposes; show the raw code instead (ADR-0009).
    """

    code: int
    name: str | None
    provenance: Provenance

    @property
    def is_verified(self) -> bool:
        return self.provenance.is_verified

    @property
    def hex_code(self) -> str:
        """The code as it appears in ddcutil output and config files, e.g. ``0x13``."""
        return f"0x{self.code:02x}"

    @property
    def label(self) -> str:
        """Human-facing text: the name when there is one, else the bare code."""
        return self.name or self.hex_code

    def merged_with(self, other: InputSource) -> InputSource:
        """Combine two findings about the same code, keeping the stronger provenance.

        The name follows the winning provenance, except that a winner with no name inherits
        the loser's name — a code seen live but unnamed is still better described by the name
        ``capabilities`` gave it than by nothing.
        """
        if other.code != self.code:
            raise ValueError(f"cannot merge {self.hex_code} with {other.hex_code}")

        stronger, weaker = (self, other) if self.provenance >= other.provenance else (other, self)
        if stronger.name is None and weaker.name is not None:
            return replace(stronger, name=weaker.name)
        return stronger


@dataclass(frozen=True, slots=True)
class Monitor:
    """An external monitor, its inputs, and which input this machine is driving.

    ``host_input`` is the code of the Host Input, derived from a successful live read — never
    stored and never guessed (ADR-0013). ``None`` means the live read did not succeed, in
    which case the monitor may be listed but must not be switched.
    """

    identity: EdidIdentity
    bus: int
    connector: str
    inputs: tuple[InputSource, ...] = ()
    host_input: int | None = None
    label: str | None = None
    """A user-supplied name from config, which wins over the EDID model if set."""

    @property
    def is_switchable(self) -> bool:
        """Whether a switch may be attempted at all.

        Without a known Host Input there is no way to tell whether a switch would cut the
        display link, so ADR-0013 fails closed rather than proceeding unwarned.
        """
        return self.host_input is not None

    @property
    def display_name(self) -> str:
        """Short human-facing name.

        Prefers the user's configured name, then the EDID model, then manufacturer plus
        connector. The config name wins because a user who bothered to name a monitor
        knows better than its firmware what they call it.
        """
        if self.label:
            return self.label
        if self.identity.model:
            return self.identity.model
        return f"{self.identity.manufacturer} on {self.connector}"

    def input_for(self, code: int) -> InputSource | None:
        for source in self.inputs:
            if source.code == code:
                return source
        return None

    @property
    def host_input_source(self) -> InputSource | None:
        return None if self.host_input is None else self.input_for(self.host_input)


def identity_key(identity: EdidIdentity, connector: str) -> str:
    """The config key for a monitor, per ADR-0013's fallback chain.

    Prefers the EDID serial. Falls back to ``manufacturer:model:connector`` when the panel
    reports no serial, using the product code when it has no model name either. The connector
    appears only in the fallback form, and never alone: it names a port, so a connector-only
    key would transfer one panel's confirmed input map to whatever is plugged in next.

    The result is used verbatim as a TOML table key, so it is kept readable (ADR-0007).
    """
    if identity.serial:
        return identity.serial
    model = identity.model or f"product-{identity.product_code}"
    return f"{identity.manufacturer}:{model}:{connector}"


@dataclass(frozen=True, slots=True)
class KeyedMonitor:
    """A monitor paired with its config key, and whether that key may be trusted.

    ``overrides_allowed`` is ``False`` when another connected monitor resolved to the same
    key. Both such monitors stay listed and switchable, but neither reads its stored
    overrides: a shared key means a confirmed input map cannot be attributed to one panel, and
    applying it to the wrong one would present guessed codes as verified (ADR-0013).
    """

    monitor: Monitor
    key: str
    overrides_allowed: bool = True

    @property
    def collides(self) -> bool:
        return not self.overrides_allowed


def assign_keys(monitors: Iterable[Monitor]) -> tuple[KeyedMonitor, ...]:
    """Pair each monitor with its identity key, withholding overrides on collision.

    Collisions are only possible among serial-less panels, since a serial is unique by
    construction — but two identical serial-less panels on different ports *do* produce
    distinct keys thanks to the connector component, so a genuine collision means two panels
    on the same connector name, i.e. two cards. Rare, and still handled rather than assumed
    away.
    """
    ordered = list(monitors)
    keys = [identity_key(m.identity, m.connector) for m in ordered]
    duplicated = {key for key in keys if keys.count(key) > 1}

    return tuple(
        KeyedMonitor(monitor=monitor, key=key, overrides_allowed=key not in duplicated)
        for monitor, key in zip(ordered, keys, strict=True)
    )


@unique
class Confirmation(Enum):
    """What a requested switch requires before it may proceed."""

    NONE = "none"
    """Proceed. Either a no-op, or a verified destination with confirmation opted out of."""

    LEAVING_HOST = "leaving_host"
    """Confirm: this switches the monitor away from this machine and cannot be undone here."""

    UNVERIFIED_DESTINATION = "unverified_destination"
    """Confirm with the stronger warning: the destination input was inferred, not verified.

    Raised even when the user has set ``confirm_switch = false``, because that opt-out covers
    inputs they switch between daily — which are verified by construction — and is not an
    assertion that guessed codes are safe (ADR-0009).
    """

    BLOCKED = "blocked"
    """Refuse: the Host Input is unknown, so the consequences cannot be assessed."""

    @property
    def requires_prompt(self) -> bool:
        return self in (Confirmation.LEAVING_HOST, Confirmation.UNVERIFIED_DESTINATION)


def switch_confirmation(
    monitor: Monitor,
    destination: InputSource,
    *,
    confirm_switch: bool = True,
) -> Confirmation:
    """Decide what switching ``monitor`` to ``destination`` requires.

    This is the safety spine of the whole tool, so the ordering of the checks matters:

    1. No known Host Input → ``BLOCKED``. Fail closed (ADR-0013).
    2. Unverified destination → ``UNVERIFIED_DESTINATION``, regardless of ``confirm_switch``
       (ADR-0009).
    3. Destination already is the Host Input → ``NONE``. Nothing changes.
    4. Otherwise the switch leaves the Host Input → ``LEAVING_HOST``, unless the user opted
       out (ADR-0002).

    ``confirm_switch`` is the *user's* setting. It has no bearing on whether an agent may
    issue the write: that always requires explicit human confirmation in the moment.
    """
    if monitor.host_input is None:
        return Confirmation.BLOCKED

    if not destination.is_verified:
        return Confirmation.UNVERIFIED_DESTINATION

    if destination.code == monitor.host_input:
        return Confirmation.NONE

    return Confirmation.LEAVING_HOST if confirm_switch else Confirmation.NONE


def merge_inputs(sources: Sequence[InputSource]) -> tuple[InputSource, ...]:
    """Collapse findings about the same codes, keeping the strongest provenance for each.

    Ordered by code so output is stable regardless of discovery order.
    """
    merged: dict[int, InputSource] = {}
    for source in sources:
        existing = merged.get(source.code)
        merged[source.code] = source if existing is None else existing.merged_with(source)
    return tuple(merged[code] for code in sorted(merged))
