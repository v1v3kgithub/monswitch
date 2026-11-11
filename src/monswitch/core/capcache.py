"""A cache for ``ddcutil capabilities``, keyed by monitor identity.

ADR-0010 kept ``capabilities`` off interactive paths because it costs ~3.1s. ADR-0014 then
removed the built-in code table, which made ``capabilities`` the *only* source of inputs other
than the one currently live. Together those left the launcher showing a single row — the input
you are already on — which is useless. See ADR-0016.

The way out is that ``capabilities`` is a property of the panel's firmware, not of this
session: the same monitor answers the same way every time. So it is probed once per monitor and
remembered, keyed on the identity key of ADR-0013. The 3.1s is paid the first time you meet a
monitor and never again, which also suits someone who moves between desks: each monitor costs
one slow query, ever.

What is cached is only what the firmware *reported*, never a provenance decision. Cached
entries re-enter discovery as ``REPORTED`` exactly as a fresh probe would, so nothing here can
promote an input to verified. A stale cache can therefore cost an input a user has to confirm
by hand, or list one that no longer exists — both of which still face the ADR-0009 warning
before any switch.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CachedCapabilities",
    "CapabilitiesCache",
    "cache_path",
]

_VERSION = 1


def cache_path() -> Path:
    """Where the cache lives, honouring ``XDG_CACHE_HOME``."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "monswitch" / "capabilities.json"


@dataclass(frozen=True, slots=True)
class CachedCapabilities:
    """One monitor's reported input values.

    ``named`` holds codes the firmware gave a name to; ``unnamed`` holds codes it listed but
    could not name (``1f: Unrecognized value`` on the reference panel). They are kept apart
    because discovery treats them differently: ADR-0009 forbids ever showing an invented name
    for a code the firmware disclaimed.
    """

    named: Mapping[int, str]
    unnamed: tuple[int, ...]


class CapabilitiesCache:
    """A small JSON file mapping identity key to reported inputs.

    Every operation is best-effort. A cache is an optimisation, so a missing, unreadable or
    corrupt file behaves as a miss rather than an error: the worst outcome is paying for a
    probe that could have been skipped.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_path()
        self._entries: dict[str, CachedCapabilities] | None = None

    # -- reading ----------------------------------------------------------

    def _load(self) -> dict[str, CachedCapabilities]:
        if self._entries is not None:
            return self._entries

        self._entries = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._entries

        if not isinstance(raw, dict) or raw.get("version") != _VERSION:
            # A different version is not an error; it is simply not understood here.
            return self._entries

        monitors = raw.get("monitors")
        if not isinstance(monitors, dict):
            return self._entries

        for key, entry in monitors.items():
            parsed = _parse_entry(entry)
            if parsed is not None and isinstance(key, str):
                self._entries[key] = parsed
        return self._entries

    def get(self, key: str) -> CachedCapabilities | None:
        return self._load().get(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._load()

    # -- writing ----------------------------------------------------------

    def put(self, key: str, capabilities: CachedCapabilities) -> None:
        """Remember one monitor's reported inputs, and persist immediately.

        Written straight away rather than on exit: the launcher daemon may be killed at any
        point, and a probe paid for but not recorded would be paid again.
        """
        entries = self._load()
        entries[key] = capabilities
        self._save()

    def forget(self, key: str) -> None:
        entries = self._load()
        if entries.pop(key, None) is not None:
            self._save()

    def clear(self) -> None:
        self._entries = {}
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)

    def _save(self) -> None:
        entries = self._entries or {}
        document = {
            "version": _VERSION,
            "monitors": {
                key: {
                    "named": {f"0x{code:02x}": name for code, name in value.named.items()},
                    "unnamed": [f"0x{code:02x}" for code in value.unnamed],
                }
                for key, value in entries.items()
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Written via a temporary file in the same directory and renamed, so a crash or a
            # second process cannot leave a half-written cache behind.
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            # An unwritable cache directory must not break discovery.
            pass


def _parse_entry(entry: object) -> CachedCapabilities | None:
    if not isinstance(entry, dict):
        return None

    named: dict[int, str] = {}
    raw_named = entry.get("named")
    if isinstance(raw_named, dict):
        for code_text, name in raw_named.items():
            code = _parse_code(code_text)
            if code is not None and isinstance(name, str) and name:
                named[code] = name

    unnamed: list[int] = []
    raw_unnamed = entry.get("unnamed")
    if isinstance(raw_unnamed, list):
        for code_text in raw_unnamed:
            code = _parse_code(code_text)
            if code is not None:
                unnamed.append(code)

    if not named and not unnamed:
        return None
    return CachedCapabilities(named=named, unnamed=tuple(unnamed))


def _parse_code(text: object) -> int | None:
    if not isinstance(text, str):
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return value if 0 <= value <= 0xFF else None
