"""Optional user configuration.

Per ADR-0001 the tool works with no configuration at all, so **every** part of this is
optional and a missing file is a normal, silent outcome rather than an error. Config exists
only to override: friendly monitor names, input nicknames, confirmed input codes, and the
confirmation preference.

Schema (ADR-0007, as amended by ADR-0013)::

    confirm_switch = true

    [monitors."DEMOSERIAL"]
    name = "HP UltraWide"

    [monitors."DEMOSERIAL".inputs]
    "0x13" = "Work Laptop"
    "0x0f" = "Desktop PC"

The table key is the monitor's identity key: its EDID serial, or
``manufacturer:model:connector`` for a panel that reports no serial. :func:`identity_key`
derives it, and ``monswitch list`` prints it.

``host_input`` is deliberately **not** part of this schema. A successful ``getvcp 60`` read
proves the value it returns is the Host Input, so storing it would add a second source that
can go stale and be trusted by the confirmation logic (ADR-0013).

Input codes in this file are hexadecimal, with or without the ``0x`` prefix — ``"0x13"`` and
``"13"`` are the same input. They are never decimal, because every other place a user meets
these codes (``ddcutil capabilities``, ``getvcp``, this project's docs) writes them in hex.

Anything listed under ``[monitors.<key>.inputs]`` is treated as **confirmed**: the user is
asserting that the code drives a real port on that panel, which promotes it past the warnings
of ADR-0009. That is the point of the table, and the reason a typo in it is worth warning
about rather than ignoring.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

__all__ = [
    "Config",
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "MonitorConfig",
    "config_path",
    "load",
    "load_tolerant",
]

_EMPTY: Mapping[int, str] = MappingProxyType({})

_KNOWN_ROOT_KEYS = frozenset({"confirm_switch", "monitors"})
_KNOWN_MONITOR_KEYS = frozenset({"name", "inputs"})


class ConfigError(Exception):
    """The config file exists but could not be used.

    Carries a message naming the offending key where possible, since the user is expected to
    fix the file by hand (ADR-0007).
    """


def config_path() -> Path:
    """Where the config lives, honouring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "monswitch" / "config.toml"


DEFAULT_CONFIG_PATH = "~/.config/monswitch/config.toml"
"""Human-facing path, for messages. Use :func:`config_path` for the real one."""


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    """Overrides for one monitor."""

    name: str | None = None
    inputs: Mapping[int, str] = field(default=_EMPTY)


@dataclass(frozen=True, slots=True)
class Config:
    """The whole configuration. All defaults, when there is no file."""

    confirm_switch: bool = True
    monitors: Mapping[str, MonitorConfig] = field(default=MappingProxyType({}))
    path: Path | None = None
    exists: bool = False

    def for_monitor(self, key: str) -> MonitorConfig:
        """Overrides for ``key``, or empty defaults if it has none."""
        return self.monitors.get(key, MonitorConfig())

    def confirmed_inputs(self) -> Mapping[str, Mapping[int, str]]:
        """The confirmed code maps, shaped for :func:`monswitch.core.discovery.discover`."""
        return MappingProxyType(
            {key: entry.inputs for key, entry in self.monitors.items() if entry.inputs}
        )

    def name_overrides(self) -> Mapping[str, str]:
        """User-supplied monitor names, keyed by identity key."""
        return MappingProxyType(
            {key: entry.name for key, entry in self.monitors.items() if entry.name}
        )


def load(path: Path | None = None) -> Config:
    """Read the config, or return defaults when there is no file.

    A missing file is not an error: the tool is zero-config by design (ADR-0001).

    Raises:
        ConfigError: if the file exists but is malformed or has an unusable value.
    """
    target = path or config_path()

    try:
        raw_bytes = target.read_bytes()
    except FileNotFoundError:
        return Config(path=target, exists=False)
    except OSError as exc:
        raise ConfigError(f"could not read {target}: {exc}") from exc

    try:
        raw = tomllib.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{target} is not valid UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{target} is not valid TOML: {exc}") from exc

    warnings: list[str] = []
    config = _build(raw, target, warnings)
    if warnings:
        raise ConfigError("; ".join(warnings))
    return config


def load_tolerant(path: Path | None = None) -> tuple[Config, tuple[str, ...]]:
    """Read the config, degrading to defaults rather than failing.

    Returns the config and any complaints about it. A broken config must not stop a user
    listing their monitors — they may well need ``monswitch list`` to find the identity key
    that will fix the file. The caller renders the warnings (ADR-0011).

    Unusable *parts* are dropped individually, so one bad input code does not discard a whole
    monitor's overrides.
    """
    target = path or config_path()

    try:
        raw_bytes = target.read_bytes()
    except FileNotFoundError:
        return Config(path=target, exists=False), ()
    except OSError as exc:
        return Config(path=target, exists=False), (f"could not read {target}: {exc}",)

    try:
        raw = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return (
            Config(path=target, exists=True),
            (f"ignoring {target}: {exc}",),
        )

    warnings: list[str] = []
    config = _build(raw, target, warnings)
    return config, tuple(warnings)


def _build(raw: object, target: Path, warnings: list[str]) -> Config:
    """Validate a parsed TOML document into a :class:`Config`, collecting complaints."""
    if not isinstance(raw, dict):
        warnings.append(f"{target}: expected a table at the top level")
        return Config(path=target, exists=True)

    for key in raw:
        if key not in _KNOWN_ROOT_KEYS:
            warnings.append(f"unknown setting {key!r} (expected one of: confirm_switch, monitors)")

    confirm_switch = True
    if "confirm_switch" in raw:
        value = raw["confirm_switch"]
        if isinstance(value, bool):
            confirm_switch = value
        else:
            warnings.append(
                f"confirm_switch must be true or false, got {value!r}; using true"
            )

    monitors: dict[str, MonitorConfig] = {}
    raw_monitors = raw.get("monitors", {})
    if not isinstance(raw_monitors, dict):
        warnings.append("[monitors] must be a table")
        raw_monitors = {}

    for key, entry in raw_monitors.items():
        if not isinstance(entry, dict):
            warnings.append(f"[monitors.{key!r}] must be a table")
            continue
        monitors[key] = _build_monitor(key, entry, warnings)

    return Config(
        confirm_switch=confirm_switch,
        monitors=MappingProxyType(monitors),
        path=target,
        exists=True,
    )


def _build_monitor(key: str, entry: Mapping[str, object], warnings: list[str]) -> MonitorConfig:
    for field_name in entry:
        if field_name not in _KNOWN_MONITOR_KEYS:
            warnings.append(
                f"unknown setting {field_name!r} under [monitors.{key!r}] "
                "(expected one of: name, inputs)"
            )

    name = None
    if "name" in entry:
        value = entry["name"]
        if isinstance(value, str) and value.strip():
            name = value.strip()
        else:
            warnings.append(f"[monitors.{key!r}] name must be a non-empty string")

    inputs: dict[int, str] = {}
    raw_inputs = entry.get("inputs", {})
    if not isinstance(raw_inputs, dict):
        warnings.append(f"[monitors.{key!r}.inputs] must be a table")
        raw_inputs = {}

    for code_text, label in raw_inputs.items():
        code = _parse_code(code_text)
        if code is None:
            warnings.append(
                f"[monitors.{key!r}.inputs] {code_text!r} is not a hex input code "
                'like "0x13"'
            )
            continue
        if not isinstance(label, str) or not label.strip():
            warnings.append(
                f"[monitors.{key!r}.inputs] {code_text!r} must map to a non-empty name"
            )
            continue
        inputs[code] = label.strip()

    return MonitorConfig(name=name, inputs=MappingProxyType(inputs))


def _parse_code(text: object) -> int | None:
    """Parse a config input code. Always hex; ``0x`` prefix optional."""
    if not isinstance(text, str):
        return None
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
