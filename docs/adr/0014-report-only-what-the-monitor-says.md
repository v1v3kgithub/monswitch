# Report Only What the Monitor Says

> **Status:** Accepted · **Date:** 2026-11-11
> **Supersedes in part:** [ADR-0001](0001-zero-config-dynamic-discovery.md) (removes the
> fallback code table), [ADR-0007](0007-config-schema.md) (config is read only on request)
> **Refines:** [ADR-0009](0009-input-provenance-and-trust-tiers.md)

Two sources of input rows were implicit: a built-in table of codes common across vendors, and
the config file at the default path. Both were applied without being asked for, and both put
rows in front of the user that their monitor had never mentioned. This ADR removes the first
entirely and makes the second opt-in.

The trigger was a user report. On the reference HP panel the picker listed seven inputs, of
which the panel itself had only ever named three:

| Row | Where it came from | Exists? |
| --- | --- | --- |
| `0x0f DisplayPort-1` | `capabilities` | yes — the one that worked |
| `0x10 DisplayPort-2` | **built-in table** | no |
| `0x11 HDMI-1` | `capabilities` | probably |
| `0x12 HDMI-2` | **built-in table** | probably not |
| `0x13` | live `getvcp` | yes |
| `0x1b USB-C / Thunderbolt` | **built-in table** | no |
| `0x1f` | `capabilities`, unnamed | firmware junk |

The user tried the second DisplayPort row, found it did nothing, and reasonably assumed it had
come from their config — which did not exist. Three of seven rows were invented by this
project.

## 1. The fallback code table is removed

ADR-0001 introduced it to avoid a worse outcome: a panel that reports nothing useful would
otherwise show a single row for its current input and look broken. That reasoning is sound for
the panel it describes and was then applied unconditionally, including to panels that *had*
answered.

The cost is not untidiness. Selecting an input that does not exist is the one action in this
tool that blanks the screen with no software route back (ADR-0013), so a table of plausible
codes manufactures precisely the most dangerous rows in the list. Marking them `GUESSED` and
warning before the switch, per ADR-0009, mitigates but does not justify them: the warning is
spent on rows that should never have been offered.

It also fails worst where it was meant to help. The table is a cross-vendor generalisation —
`0x13` is USB-C on HP and may be anything elsewhere — so on an unfamiliar monitor, exactly the
case with no confirmed map, its names are least trustworthy.

**Decision:** the input list contains only codes the monitor reported (`capabilities`),
observed live (`getvcp 60`), or supplied by the user. Nothing speculative. A panel that reports
nothing therefore shows one row, which is honest; if that proves a real problem in use, the
answer is a clearer empty-state message, not invented rows.

**Consequence:** a code seen only in the live read carries no name, since nothing trustworthy
supplied one. `0x13` displays as `0x13` rather than "USB-C". This is the honest rendering of a
vendor-specific code, and a config nickname is how a user attaches a name.

## 2. Config is read only when asked for

ADR-0007 assumed the config at the default path is always consulted. For the reporting user,
who hot-desks across monitors, that is the wrong default twice over: the file would be keyed on
serials they will not see again, and — as the report showed — its mere possibility muddies what
the listing means. They could not tell which rows were their own configuration and which were
the tool's invention.

**Decision:** no config is read unless requested. `--config PATH` reads that file;
`--use-default-config` reads the standard path. With neither, the listing is exactly what the
monitor reported.

Two details follow from the failure modes:

- If a config exists at the default path and was not requested, that is mentioned once on
  stderr. Silently ignoring a file the user wrote is its own trap.
- `--config` takes a **required** value, and `--use-default-config` is a separate flag rather
  than a bare `--config`. An optional-value option is filled from the next token, so
  `monswitch --config list` consumed `list` as the path, left no subcommand, and opened the GTK
  picker instead of listing — a window the user never asked for, blocking until killed. An
  option that swallows a subcommand is now detected and reported as a usage error.

## Considered Options

- **Keep the table, hide guesses behind a flag:** the default view would be clean and nothing
  lost. Rejected as two different lists depending on a flag the user forgets they set, and the
  dangerous rows remain one flag away.
- **Keep the table only as a last resort**, when the monitor reports nothing: preserves
  ADR-0001's stated reason exactly. A reasonable option, and the user chose outright removal
  over it — the panel-reports-nothing case is hypothetical here, and a tool that invents rows
  only on unfamiliar hardware is hardest to trust precisely when trust matters.
- **Promote standard MCCS codes to verified:** `0x0f` and `0x11` are defined by the VESA
  standard, so one could treat them as real when `capabilities` also names them. Rejected: the
  reference panel already violates the standard elsewhere (it omits its own live input and
  lists junk), which is poor grounds for extending it trust.
- **Report only what the monitor says (Chosen):** every row traceable to the hardware or the
  user.

## Consequences

- Fewer rows, all defensible. On the reference panel: `0x0f`, `0x11`, `0x13`, `0x1f`.
- `0x1f` still appears, as `GUESSED` and nameless, because the monitor did report it. ADR-0009
  already forbids naming it; this ADR does not add a judgement about whether a reported code is
  real.
- `learn` becomes the only route to `CONFIRMED`, and more clearly worth building. Note it
  confirms per *unit*: for a hot-desking user it must be re-run per monitor, which limits its
  value and is worth revisiting.
- Scripts relying on a code appearing without the monitor reporting it will now fail to resolve
  it. That is the intended behaviour change: the code was never known to exist.
