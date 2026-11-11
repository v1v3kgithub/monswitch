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
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "INPUT_SOURCE_FEATURE",
    "BusNotFound",
    "CapabilityInput",
    "CommandResult",
    "Ddcutil",
    "DdcutilError",
    "DdcutilFailed",
    "DdcutilNotInstalled",
    "DdcutilParseError",
    "DdcutilTimeout",
    "DetectedDisplay",
    "I2cPermissionDenied",
    "NoMonitorOnBus",
    "Runner",
    "SetvcpFailed",
    "WriteCapableDdcutil",
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
_INLINE_VALUES = re.compile(
    r"^\s*Values:\s*(?P<values>(?:[0-9a-fA-F]{2}\s+)*[0-9a-fA-F]{2})\s*(?P<note>\(.*\))?\s*$"
)

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

    raise DdcutilParseError(f"no input source value found in getvcp output: {text.strip()!r}")


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
                "display_number": int(header.group("number")) if header.group("number") else None,
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


# ---------------------------------------------------------------------------
# Subprocess wrapper
#
# Everything above is pure parsing. Everything below actually runs ddcutil.
#
# Read-only by construction: this wrapper exposes detect, capabilities and
# getvcp, and has no write method at all. That omission is deliberate and
# load-bearing -- code holding a Ddcutil cannot change a monitor, by accident
# or otherwise. Writing requires the WriteCapableDdcutil subclass at the foot
# of this module, which has to be asked for by name.
# ---------------------------------------------------------------------------


class DdcutilError(Exception):
    """Base for failures of the ``ddcutil`` command itself.

    Separate from :class:`DdcutilParseError`: these mean the command did not give a usable
    answer, rather than that its output was unrecognisable. Each subclass carries enough
    context for a frontend to render an actionable message (ADR-0011).
    """


class DdcutilNotInstalled(DdcutilError):
    """The ``ddcutil`` executable could not be found on ``PATH``."""


class DdcutilTimeout(DdcutilError):
    """``ddcutil`` did not finish within the allotted time."""


class I2cPermissionDenied(DdcutilError):
    """The I²C device node could not be opened: the user lacks access.

    The actionable fix is one of the three routes in the README — a ``uaccess`` ACL on the
    local seat, ``i2c`` group membership, or a scoped sudoers rule.
    """


class BusNotFound(DdcutilError):
    """No such I²C bus. Usually a stale bus number; re-resolve rather than retrying."""


class NoMonitorOnBus(DdcutilError):
    """The bus exists and is readable, but no monitor answered DDC/CI on it.

    Expected for a disconnected port, and for a panel with DDC/CI switched off in its OSD —
    which is the first thing to suggest when a monitor is visibly present.
    """


class DdcutilFailed(DdcutilError):
    """``ddcutil`` exited non-zero for a reason not matched by a more specific subclass."""


# ddcutil 1.4.1 exits 1 for every failure, so the exit code alone cannot distinguish a
# permission problem from an absent monitor. The distinction has to come from the message.
# Order matters: a permission failure also prints "No monitor detected", so the more specific
# pattern must be tested first.
_PERMISSION_MARKERS = ("permission denied", "eacces")
_BUS_NOT_FOUND = re.compile(r"\bBus\s+\S+\s+not found\b", re.IGNORECASE)
_NO_MONITOR = re.compile(r"\bNo monitor detected\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The raw outcome of one ``ddcutil`` invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """stdout and stderr together.

        ddcutil writes diagnostics to both depending on the message, so classification and
        parsing both read the combined stream.
        """
        return f"{self.stdout}\n{self.stderr}" if self.stderr else self.stdout


Runner = Callable[[Sequence[str], float], CommandResult]
"""How a command is actually executed. Injectable so the wrapper is testable without hardware."""


def _subprocess_runner(argv: Sequence[str], timeout: float) -> CommandResult:
    """Default runner: a plain, non-shell subprocess call."""
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DdcutilNotInstalled(
            f"{argv[0]!r} not found on PATH; install ddcutil (see README)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DdcutilTimeout(f"{' '.join(argv)} did not finish within {timeout:g}s") from exc

    return CommandResult(
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


@dataclass(frozen=True, slots=True)
class Ddcutil:
    """Runs the read-only ``ddcutil`` commands and parses their output.

    ``timeout`` defaults to 10s: generously above the measured 3.1s worst case
    (``capabilities``) so a slow panel is not cut off, but bounded so a wedged DDC/CI channel
    cannot hang a launcher indefinitely.
    """

    executable: str = "ddcutil"
    timeout: float = 10.0
    runner: Runner = _subprocess_runner

    def detect(self, *, terse: bool = True) -> tuple[DetectedDisplay, ...]:
        """Run ``ddcutil detect`` — the fallback discovery path (ADR-0010).

        Slow (1.5s measured) because it probes every bus, so prefer sysfs resolution and call
        this only when the driver publishes no ``ddc`` links.
        """
        args = ["detect"]
        if terse:
            args.append("--terse")
        result = self._run(args)
        # detect exits 0 even when it finds nothing, so an empty result is not an error.
        if result.returncode != 0:
            self._raise_for(result)
        return parse_detect(result.output)

    def getvcp_input(self, bus: int) -> int:
        """Read the current Input Source on ``bus``.

        A successful read is also proof that the monitor is displaying this machine, which is
        what makes the returned value the Host Input (ADR-0013).
        """
        result = self._run(["--bus", str(bus), "getvcp", "60", "--terse"])
        if result.returncode != 0:
            self._raise_for(result, bus=bus)
        return parse_getvcp_input(result.output)

    def capabilities_inputs(self, bus: int) -> tuple[CapabilityInput, ...]:
        """Read the advertised Input Source values on ``bus``.

        Slowest of the three (3.1s measured) and unreliable in both directions, so it belongs
        off the interactive path (ADR-0010).
        """
        result = self._run(["--bus", str(bus), "capabilities"])
        if result.returncode != 0:
            self._raise_for(result, bus=bus)
        return parse_capabilities_inputs(result.output)

    def _run(self, args: Sequence[str]) -> CommandResult:
        return self.runner([self.executable, *args], self.timeout)

    def _raise_for(self, result: CommandResult, *, bus: int | None = None) -> None:
        """Translate a failed invocation into the most specific error available."""
        output = result.output
        lowered = output.lower()
        where = f" on bus {bus}" if bus is not None else ""

        # Checked before NoMonitorOnBus: ddcutil prints both lines for a permission failure.
        if any(marker in lowered for marker in _PERMISSION_MARKERS):
            raise I2cPermissionDenied(
                f"cannot open the I2C device{where}: permission denied. "
                "Grant non-root access (see README: i2c group or uaccess ACL). "
                f"ddcutil said: {output.strip()}"
            )

        if _BUS_NOT_FOUND.search(output):
            raise BusNotFound(f"I2C bus not found{where}; re-resolve the monitor")

        if _NO_MONITOR.search(output):
            raise NoMonitorOnBus(
                f"no monitor answered DDC/CI{where}. If a monitor is connected, check that "
                "DDC/CI is enabled in its on-screen menu"
            )

        raise DdcutilFailed(f"{' '.join(result.argv)} exited {result.returncode}: {output.strip()}")


# ---------------------------------------------------------------------------
# The write path
#
# Everything above this line is read-only. This is the only function in the
# project that can change a monitor's state, and it is deliberately the last
# thing added.
#
# It takes no decisions: whether a switch is allowed, and whether the user
# agreed to it, are settled before anything calls this. See
# monswitch.core.switching, which is the only intended caller.
# ---------------------------------------------------------------------------


class SetvcpFailed(DdcutilError):
    """``setvcp`` was issued but ddcutil reported a failure.

    The monitor may or may not have switched. Do not assume either way: a write that
    succeeded and left the Host Input also destroys the channel that would be used to check
    (ADR-0011).
    """


def _set_input_argv(executable: str, bus: int, code: int) -> tuple[str, ...]:
    """Build the setvcp command line.

    Separate from the call that runs it so tests can assert the exact argv without the risk
    of a function that executes anything.
    """
    if not 0 <= code <= 0xFF:
        raise ValueError(f"input code out of range: {code!r}")
    return (executable, "--bus", str(bus), "setvcp", "60", f"0x{code:02x}")


class WriteCapableDdcutil(Ddcutil):
    """A :class:`Ddcutil` that can also write the Input Source feature.

    Kept as a subclass so that the read-only :class:`Ddcutil` remains available and remains
    the default everywhere. Code that only needs to read cannot gain the ability to write by
    accident; it has to ask for this type by name.

    **Callers must not use this directly.** Go through
    :func:`monswitch.core.switching.switch_input`, which applies the confirmation policy.
    Issuing ``setvcp 60`` without a human agreeing in the moment can leave a monitor showing
    an input the user cannot get back from software.
    """

    def set_input(self, bus: int, code: int) -> None:
        """Switch the monitor on ``bus`` to input ``code``.

        DDC/CI writes are fire-and-forget and the monitor may take a second or two to
        re-sync. This deliberately does **not** read the value back to confirm: if the switch
        worked and it left the Host Input, the display link and the DDC/CI channel are gone,
        so a failed read-back would be the expected outcome of success, not evidence of
        failure.
        """
        argv = _set_input_argv(self.executable, bus, code)
        result = self.runner(argv, self.timeout)
        if result.returncode != 0:
            output = result.output
            lowered = output.lower()
            if any(marker in lowered for marker in _PERMISSION_MARKERS):
                raise I2cPermissionDenied(
                    f"cannot write to the I2C device on bus {bus}: permission denied. "
                    f"ddcutil said: {output.strip()}"
                )
            raise SetvcpFailed(
                f"setting input 0x{code:02x} on bus {bus} failed: {output.strip()}. "
                "The monitor may not have switched; run monswitch list to see its "
                "current input"
            )
