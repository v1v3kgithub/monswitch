"""Discovery: assemble monitors and their inputs from every available source.

This is the core's main entry point. Everything below it is a parser or a wrapper; everything
above it is presentation. Both front-ends call this and render what it returns.

Per ADR-0001 there is no mandatory calibration step, so the input list is *synthesised* from
three sources of differing trustworthiness, each contributing a provenance tier (ADR-0009):

==================  ==============  ======================================================
Source              Tier            What it actually proves
==================  ==============  ======================================================
Config / ``learn``  ``CONFIRMED``   A human checked this code drives this port
``getvcp 60``       ``LIVE``        The code is real; the monitor is on it right now
``capabilities``    ``REPORTED``    The firmware admits it; existence unverified
==================  ==============  ======================================================

Every input comes from the monitor or from the user. ADR-0014 removed a fourth source -- a
built-in table of codes common across vendors -- because it manufactured rows for inputs the
panel had never mentioned, and those are the rows whose selection blanks the screen
unrecoverably.

Merging is ``max()`` on the tier, so a code seen live outranks one merely reported.

Two rules here are load-bearing rather than cosmetic:

**Firmware-unnamed codes are never given a name.** When ``capabilities`` lists a value as
"Unrecognized value" — ``1f`` on the reference panel — that value is *not* promoted to
``REPORTED`` and does *not* inherit a name from the fallback table, even though the table has
a plausible one. ADR-0009 requires it be carried as ``GUESSED`` and shown by its code alone.
The firmware saying "I don't know what this is" is evidence against the table, not for it.

**A failed live read does not hide the monitor.** It is listed, with ``host_input=None``, which
makes it unswitchable per ADR-0013 and surfaces as a warning rather than silence (ADR-0011).

A consequence worth knowing: a code seen only in the live read carries no name, because
nothing trustworthy supplied one. ``0x13`` on the reference panel therefore displays as
``0x13`` rather than "USB-C". That is the honest rendering — the name would have been a guess
about a vendor-specific code — and a config nickname is how a user attaches one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .ddc import Ddcutil, DdcutilError, DdcutilParseError, DetectedDisplay
from .edid import EdidIdentity
from .models import (
    InputSource,
    KeyedMonitor,
    Monitor,
    Provenance,
    assign_keys,
    identity_key,
    merge_inputs,
)
from .sysfs import DEFAULT_DRM_ROOT, resolve

__all__ = [
    "Discovery",
    "MonitorWarning",
    "discover",
]




@dataclass(frozen=True, slots=True)
class MonitorWarning:
    """Something a frontend must tell the user about a specific monitor.

    Carries the monitor's own label rather than an index, because ``--display N`` numbering is
    unstable and an index means nothing to a user (ADR-0010).
    """

    monitor: str
    message: str
    switchable: bool = True


@dataclass(frozen=True, slots=True)
class Discovery:
    """The result of a discovery pass."""

    monitors: tuple[KeyedMonitor, ...] = ()
    warnings: tuple[MonitorWarning, ...] = ()
    used_detect_fallback: bool = False
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def switchable(self) -> tuple[KeyedMonitor, ...]:
        return tuple(k for k in self.monitors if k.monitor.is_switchable)

    @property
    def is_empty(self) -> bool:
        return not self.monitors


def discover(
    ddcutil: Ddcutil | None = None,
    *,
    root: Path = DEFAULT_DRM_ROOT,
    include_capabilities: bool = False,
    confirmed_inputs: Mapping[str, Mapping[int, str]] | None = None,
    name_overrides: Mapping[str, str] | None = None,
) -> Discovery:
    """Find monitors and build their input lists.

    Args:
        ddcutil: the wrapper to read through. Injectable for testing.
        root: sysfs DRM root, for testing against a synthetic tree.
        include_capabilities: whether to consult ``capabilities``. Defaults to ``False``
            because it costs 3.1s and must stay off interactive paths (ADR-0010); ``list``
            opts in, a launcher keystroke does not.
        confirmed_inputs: user-confirmed code maps, keyed by identity key (ADR-0013). Supplied
            by the config layer; an empty mapping simply means nothing is confirmed yet.
        name_overrides: user-supplied monitor names, keyed by identity key.

    Never raises for a single unreadable monitor: the failure becomes a warning and the other
    monitors are still returned.
    """
    runner = ddcutil or Ddcutil()
    confirmed_by_key = confirmed_inputs or {}
    names_by_key = name_overrides or {}

    resolution = resolve(root)
    warnings: list[MonitorWarning] = []
    used_fallback = False

    if resolution.needs_fallback:
        used_fallback = True
        candidates = _candidates_from_detect(runner, warnings)
    else:
        candidates = [(m.identity, m.bus, m.connector) for m in resolution.monitors]

    # Keys are derived for every candidate before any override is applied, because a key
    # shared by two monitors must have its overrides withheld from both (ADR-0013). Applying
    # them first and detecting the collision afterwards would mean one panel's confirmed map
    # had already been attributed to another, which is the failure the rule exists to stop.
    keys = [identity_key(identity, connector) for identity, _, connector in candidates]
    colliding = {key for key in keys if keys.count(key) > 1}

    monitors: list[Monitor] = []
    for (identity, bus, connector), key in zip(candidates, keys):
        label = identity.model or f"{identity.manufacturer} on {connector}"
        host_input = _read_host_input(runner, bus, label, warnings)
        reported, firmware_unnamed = _read_capabilities(
            runner, bus, label, warnings, enabled=include_capabilities
        )

        trusted = key not in colliding
        found = _assemble_inputs(
            host_input=host_input,
            reported=reported,
            firmware_unnamed=firmware_unnamed,
            confirmed=confirmed_by_key.get(key, {}) if trusted else {},
        )

        monitors.append(
            Monitor(
                identity=identity,
                bus=bus,
                connector=connector,
                inputs=found,
                host_input=host_input,
                label=names_by_key.get(key) if trusted else None,
            )
        )

    keyed = assign_keys(monitors)

    # A colliding key means stored overrides were withheld (ADR-0013). Say so, because the
    # user sees unverified badges on a monitor they thought they had confirmed.
    for entry in keyed:
        if entry.collides:
            warnings.append(
                MonitorWarning(
                    monitor=entry.monitor.display_name,
                    message=(
                        f"two monitors share the identity {entry.key!r}, so saved names and "
                        "confirmed inputs were ignored for both. Give one a different "
                        "connector-based entry in the config to tell them apart"
                    ),
                )
            )

    return Discovery(
        monitors=keyed,
        warnings=tuple(warnings),
        used_detect_fallback=used_fallback,
        skipped=resolution.skipped,
    )


def _candidates_from_detect(
    runner: Ddcutil, warnings: list[MonitorWarning]
) -> list[tuple[EdidIdentity, int, str]]:
    """Build candidates from ``ddcutil detect`` when sysfs has no ``ddc`` links.

    The terse output carries no product code, so identity is assembled with ``product_code=0``.
    That only weakens the fallback identity key for a panel that *also* has no serial and no
    model, which is rare enough to accept over paying for a verbose second probe.
    """
    try:
        displays = runner.detect()
    except (DdcutilError, DdcutilParseError) as exc:
        warnings.append(
            MonitorWarning(
                monitor="(all)",
                message=f"could not list monitors: {exc}",
                switchable=False,
            )
        )
        return []

    candidates: list[tuple[EdidIdentity, int, str]] = []
    for display in displays:
        if display.bus is None:
            continue
        if not display.usable:
            warnings.append(
                MonitorWarning(
                    monitor=display.model or display.manufacturer or f"bus {display.bus}",
                    message="does not support DDC/CI input switching",
                    switchable=False,
                )
            )
            continue
        identity = EdidIdentity(
            manufacturer=display.manufacturer or "???",
            product_code=0,
            model=display.model,
            serial=display.serial,
        )
        candidates.append((identity, display.bus, display.connector or f"bus-{display.bus}"))
    return candidates


def _read_host_input(
    runner: Ddcutil, bus: int, label: str, warnings: list[MonitorWarning]
) -> int | None:
    """Read the Host Input, or record why it could not be read.

    A successful read is itself the proof that the returned value is the Host Input: DDC/CI
    rides the display link, so a monitor that answers is a monitor showing this machine
    (ADR-0013).
    """
    try:
        return runner.getvcp_input(bus)
    except (DdcutilError, DdcutilParseError) as exc:
        warnings.append(
            MonitorWarning(
                monitor=label,
                message=f"current input could not be read, so switching is disabled: {exc}",
                switchable=False,
            )
        )
        return None


def _read_capabilities(
    runner: Ddcutil,
    bus: int,
    label: str,
    warnings: list[MonitorWarning],
    *,
    enabled: bool,
) -> tuple[dict[int, str | None], set[int]]:
    """Read advertised inputs, returning named entries and the firmware-unnamed codes.

    The two are returned separately because they are treated differently: a named entry is
    ``REPORTED``, while a code the firmware could not name is held at ``GUESSED`` and denied a
    name from any other source (ADR-0009).
    """
    if not enabled:
        return {}, set()

    try:
        entries = runner.capabilities_inputs(bus)
    except (DdcutilError, DdcutilParseError) as exc:
        warnings.append(
            MonitorWarning(
                monitor=label,
                message=f"advertised inputs could not be read: {exc}",
            )
        )
        return {}, set()

    named: dict[int, str | None] = {}
    unnamed: set[int] = set()
    for entry in entries:
        if entry.name is None:
            unnamed.add(entry.code)
        else:
            named[entry.code] = entry.name
    return named, unnamed


def _assemble_inputs(
    *,
    host_input: int | None,
    reported: Mapping[int, str | None],
    firmware_unnamed: set[int],
    confirmed: Mapping[int, str],
) -> tuple[InputSource, ...]:
    """Merge every reported source into one tiered input list.

    Every input here was mentioned by the monitor itself or by the user. Nothing is added
    speculatively -- see ADR-0014 for why the built-in code table was removed.
    """
    found: list[InputSource] = []

    for code in firmware_unnamed:
        # Held at GUESSED, and given no name: the firmware explicitly disclaimed this value.
        found.append(InputSource(code=code, name=None, provenance=Provenance.GUESSED))

    for code, name in reported.items():
        found.append(InputSource(code=code, name=name, provenance=Provenance.REPORTED))

    if host_input is not None:
        found.append(
            InputSource(code=host_input, name=None, provenance=Provenance.LIVE)
        )

    for code, name in confirmed.items():
        found.append(InputSource(code=code, name=name, provenance=Provenance.CONFIRMED))

    merged = merge_inputs(found)

    # Re-suppress names on firmware-disclaimed codes: merging may have let one inherit a name
    # from the fallback table, which is the inversion ADR-0009 forbids. A user-confirmed name
    # still wins, because that is a human overriding the firmware deliberately.
    return tuple(
        source
        if source.code not in firmware_unnamed or source.provenance is Provenance.CONFIRMED
        else InputSource(code=source.code, name=None, provenance=source.provenance)
        for source in merged
    )
