"""Monitor resolution from sysfs — the primary discovery path (ADR-0010).

The kernel already publishes everything needed to find a monitor and its I²C bus, so this
costs no I²C traffic and no ``ddcutil`` invocation. Each DRM connector directory under
``/sys/class/drm`` carries:

* ``status`` — ``connected`` or ``disconnected``,
* ``ddc`` — a symlink to the I²C adapter that reaches that connector's DDC/CI channel,
* ``edid`` — the raw EDID, zero bytes when nothing is plugged in.

Measured at 0.02 s for the whole walk, against 1.5 s for ``ddcutil detect``. Because it is
read fresh every run there is nothing to cache and nothing to invalidate: a replug or reboot
is reflected immediately.

Not every driver cooperates. The ``ddc`` symlink comes from the kernel's ``drm_connector``
DDC plumbing, which ``i915`` and ``amdgpu`` populate but the NVIDIA proprietary driver does
not. :attr:`Resolution.ddc_links_present` tells the caller whether this path found any usable
link at all, which is the signal to fall back to ``ddcutil detect``.

This module does I/O but no subprocess and no writes. It never touches ``setvcp``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .edid import EdidError, EdidIdentity, parse

__all__ = [
    "DEFAULT_DRM_ROOT",
    "DetectedMonitor",
    "Resolution",
    "SkipReason",
    "resolve",
]

DEFAULT_DRM_ROOT = Path("/sys/class/drm")

# "card1-DP-1" -> card "card1", connector "DP-1". Connector names can contain hyphens
# ("HDMI-A-1"), so only the leading cardN- prefix is stripped.
_CONNECTOR_NAME = re.compile(r"^(card\d+)-(.+)$")

# The I2C adapter directory the `ddc` symlink points at, e.g. "i2c-16".
_I2C_ADAPTER = re.compile(r"^i2c-(\d+)$")

# Internal panels. eDP has no DDC/CI input switching, and LVDS/DSI are likewise built in.
_INTERNAL_PREFIXES = ("eDP", "LVDS", "DSI")


class SkipReason:
    """Why a connector was not usable as a switchable monitor.

    Plain string constants rather than an enum: these are passed straight to the user by the
    frontends (ADR-0011), and the set is open — a new driver quirk means a new reason, not a
    change to a closed type.
    """

    DISCONNECTED = "disconnected"
    INTERNAL_PANEL = "internal panel; no DDC/CI input switching"
    NO_DDC_LINK = "no ddc symlink; driver does not expose the I2C adapter"
    UNREADABLE_DDC_LINK = "ddc symlink does not name an i2c adapter"
    NO_EDID = "edid is empty; nothing plugged in"
    BAD_EDID = "edid could not be parsed"
    UNREADABLE = "sysfs attributes could not be read"


@dataclass(frozen=True, slots=True)
class DetectedMonitor:
    """An external monitor found via sysfs, with the bus needed to talk to it.

    ``bus`` is the I²C adapter number for ``ddcutil --bus N``. ``connector`` is the short DRM
    name (``DP-1``, ``HDMI-A-1``) — a run-local handle for display and logging, and a
    component of the identity key only when the EDID carries no serial (ADR-0013). It is not
    an identity on its own: it names a port, not a panel.
    """

    identity: EdidIdentity
    bus: int
    connector: str
    card: str

    @property
    def drm_name(self) -> str:
        """The full sysfs connector directory name, e.g. ``card1-DP-1``."""
        return f"{self.card}-{self.connector}"


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of a sysfs walk: what was found, and what was passed over and why."""

    monitors: tuple[DetectedMonitor, ...] = ()
    skipped: tuple[tuple[str, str], ...] = field(default=())
    root_present: bool = True
    ddc_links_seen: int = 0

    @property
    def ddc_links_present(self) -> bool:
        """Whether this driver publishes the connector-to-I²C mapping at all.

        Counted across every connector before any filtering, including disconnected ports and
        internal panels, because the question is about the *driver*, not about what happens to
        be plugged in. ``False`` means the mapping is unavailable here — the signal to fall
        back to ``ddcutil detect`` (ADR-0010).

        Deliberately distinct from "no monitors found". A machine with working links and
        nothing attached must not trigger a 1.5 s fallback probe to discover the same nothing.
        """
        return self.ddc_links_seen > 0 or bool(self.monitors)

    @property
    def needs_fallback(self) -> bool:
        """Whether the caller should try ``ddcutil detect`` instead."""
        return not self.root_present or not self.ddc_links_present


def resolve(root: Path = DEFAULT_DRM_ROOT) -> Resolution:
    """Find external, switchable monitors by walking DRM connectors under ``root``.

    ``root`` is injectable so the walk can be tested against a synthetic tree; production
    callers use the default.

    Never raises for an unusable connector — an unreadable or malformed one is recorded in
    :attr:`Resolution.skipped` and the walk continues. A machine with one broken connector
    should still switch the others.
    """
    if not root.is_dir():
        return Resolution(root_present=False)

    monitors: list[DetectedMonitor] = []
    skipped: list[tuple[str, str]] = []
    links_seen = 0

    for connector_dir in _connector_dirs(root):
        name = connector_dir.name
        match = _CONNECTOR_NAME.match(name)
        if match is None:
            continue
        card, connector = match.group(1), match.group(2)

        # Read the link first, and for every connector, so that ddc_links_present reflects
        # what the driver publishes rather than what is currently plugged in.
        bus = _read_bus(connector_dir)
        if bus is not None:
            links_seen += 1

        if connector.startswith(_INTERNAL_PREFIXES):
            skipped.append((name, SkipReason.INTERNAL_PANEL))
            continue

        try:
            status = (connector_dir / "status").read_text().strip()
        except OSError:
            skipped.append((name, SkipReason.UNREADABLE))
            continue

        if status != "connected":
            skipped.append((name, SkipReason.DISCONNECTED))
            continue

        if bus is None:
            link = connector_dir / "ddc"
            reason = (
                SkipReason.UNREADABLE_DDC_LINK
                if link.is_symlink()
                else SkipReason.NO_DDC_LINK
            )
            skipped.append((name, reason))
            continue

        try:
            raw = (connector_dir / "edid").read_bytes()
        except OSError:
            skipped.append((name, SkipReason.UNREADABLE))
            continue

        if not raw:
            skipped.append((name, SkipReason.NO_EDID))
            continue

        try:
            identity = parse(raw)
        except EdidError:
            skipped.append((name, SkipReason.BAD_EDID))
            continue

        monitors.append(
            DetectedMonitor(identity=identity, bus=bus, connector=connector, card=card)
        )

    return Resolution(
        monitors=tuple(monitors), skipped=tuple(skipped), ddc_links_seen=links_seen
    )


def _connector_dirs(root: Path) -> list[Path]:
    """Every DRM connector directory under ``root``, each listed once.

    Real sysfs publishes connectors twice: as flat symlinks directly in ``/sys/class/drm``
    (``card1-DP-1``) and nested under the card (``card1/card1-DP-1``). Both resolve to the
    same directory, so both globs are collected and deduplicated by resolved path.

    An earlier version used ``flat or nested``, which looked equivalent on this machine and
    was not: one flat match suppressed the nested scan entirely, so a second GPU whose
    connectors were only nested vanished silently. Prefer the union over the cheaper guess.
    """
    seen: dict[Path, Path] = {}
    for pattern in ("card*-*", "card*/card*-*"):
        for candidate in root.glob(pattern):
            if not candidate.is_dir():
                continue
            try:
                real = candidate.resolve()
            except OSError:
                real = candidate
            seen.setdefault(real, candidate)
    return sorted(seen.values(), key=lambda path: path.name)


def _read_bus(connector_dir: Path) -> int | None:
    """The I²C adapter number from the connector's ``ddc`` symlink, or ``None``.

    Read with :func:`os.readlink` rather than resolving the path: the link is relative
    (``ddc -> i2c-16``) and only its name is wanted, so this works without the target having
    to exist and without following it out of the tree.
    """
    link = connector_dir / "ddc"
    try:
        target = os.readlink(link)
    except OSError:
        return None

    match = _I2C_ADAPTER.match(Path(target).name)
    if match is None:
        return None
    return int(match.group(1))
