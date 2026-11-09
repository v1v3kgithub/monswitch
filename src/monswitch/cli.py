"""Command-line front-end.

A thin presentation wrapper over ``monswitch.core``, per the unified-core convention: this
module formats and prints, and makes no decisions the core could make instead.

Currently implements ``monswitch list``. The write path (``set``) and the GTK picker are not
here yet, deliberately — ``setvcp`` arrives with its confirmation flow, not before.

Error handling follows ADR-0011: the core raises typed errors and returns warnings, and this
module renders them. Nothing exits silently, and every failure says what to do about it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .core.config import Config, config_path, load_tolerant
from .core.ddc import Ddcutil, DdcutilError, DdcutilNotInstalled
from .core.discovery import Discovery, discover
from .core.models import KeyedMonitor, Provenance
from .core.sysfs import DEFAULT_DRM_ROOT

__all__ = ["main"]

PROGRAM = "monswitch"
VERSION = "0.0.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

# How each tier is described in output. The user never sees the enum names.
_TIER_LABELS = {
    Provenance.CONFIRMED: "confirmed",
    Provenance.LIVE: "live",
    Provenance.REPORTED: "reported",
    Provenance.GUESSED: "guessed",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Switch an external monitor's input source over DDC/CI.",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {VERSION}")
    parser.add_argument(
        "-m",
        "--monitor",
        metavar="MONITOR",
        help=(
            "target a specific monitor by name, model or serial. Always unambiguous, so "
            "prefer it in scripts"
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        help=f"config file to use (default: {config_path()})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also report connectors that were skipped and why",
    )

    subcommands = parser.add_subparsers(dest="command")

    listing = subcommands.add_parser(
        "list",
        help="show detected monitors, their inputs, and which input is active",
        description=(
            "Show detected monitors and their inputs. Read-only: this never changes "
            "anything on the monitor."
        ),
    )
    listing.add_argument(
        "--no-capabilities",
        dest="capabilities",
        action="store_false",
        help=(
            "skip the monitor's advertised input list. Faster (it costs a few seconds), at "
            "the price of showing fewer inputs"
        ),
    )
    listing.set_defaults(capabilities=True)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    ddcutil: Ddcutil | None = None,
    drm_root: Path = DEFAULT_DRM_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run the CLI. Returns the process exit code.

    The keyword arguments exist so the tests can drive this with a fake ``ddcutil`` and a
    synthetic sysfs tree instead of real hardware.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    if args.command is None:
        # ADR-0008 specifies a GTK popup picker here. Until it exists, say so plainly rather
        # than silently doing something else.
        print(parser.format_help().rstrip(), file=stdout)
        print(
            "\nThe popup picker is not implemented yet. For now, use: monswitch list",
            file=stdout,
        )
        return EXIT_OK

    if args.command == "list":
        return _run_list(args, ddcutil=ddcutil, drm_root=drm_root, out=stdout, err=stderr)

    parser.print_usage(file=stderr)
    return EXIT_USAGE


def _run_list(
    args: argparse.Namespace,
    *,
    ddcutil: Ddcutil | None,
    drm_root: Path,
    out: TextIO,
    err: TextIO,
) -> int:
    config, config_warnings = load_tolerant(args.config)
    for warning in config_warnings:
        print(f"{PROGRAM}: config: {warning}", file=err)

    try:
        result = discover(
            ddcutil,
            root=drm_root,
            include_capabilities=args.capabilities,
            confirmed_inputs=config.confirmed_inputs(),
            name_overrides=config.name_overrides(),
        )
    except DdcutilNotInstalled as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        print(
            "Install it with your package manager, e.g. sudo apt install ddcutil",
            file=err,
        )
        return EXIT_ERROR
    except DdcutilError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_ERROR

    monitors = _select(result.monitors, args.monitor)

    if args.monitor and not monitors:
        print(
            f"{PROGRAM}: no monitor matches {args.monitor!r}. "
            "Run monswitch list to see what is connected.",
            file=err,
        )
        return EXIT_ERROR

    if not monitors:
        _report_nothing_found(result, out=out, err=err, verbose=args.verbose)
        return EXIT_ERROR

    for index, keyed in enumerate(monitors):
        if index:
            print(file=out)
        _print_monitor(keyed, config=config, out=out)

    _print_notes(result, monitors, out=out, err=err, verbose=args.verbose)
    return EXIT_OK


def _select(monitors: Sequence[KeyedMonitor], query: str | None) -> list[KeyedMonitor]:
    """Filter monitors by a user-supplied query.

    Matches case-insensitively against the display name, EDID model, manufacturer, serial and
    connector, so anything the listing shows can be pasted back in as a selector.
    """
    if not query:
        return list(monitors)

    needle = query.strip().lower()
    matches = []
    for keyed in monitors:
        identity = keyed.monitor.identity
        haystack = [
            keyed.monitor.display_name,
            keyed.key,
            identity.model or "",
            identity.manufacturer,
            identity.serial or "",
            keyed.monitor.connector,
        ]
        if any(needle in field.lower() for field in haystack if field):
            matches.append(keyed)
    return matches


def _print_monitor(keyed: KeyedMonitor, *, config: Config, out: TextIO) -> None:
    monitor = keyed.monitor
    identity = monitor.identity

    # The model is only worth repeating when the heading is a configured name rather than
    # the model itself, so "HP Z38c (HPN HP Z38c)" never happens.
    parts = [identity.manufacturer]
    if identity.model and identity.model != monitor.display_name:
        parts.append(identity.model)
    print(f"{monitor.display_name}  ({' '.join(parts)})", file=out)
    print(f"  bus {monitor.bus} · connector {monitor.connector} · key {keyed.key}", file=out)

    if not monitor.is_switchable:
        print("  switching unavailable: the current input could not be read", file=out)

    if not monitor.inputs:
        print("  no inputs discovered", file=out)
        return

    print(file=out)
    for source in monitor.inputs:
        active = source.code == monitor.host_input
        marker = "*" if active else " "
        tier = _TIER_LABELS[source.provenance]
        notes = []
        if active:
            notes.append("current input, this machine")
        if not source.is_verified:
            notes.append("unverified")
        suffix = f"  ({'; '.join(notes)})" if notes else ""
        print(
            f"  {marker} {source.hex_code}  {source.label:<22} {tier:<9}{suffix}",
            file=out,
        )


def _print_notes(
    result: Discovery,
    shown: Sequence[KeyedMonitor],
    *,
    out: TextIO,
    err: TextIO,
    verbose: bool,
) -> None:
    unverified = any(
        not source.is_verified for keyed in shown for source in keyed.monitor.inputs
    )
    if unverified:
        print(
            "\nInputs marked unverified were inferred, not observed. Switching to one may "
            "leave the monitor dark with no way back from software; "
            "run monswitch learn to confirm them.",
            file=out,
        )

    if result.used_detect_fallback:
        print(
            "\nNote: resolved monitors with ddcutil detect, because this driver does not "
            "publish sysfs ddc links.",
            file=out,
        )

    for warning in result.warnings:
        print(f"{PROGRAM}: {warning.monitor}: {warning.message}", file=err)

    if verbose:
        for name, reason in result.skipped:
            print(f"{PROGRAM}: skipped {name}: {reason}", file=err)


def _report_nothing_found(
    result: Discovery, *, out: TextIO, err: TextIO, verbose: bool
) -> None:
    """Explain an empty result. Never just print nothing (ADR-0011)."""
    print(f"{PROGRAM}: no external monitor with DDC/CI was found.", file=err)
    print("Things worth checking:", file=err)
    print("  - the monitor is connected and switched on", file=err)
    print("  - DDC/CI is enabled in the monitor's own on-screen menu", file=err)
    print("    (often under Management, Settings or Other)", file=err)
    print("  - the i2c-dev kernel module is loaded: sudo modprobe i2c-dev", file=err)
    print("  - you can read /dev/i2c-*: try ddcutil detect", file=err)
    print("Laptop internal panels are excluded; they cannot switch inputs.", file=err)

    for warning in result.warnings:
        print(f"{PROGRAM}: {warning.monitor}: {warning.message}", file=err)

    if verbose:
        for name, reason in result.skipped:
            print(f"{PROGRAM}: skipped {name}: {reason}", file=err)
    elif result.skipped:
        print(
            f"{PROGRAM}: {len(result.skipped)} connector(s) were skipped; "
            "re-run with -v to see why.",
            file=err,
        )


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
