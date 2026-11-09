"""Command-line front-end.

A thin presentation wrapper over ``monswitch.core``, per the unified-core convention: this
module formats and prints, and makes no decisions the core could make instead.

Implements ``monswitch list`` and ``monswitch set``. The GTK picker is not here yet.

``set`` can change hardware, so it goes through :func:`monswitch.core.switching.switch_input`
rather than touching the write path itself: that is where the confirmation policy lives.
``--dry-run`` resolves a target and reports the verdict without writing.

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
from .core.ddc import (
    Ddcutil,
    DdcutilError,
    DdcutilNotInstalled,
    WriteCapableDdcutil,
)
from .core.discovery import Discovery, discover
from .core.interaction import Choice, Declined, Prompting, Strict
from .core.models import (
    Confirmation,
    InputSource,
    KeyedMonitor,
    Monitor,
    Provenance,
    switch_confirmation,
)
from .core.resolution import resolve_input
from .core.switching import SwitchRefused, switch_input
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


def _common_flags() -> argparse.ArgumentParser:
    """Flags accepted either before or after the subcommand.

    Everything here defaults to ``SUPPRESS`` so that the copy attached to a subparser does
    not overwrite a value already set by the copy on the main parser. Without that, argparse
    silently clobbers `monswitch -v list` with the subparser's own default, and the flag
    appears to do nothing.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-m",
        "--monitor",
        metavar="MONITOR",
        default=argparse.SUPPRESS,
        help=(
            "target a specific monitor by name, model or serial. Always unambiguous, so "
            "prefer it in scripts"
        ),
    )
    common.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        default=argparse.SUPPRESS,
        help=f"config file to use (default: {config_path()})",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="also report connectors that were skipped and why",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_flags()
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Switch an external monitor's input source over DDC/CI.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {VERSION}")

    subcommands = parser.add_subparsers(dest="command")

    listing = subcommands.add_parser(
        "list",
        parents=[common],
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

    setter = subcommands.add_parser(
        "set",
        parents=[common],
        help="switch a monitor to an input",
        description=(
            "Switch a monitor's input. Arity decides the target: one positional is an "
            "input on the auto-targeted monitor, two are a monitor then an input. "
            "-m/--monitor is always unambiguous and is the form to use in scripts."
        ),
    )
    setter.add_argument(
        "target",
        nargs="+",
        metavar="[MONITOR] INPUT",
        help="an input, optionally preceded by a monitor",
    )
    setter.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the target and report what would happen, changing nothing",
    )
    setter.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "do not ask before switching away from this machine. Does not cover an "
            "unverified input; confirm that input first, or use --force"
        ),
    )
    setter.add_argument(
        "--force",
        action="store_true",
        help=(
            "also skip the confirmation for an unverified input, which may not exist on "
            "the monitor. Prefer confirming the input instead"
        ),
    )

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

    # The shared flags use SUPPRESS so the two copies cannot overwrite each other, which
    # means an unused flag is absent rather than defaulted. Fill the gaps once, here.
    for name, default in (("monitor", None), ("config", None), ("verbose", False)):
        if not hasattr(args, name):
            setattr(args, name, default)

    if args.command is None:
        # ADR-0008: no arguments means the popup picker.
        return _run_picker(args, ddcutil=ddcutil, drm_root=drm_root, out=stdout, err=stderr)

    if args.command == "list":
        return _run_list(args, ddcutil=ddcutil, drm_root=drm_root, out=stdout, err=stderr)

    if args.command == "set":
        return _run_set(
            args, parser, ddcutil=ddcutil, drm_root=drm_root, out=stdout, err=stderr
        )

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


def _split_target(
    positional: Sequence[str], explicit_monitor: str | None
) -> tuple[str | None, str] | None:
    """Apply ADR-0013's arity rule to the positional arguments.

    One positional is an input on the auto-targeted monitor; two are a monitor then an input.
    The values are never inspected to decide which role they play, so ``set hp dp1`` is always
    monitor-then-input and never a guess that could land on the wrong hardware.

    Returns ``None`` when the combination is not allowed.
    """
    if explicit_monitor is not None and len(positional) > 1:
        # Supplying both is an error rather than a precedence puzzle (ADR-0013).
        return None
    if len(positional) == 1:
        return explicit_monitor, positional[0]
    if len(positional) == 2:
        return positional[0], positional[1]
    return None


def _run_set(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    ddcutil: Ddcutil | None,
    drm_root: Path,
    out: TextIO,
    err: TextIO,
) -> int:
    split = _split_target(args.target, args.monitor)
    if split is None:
        if args.monitor is not None and len(args.target) > 1:
            print(
                f"{PROGRAM}: give the monitor either with -m or as the first argument, "
                "not both",
                file=err,
            )
        else:
            print(
                f"{PROGRAM}: expected 'set <input>' or 'set <monitor> <input>', "
                f"got {len(args.target)} arguments",
                file=err,
            )
        return EXIT_USAGE

    monitor_query, input_query = split

    config, config_warnings = load_tolerant(args.config)
    for warning in config_warnings:
        print(f"{PROGRAM}: config: {warning}", file=err)

    try:
        result = discover(
            ddcutil,
            root=drm_root,
            include_capabilities=True,
            confirmed_inputs=config.confirmed_inputs(),
            name_overrides=config.name_overrides(),
        )
    except DdcutilNotInstalled as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_ERROR
    except DdcutilError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_ERROR

    candidates = _select(result.monitors, monitor_query)

    if not candidates:
        if monitor_query:
            print(f"{PROGRAM}: no monitor matches {monitor_query!r}", file=err)
        else:
            _report_nothing_found(result, out=out, err=err, verbose=args.verbose)
        return EXIT_ERROR

    if len(candidates) > 1:
        # ADR-0004: auto-targeting refuses to guess between monitors.
        print(
            f"{PROGRAM}: several monitors are connected; say which one:",
            file=err,
        )
        for keyed in candidates:
            print(f"  - {keyed.monitor.display_name}  (key {keyed.key})", file=err)
        return EXIT_USAGE

    keyed = candidates[0]
    monitor = keyed.monitor

    match = resolve_input(monitor, input_query)
    if match.is_empty:
        print(
            f"{PROGRAM}: {monitor.display_name} has no input matching {input_query!r}. "
            "Run monswitch list to see its inputs.",
            file=err,
        )
        return EXIT_ERROR

    if match.is_ambiguous:
        strategy = Prompting() if sys.stdin.isatty() else Strict()
        choices = [
            Choice(
                value=source,
                label=f"{source.hex_code}  {source.label}",
                note=None if source.is_verified else "unverified",
            )
            for source in match.matches
        ]
        try:
            chosen = strategy.choose(input_query, choices)
        except Declined as exc:
            print(f"{PROGRAM}: {exc}", file=err)
            return EXIT_ERROR
        destination = chosen if isinstance(chosen, InputSource) else match.matches[0]
    else:
        destination = match.only

    verdict = switch_confirmation(
        monitor, destination, confirm_switch=config.confirm_switch
    )

    print(
        f"{monitor.display_name}: {destination.hex_code} {destination.label} "
        f"({_TIER_LABELS[destination.provenance]})",
        file=out,
    )
    print(f"  resolved from {input_query!r} by {match.matched_by}", file=out)
    print(f"  current input: {_describe_host(monitor)}", file=out)
    print(f"  verdict: {_describe_verdict(verdict)}", file=out)

    if args.dry_run:
        print("  (dry run: nothing was changed)", file=out)
        return EXIT_OK

    writer = (
        ddcutil
        if isinstance(ddcutil, WriteCapableDdcutil)
        else WriteCapableDdcutil(
            executable=ddcutil.executable if ddcutil else "ddcutil",
            timeout=ddcutil.timeout if ddcutil else 10.0,
        )
    )
    strategy = Prompting() if sys.stdin.isatty() else Strict()

    try:
        outcome = switch_input(
            monitor,
            destination,
            ddcutil=writer,
            strategy=strategy,
            confirm_switch=config.confirm_switch,
            assume_yes=args.yes,
            force=args.force,
        )
    except SwitchRefused as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_ERROR
    except DdcutilError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_ERROR

    if not outcome.changed:
        print("  already on that input; nothing to do", file=out)
        return EXIT_OK

    if outcome.channel_lost:
        # The DDC/CI channel went with the display link. Saying so is the honest report:
        # there is nothing left to verify against (ADR-0011).
        print(
            f"  switched. This monitor is no longer showing this machine, so it can only "
            f"be switched back from its own buttons or from whatever is on "
            f"{destination.label}.",
            file=out,
        )
    else:
        print("  switched.", file=out)
    return EXIT_OK


def _describe_host(monitor: Monitor) -> str:
    if monitor.host_input is None:
        return "unknown (could not be read)"
    source = monitor.host_input_source
    label = source.label if source else f"0x{monitor.host_input:02x}"
    return f"0x{monitor.host_input:02x} {label}"


def _describe_verdict(verdict: Confirmation) -> str:
    if verdict is Confirmation.NONE:
        return "no confirmation needed"
    if verdict is Confirmation.LEAVING_HOST:
        return (
            "would switch this monitor away from this machine, which cannot be undone "
            "from software - confirmation required"
        )
    if verdict is Confirmation.UNVERIFIED_DESTINATION:
        return (
            "destination was inferred, not observed; it may not exist on this panel - "
            "stronger confirmation required, even with confirm_switch = false"
        )
    return "refused: the current input could not be read, so the risk cannot be assessed"


def _run_picker(
    args: argparse.Namespace,
    *,
    ddcutil: Ddcutil | None,
    drm_root: Path,
    out: TextIO,
    err: TextIO,
) -> int:
    """Open the GTK popup picker (ADR-0008).

    Discovery happens before the window opens, so a failure can be reported plainly on the
    terminal rather than as an empty popup. Once the window is up, everything the user needs
    to know is rendered inside it (ADR-0011).
    """
    from .gtk_picker import GTK_AVAILABLE, gtk_import_error, run_picker

    if not GTK_AVAILABLE:
        print(f"{PROGRAM}: the popup picker needs GTK: {gtk_import_error()}", file=err)
        print(
            "Install it with: sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "Meanwhile, monswitch list and monswitch set work without it.",
            file=err,
        )
        return EXIT_ERROR

    config, config_warnings = load_tolerant(args.config)
    for warning in config_warnings:
        print(f"{PROGRAM}: config: {warning}", file=err)

    try:
        result = discover(
            ddcutil,
            root=drm_root,
            include_capabilities=True,
            confirmed_inputs=config.confirmed_inputs(),
            name_overrides=config.name_overrides(),
        )
    except DdcutilError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_ERROR

    monitors = _select(result.monitors, args.monitor)
    if not monitors:
        _report_nothing_found(result, out=out, err=err, verbose=args.verbose)
        return EXIT_ERROR

    # Warnings are carried into the window, since there is no terminal in the user's attention
    # once a popup is open.
    opening_message = (
        "; ".join(f"{w.monitor}: {w.message}" for w in result.warnings)
        if result.warnings
        else None
    )

    writer = (
        ddcutil
        if isinstance(ddcutil, WriteCapableDdcutil)
        else WriteCapableDdcutil(
            executable=ddcutil.executable if ddcutil else "ddcutil",
            timeout=ddcutil.timeout if ddcutil else 10.0,
        )
    )

    outcome = run_picker(
        monitors, config=config, ddcutil=writer, message=opening_message
    )

    if outcome.error:
        print(f"{PROGRAM}: {outcome.error}", file=err)
        return EXIT_ERROR
    if outcome.switched:
        print(f"{PROGRAM}: switched.", file=out)
    return EXIT_OK
