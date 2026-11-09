"""Parsers for ``ddcutil`` output.

Pure string-in, data-out. The subprocess wrapper that actually runs ``ddcutil`` lands here
later; keeping the parsing separable is what lets the whole matrix of real-world output be
tested from captured fixtures, with no hardware and no writes.

Three things get parsed, all read-only commands:

* ``getvcp 60`` — the current input, in either output format.
* ``capabilities`` — the input values the firmware admits to.
* ``detect`` — the fallback discovery path for drivers that do not publish the sysfs ``ddc``
  link (ADR-0010). ``--terse`` is the documented parse target, but the verbose form is
  accepted too since both are cheap to support.

Two quirks drive the design, both verified on the reference HP Z38c and recorded in the
agent brief:

``getvcp 60`` prints ``Invalid value (sl=0x13)`` for vendor-specific codes. That is **not** an
error. ``0x13`` is the monitor's real current input; ddcutil simply has no name for it. The
``sl=`` value is authoritative and the verdict beside it is noise.

``capabilities`` misreports in both directions — it omits ``0x13`` entirely while listing
``1f: Unrecognized value``, which is not a usable input. These parsers stay faithful to what
ddcutil said and do not editorialise: an unnamed value is returned with ``name=None`` so the
discovery merge can tier it per ADR-0009. Deciding what an unnamed code means is not parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CapabilityInput",
    "DdcutilParseError",
    "DetectedDisplay",
    "INPUT_SOURCE_FEATURE",
    "parse_capabilities_inputs",
    "parse_detect",
    "parse_getvcp_input",
]

# MCCS VCP feature code for Input Source. The only feature this tool ever writes.
INPUT_SOURCE_FEATURE = 0x60

# Non-terse: "VCP code 0x60 (Input Source ): Invalid value (sl=0x13)"
# also        "VCP code 0x60 (Input Source ): DisplayPort-1 (sl=0x0f)"
_SL_VALUE = re.compile(r"\bsl\s*=\s*0x([0-9a-fA-F]{1,2})\b")

# Terse: "VCP 60 SNC x13" — opcode, value type, then the value with a bare x prefix.
_TERSE_VCP = re.compile(
    r"^\s*VCP\s+(?P<code>[0-9a-fA-F]{2})\s+(?P<type>\w+)\s+x(?P<value>[0-9a-fA-F]{1,2})\s*$",
    re.MULTILINE,
)

# "   Feature: 60 (Input Source)"
_FEATURE_HEADER = re.compile(r"^\s*Feature:\s*([0-9a-fA-F]{2})\b")

# "         0f: DisplayPort-1"
_VALUE_LINE = re.compile(r"^\s*([0-9a-fA-F]{2}):\s*(.*?)\s*$")

# "      Values: 00 01 02 03 (interpretation unavailable)"
_INLINE_VALUES = re.compile(r"^\s*Values:\s*(?P<values>(?:[0-9a-fA-F]{2}\s+)*[0-9a-fA-F]{2})\s*(?P<note>\(.*\))?\s*$")

_VALUES_HEADER = re.compile(r"^\s*Values:\s*$")

# ddcutil's own words for "I have no name for this value".
_UNNAMED_MARKERS = ("unrecognized value", "interpretation unavailable", "unrecognized")

# "Display 1" / "Invalid display"
_DISPLAY_HEADER = re.compile(r"^(?P<invalid>Invalid display)|^Display\s+(?P<number>\d+)\s*$")

_I2C_BUS = re.compile(r"^\s*I2C bus:\s*(?:/dev/i2c-)?(\d+)\s*$")
_DRM_CONNECTOR = re.compile(r"^\s*DRM connector:\s*(\S+)\s*$")

# Terse form: "   Monitor:          HPN:HP Z38c:DEMOSERIAL"
_MONITOR_TRIPLE = re.compile(r"^\s*Monitor:\s*(.*)$")

# Verbose form, inside "EDID synopsis:". Mfg id carries a " - UNK" suffix when ddcutil
# cannot look the PNP ID up, which is cosmetic and stripped.
_MFG_ID = re.compile(r"^\s*Mfg id:\s*(.*?)\s*$")
_MODEL = re.compile(r"^\s*Model:\s*(.*?)\s*$")
_SERIAL = re.compile(r"^\s*Serial number:\s*(.*?)\s*$")


class DdcutilParseError(ValueError):
    """Raised when ``ddcutil`` output cannot be understood.

    Distinct from ddcutil *failing*: a non-zero exit or a DDC timeout is the subprocess
    wrapper's concern. This means the command appeared to succeed but said something this
    parser does not recognise, which usually means a ddcutil version difference.
    """


@dataclass(frozen=True, slots=True)
class CapabilityInput:
    """One input value as reported by ``capabilities``.

    ``name`` is ``None`` when ddcutil had no name for the value (``1f: Unrecognized value``).
    Such a value is reported, not dropped, so the discovery merge can decide its provenance
    tier — but it must never be shown to a user with an invented name (ADR-0009).
    """

    code: int
    name: str | None

    @property
    def is_named(self) -> bool:
        return self.name is not None


@dataclass(frozen=True, slots=True)
class DetectedDisplay:
    """One display block from ``ddcutil detect``.

    ``usable`` is ``False`` for a block ddcutil labelled "Invalid display" — on the reference
    machine, the eDP laptop panel, which has no DDC/CI. Such blocks are returned rather than
    dropped so a caller can explain why a monitor it can see is not switchable (ADR-0011).
    """

    bus: int | None
    connector: str | None
    manufacturer: str | None
    model: str | None
    serial: str | None
    display_number: int | None
    usable: bool


def parse_getvcp_input(text: str) -> int:
    """Extract the current Input Source code from ``getvcp 60`` output.

    Handles both the terse form (``VCP 60 SNC x13``) and the verbose one
    (``VCP code 0x60 (Input Source ): Invalid value (sl=0x13)``). The "Invalid value" verdict
    is ignored by design: the ``sl=`` value is the monitor's real current input.

    Raises:
        DdcutilParseError: if no input value can be found in the text.
    """
    match = _SL_VALUE.search(text)
    if match is not None:
        return int(match.group(1), 16)

    for terse in _TERSE_VCP.finditer(text):
        if int(terse.group("code"), 16) == INPUT_SOURCE_FEATURE:
            return int(terse.group("value"), 16)

    raise DdcutilParseError(
        f"no input source value found in getvcp output: {text.strip()!r}"
    )


def parse_capabilities_inputs(text: str) -> tuple[CapabilityInput, ...]:
    """Extract the Input Source values from ``capabilities`` output.

    Returns an empty tuple when the monitor reports no feature ``60`` at all, which is a
    legitimate answer and not an error — it means this parser found nothing to trust, and
    discovery must fall back to the live value and the known-code table (ADR-0001).

    Remember that this list is unreliable in both directions: it omitted ``0x13`` on the
    reference panel while listing junk. It is a hint, never the truth.
    """
    inputs: list[CapabilityInput] = []
    in_feature = False
    in_values_block = False

    for line in text.splitlines():
        header = _FEATURE_HEADER.match(line)
        if header is not None:
            # Any Feature: line ends the previous feature's value block.
            in_feature = int(header.group(1), 16) == INPUT_SOURCE_FEATURE
            in_values_block = False
            continue

        if not in_feature:
            continue

        inline = _INLINE_VALUES.match(line)
        if inline is not None:
            # The inline form is a bare hex list ("Values: 00 01 02 03"), with no names at
            # all -- ddcutil emits it when it cannot interpret the feature. Every value is
            # therefore unnamed, whatever the trailing note says.
            for token in inline.group("values").split():
                inputs.append(CapabilityInput(code=int(token, 16), name=None))
            continue

        if _VALUES_HEADER.match(line):
            in_values_block = True
            continue

        if not in_values_block:
            continue

        value = _VALUE_LINE.match(line)
        if value is None:
            # Dedented out of the value list without hitting a new Feature: line.
            in_values_block = False
            continue

        name = value.group(2).strip()
        if any(marker in name.lower() for marker in _UNNAMED_MARKERS) or not name:
            name = None
        inputs.append(CapabilityInput(code=int(value.group(1), 16), name=name))

    return tuple(inputs)


def parse_detect(text: str) -> tuple[DetectedDisplay, ...]:
    """Parse ``ddcutil detect`` output, terse or verbose.

    This is the fallback discovery path (ADR-0010); sysfs is preferred where the driver
    publishes ``ddc`` links. Blocks that ddcutil marked "Invalid display" are returned with
    ``usable=False`` rather than discarded.
    """
    displays: list[DetectedDisplay] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        if current is None:
            return
        displays.append(
            DetectedDisplay(
                bus=current.get("bus"),  # type: ignore[arg-type]
                connector=current.get("connector"),  # type: ignore[arg-type]
                manufacturer=current.get("manufacturer"),  # type: ignore[arg-type]
                model=current.get("model"),  # type: ignore[arg-type]
                serial=current.get("serial"),  # type: ignore[arg-type]
                display_number=current.get("display_number"),  # type: ignore[arg-type]
                usable=bool(current.get("usable", True)),
            )
        )

    for line in text.splitlines():
        header = _DISPLAY_HEADER.match(line)
        if header is not None:
            flush()
            current = {
                "usable": header.group("invalid") is None,
                "display_number": int(header.group("number"))
                if header.group("number")
                else None,
            }
            continue

        if current is None:
            continue

        bus = _I2C_BUS.match(line)
        if bus is not None:
            current["bus"] = int(bus.group(1))
            continue

        connector = _DRM_CONNECTOR.match(line)
        if connector is not None:
            current["connector"] = connector.group(1)
            continue

        monitor = _MONITOR_TRIPLE.match(line)
        if monitor is not None:
            manufacturer, model, serial = _split_monitor_triple(monitor.group(1))
            current["manufacturer"] = manufacturer
            current["model"] = model
            current["serial"] = serial
            continue

        mfg = _MFG_ID.match(line)
        if mfg is not None:
            current["manufacturer"] = _clean_mfg_id(mfg.group(1))
            continue

        model_line = _MODEL.match(line)
        if model_line is not None:
            current["model"] = model_line.group(1) or None
            continue

        serial_line = _SERIAL.match(line)
        if serial_line is not None:
            current["serial"] = serial_line.group(1) or None
            continue

    flush()
    return tuple(displays)


def _split_monitor_triple(raw: str) -> tuple[str | None, str | None, str | None]:
    """Split the terse ``Monitor:`` field, e.g. ``HPN:HP Z38c:DEMOSERIAL``.

    Fields are often empty (``LGD::`` for the laptop panel), which becomes ``None`` rather
    than an empty string so it matches what the EDID parser reports for the same panel.
    """
    parts = raw.strip().split(":")
    parts += [""] * (3 - len(parts))
    manufacturer, model, serial = (part.strip() or None for part in parts[:3])
    return manufacturer, model, serial


def _clean_mfg_id(raw: str) -> str | None:
    """Strip ddcutil's ``" - UNK"`` lookup suffix from a verbose ``Mfg id:`` value."""
    value = raw.split(" - ")[0].strip()
    return value or None
