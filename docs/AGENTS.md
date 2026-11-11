# monswitch — agent brief

**Canonical agent doc.** `CLAUDE.md` and `GEMINI.md` at the repo root are pointers at this
file; keep the substance here so the two cannot drift apart again (they have, twice).

`README.md` is the user-facing doc. `docs/adr/` is the authoritative design record and
`docs/CONTEXT.md` the domain vocabulary.

## What this project is

A Linux tool to switch an external monitor's **input source** over DDC/CI. Two front-ends
planned: a GTK popup picker invoked as `monswitch`, and a Ulauncher extension.

Mechanism: VESA MCCS feature code `0x60` (Input Source), written over DDC/CI via `ddcutil`,
which talks to `/dev/i2c-*` adapters exposed by the GPU driver. Works over USB-C DP-alt-mode,
not just a physical DP cable.

## State as of 2026-11-09

`monswitch list` works. Everything implemented so far is **read-only**: there is no `setvcp`
call anywhere in the codebase, and the `ddcutil` wrapper deliberately exposes no method that
could issue one. Tests assert that absence.

Implemented: EDID parser, sysfs resolution, `ddcutil` parsers and wrapper, domain models with
the provenance tiers and confirmation policy, the discovery merge, the config layer, and the
`list` CLI. 244 tests, runnable without hardware.

Also implemented: input resolution (ADR-0006 priority order), the interaction-strategy seam
that keeps `isatty()` out of the core (ADR-0011), the `set` arity rule (ADR-0013),
`monswitch set --dry-run`, the write path behind its confirmation gate (`switching.py`), and
the GTK popup picker (`monswitch` with no arguments).

The picker's logic — filtering, selection, and the two-step Enter that serves as its
confirmation — lives in `core/picker.py` and is tested without a display. `gtk_picker.py` holds
widgets and key events only, and the Ulauncher extension is meant to reuse the same model.

**Never launch the picker from an agent session.** `Gtk.main()` blocks, and the window appears
on the user's screen unannounced. Verify it by importing the module and testing `PickerModel`;
leave running it to the user.

The Ulauncher extension lives in `extension/`. Its logic is in `core/launcher.py` (items,
actions, the cached snapshot) and it reuses `PickerModel` for filtering and confirmation, so
the launcher and the GTK picker cannot drift apart on the two-step Enter. `extension/main.py`
translates to Ulauncher's API and does nothing else.

Not yet built: `learn`.

Design is settled in [`adr/`](adr/) — 13 ADRs, indexed in [`adr/README.md`](adr/README.md).
**Read the ADRs before coding**, 0009–0013 especially: they carry the safety-critical rules and
amend several of the earlier eight. They supersede any older design sketch in this file or in
`README.md`.

Next to build: `monswitch list`, on the zero-config discovery of ADR-0001, the provenance model
of ADR-0009 and the sysfs resolution of ADR-0010.

## Hard-won facts — do not re-derive these

From live, read-only probing of the dev machine. Trust them over your priors.

1. **`ddcutil capabilities` misreports inputs in both directions.** On the reference HP Z38c it
   reports only `0f: DisplayPort-1`, `11: HDMI-1`, `1f: Unrecognized value` — while `getvcp 60`
   on the live USB-C input returns `sl=0x13`, absent from that list. So it *omits* real inputs
   and *contains* junk (`1f`). Never build the input list from `capabilities` alone, and never
   treat it as complete. It also costs 3.1 s, so keep it off interactive paths.

   Per ADR-0001 there is **no mandatory `learn` step**. Per **ADR-0014** the input list
   contains only codes the monitor reported, observed live, or the user supplied — the
   built-in fallback code table was removed, because it invented rows for inputs the panel
   never mentioned and those are the ones whose selection blanks the screen unrecoverably.
   Every `InputSource` still carries a provenance tier (ADR-0009) and the UI must show it.
   `learn` is the optional pass that promotes a code to `confirmed`.

   Also per ADR-0014: **config is opt-in.** Nothing is read without `--config PATH` or
   `--use-default-config`, so a bare `monswitch list` shows exactly what the hardware said.

2. **`getvcp 60` prints `Invalid value (sl=0x13)`** for vendor codes. Not an error to fix —
   `0x13` is the real current input. Parse the `sl=0x..` value; do not trust ddcutil's verdict.
   `--terse` gives the cleaner `VCP 60 SNC x13`.

3. **Switching away from your own input is one-way.** The DDC/CI channel rides the display link.
   Switch the monitor from your USB-C to DP and it stops showing your machine *and* you lose the
   channel to switch it back. Recovery is the physical OSD buttons.

   Two separate rules, do not conflate them:

   - **Agent rule (absolute).** You never issue `setvcp 60` without explicit user confirmation
     in the moment — not to test, not in automated runs. `confirm_switch = false` in the user's
     config and the CLI's `-y` / `--yes` / `--force` flags are **user** affordances; they are
     never permission for an agent to skip asking. Test with `getvcp` (read-only).
   - **Product rule (configurable).** Per ADR-0002 the shipped tool confirms by default before
     leaving the Host Input, and the user may opt out. Per ADR-0009 that opt-out does **not**
     silence the warning for an unverified destination.

   Corollary (ADR-0013): a successful `getvcp 60` read proves the returned value *is* the Host
   Input, since the link would otherwise be dead. The Host Input is therefore always derived,
   never stored, and no live read means no switch.

4. **A Ulauncher extension runs as the user with no TTY**, so it can never prompt for sudo.
   Non-root access to `/dev/i2c-*` is a hard prerequisite for that path, not a convenience; do
   not design around `sudo` prompts. Three routes grant it, in order of preference:
   `uaccess`/POSIX ACL on the seat's devices (already true on the dev machine), `i2c` group
   membership, or a sudoers rule scoped to `/usr/bin/ddcutil`.

   On the dev machine the ACL route is already satisfied and **no group change is needed** —
   verified 2026-11-09: the user is not in `i2c`, yet `ddcutil --bus 16 getvcp 60` succeeds
   unprivileged, because logind grants `user:<you>:rw-` to the holder of the local seat
   (`Seat=seat0`, `Remote=no`).

   That ACL follows the local seat, so it does **not** apply over SSH or to a systemd system
   service. **Remote switching is explicitly out of scope** (user's decision, 2026-11-09): both
   front-ends run in the graphical session on the local seat, so the seat-bound ACL is
   sufficient and the `i2c` group is a documented fallback only. Do not add SSH or headless
   support, or design for it, without asking.

5. **`--display N` indices are not stable** across reboots or replugs. Resolve monitors fresh
   each run — by sysfs (preferred, fact 6) or `--mfg`/`--model`/`--sn`/`--bus`.

6. **Prefer sysfs over `ddcutil detect` on the read path.** Measured 2026-11-09: sysfs walk
   0.02 s, `--bus N getvcp 60` 0.35 s, `detect` 1.5 s, `capabilities` 3.1 s.

   `/sys/class/drm/card*/card*-*/ddc` is a symlink to the I²C adapter, and the adjacent
   `edid`/`status` files identify and filter the panel at no I²C cost. Dev machine:
   `card1-DP-1 -> i2c-16`, EDID gives `HPN` / `HP Z38c` / `DEMOSERIAL`. The real serial is in
   the `0xff` descriptor block; the offset-12 serial field reads `0x01010101` and is useless.
   `detect` is the fallback for drivers that do not expose `ddc` (NVIDIA proprietary). ADR-0010.

   **There is no `--nodetect` flag** in `ddcutil` 1.4.1 — it errors with
   `Unknown option --nodetect`. Earlier versions of this doc and `README.md` recommended it.

7. **Laptop internal panels (`eDP-*`) have no DDC/CI input switching.** Filter them out by
   connector name.

8. **PEP 668 blocks the obvious install route.** `/usr/bin/ulauncher` is a `#!/usr/bin/python3`
   script, so the extension runs on the *system* interpreter and cannot see a venv;
   `~/.local/lib/python3.12/site-packages` does not exist so is not on its `sys.path`;
   `/usr/lib/python3.12/EXTERNALLY-MANAGED` makes `pip install --user .` refuse; and `gi` is
   only available as system `python3-gi`, so a venv is wrong for the picker too. ADR-0012.

## Dev machine

Ubuntu, kernel 7.0.0, Intel iGPU (`i915`). Internal panel `eDP-1`; HP Z38c on `DP-1`, which
*is* the USB-C port in DP alt mode. `ddcutil` 1.4.1, Python 3.12.3, Ulauncher 5.16.2 (extension
API v2 — manifest needs `"required_api_version": "^2.0.0"`).

Local setup state (verified 2026-11-09): `ddcutil` installed, `/dev/i2c-*` present. The user is
**not** in the `i2c` group and **does not need to be** — logind `uaccess` grants POSIX ACLs
`user:<you>:rw-` on `/dev/i2c-6`–`/dev/i2c-19`, the DRM-connector buses. Non-root `ddcutil`
works as-is; `sudo` is not required. (An earlier doc claimed the opposite. Re-verify with
`getfacl /dev/i2c-16` rather than trusting either claim.)

## Project origin (context not in the artifacts)

The project grew out of a concrete goal: the user's laptop drives an HP monitor over USB-C and
they want to flip the monitor to its DisplayPort input from software.

**No `setvcp` has ever been issued on this machine.** All probing to date has been read-only.
Keep it that way absent explicit, in-the-moment confirmation (fact 3).

Still true as of 2026-11-09, after the write path landed. `monswitch set usbc` has been run
live with the user's authorisation, but that is the **no-op** path: the destination was the
Host Input, so `switch_input` returned before reaching `set_input` and built no command.
`getvcp` read `x13` identically before and after. The real write therefore has **no live
coverage**, and acquiring it means blacking out the reference monitor and recovering with its
physical buttons — so it is the user's call to make, at the monitor, not an agent's.

## CLI surface

| Invocation | Behaviour |
| --- | --- |
| `monswitch list` | Detected monitors, their inputs, the active one marked, unverified inputs badged |
| `monswitch set <input>` | Auto-targets the only external monitor (ADR-0004) |
| `monswitch set <monitor> <input>` | Two positionals = monitor then input. Arity alone decides; the parser never inspects values to assign roles (ADR-0013) |
| `monswitch -m <monitor> set <input>` | Always unambiguous; the form for scripts. `-m` plus two positionals is an error |
| `monswitch learn` | *Optional* OSD walk-through promoting guessed codes to `confirmed`. Not a prerequisite for anything |
| `monswitch` (no args) | GTK3 popup picker (ADR-0008) |

`--refresh` forces re-probing, bypassing the fallback bus cache. `-y`/`--yes`/`--force` are
user-only (fact 3).

## Conventions

- **Language:** Python 3.12, standard library only in the core (`tomllib`, `argparse`,
  `dataclasses`, `subprocess`). GTK3 via PyGObject (`python3-gi`) for the picker, apt-installed
  and never a `pip` dependency.
- **Unified core.** One shared module both front-ends call — `ddc.py` (subprocess wrapper and
  output parsers), `config.py`, `models.py`. Front-ends are thin presentation wrappers; the
  Ulauncher extension is not a reimplementation. No import-time side effects anywhere in the
  core: no `ddcutil` call, no sysfs walk, no config read (ADR-0012).
- **Shell out to `ddcutil`**; do not reimplement DDC/CI over raw I²C.
- **Testing:** `pytest` over captured fixtures — EDID bytes, `detect --terse`, `getvcp 60` both
  terse and not, `capabilities`. The parsers are pure bytes/string-in, data-out and need no
  hardware. Never issue `setvcp` from a test.

  Tests are written as `unittest.TestCase` classes, which pytest collects natively. This is
  deliberate: PEP 668 (fact 8) means pytest may be absent, and the core is stdlib-only, so the
  suite must be runnable with no dependencies at all.

  ```bash
  python3 -m venv --system-site-packages .venv   # --system-site-packages so `gi` stays visible
  .venv/bin/pip install pytest
  .venv/bin/python -m pytest tests/ -q           # with the dev venv
  PYTHONPATH=src python3 -m unittest discover -s tests -t tests   # zero dependencies
  ```

  The venv is a **development** convenience only. It is never the runtime path: the Ulauncher
  daemon runs the system interpreter and cannot see it, and the picker needs system `gi`
  (ADR-0012). Do not add a venv to any install instructions.
- **Documentation:** all docs, ADRs and specs under `./docs/`. ADR conventions in
  `docs/adr/README.md` — including that a superseded ADR keeps its original reasoning and gains
  a note, and that the status change is recorded in both files *and* the index.

## Safety

Read operations (`detect`, `capabilities`, `getvcp`, sysfs) are safe to run freely. `setvcp 60`
changes physical hardware state the user may not be able to undo from software — ask first,
every time, per fact 3. Other `setvcp` features (brightness, colour) are recoverable but out of
scope; do not touch them.
