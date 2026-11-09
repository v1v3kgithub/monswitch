# MonitorSwitcher — agent handoff

Read this first. `README.md` is the user-facing doc; this file is where an agent picks up.

## What this project is

A Linux tool to switch an external monitor's **input source** over DDC/CI. Two front-ends
planned: a GTK popup picker invoked as `monswitch`, and a Ulauncher extension.

Mechanism: VESA MCCS feature code `0x60` (Input Source), written over DDC/CI via `ddcutil`,
which talks to `/dev/i2c-*` adapters exposed by the GPU driver. Works over USB-C DP-alt-mode,
not just a physical DP cable.

## State as of 2026-11-09

Documentation only. **No code has been written yet.**

Design is settled in `./docs/adr/` (ADR-0001..0009) and the domain vocabulary in
`./docs/CONTEXT.md`. **Read the ADRs before coding** — they supersede any older design
sketch in this file or in `README.md`.

The next thing to build is `monswitch list`, on top of the zero-config discovery of
ADR-0001 and the provenance model of ADR-0009.

## Hard-won facts — do not re-derive these

These came out of live probing on the dev machine. Trust them over your priors.

1. **`ddcutil capabilities` lies about input values — in both directions.** On the reference
   HP monitor it reports only `0f: DisplayPort-1`, `11: HDMI-1`, `1f: Unrecognized value` —
   while `getvcp 60` on the live USB-C input returns `sl=0x13`, a value absent from that
   list. So the list both *omits* real inputs and *contains* junk (`1f`). Never build the
   input list from `capabilities` alone, and never treat it as complete.

   **Superseded design note:** an earlier version of this file called for a mandatory
   `monswitch learn` calibration step. ADR-0001 replaced that with **zero-config dynamic
   discovery** (merge live `getvcp 60` + `capabilities` + a fallback table of known
   VESA/vendor codes). `learn` survives only as an *optional* verification pass that
   promotes guessed codes to verified ones. Because that merge guesses, every `InputSource`
   must carry its provenance — see **ADR-0009**, which is load-bearing for safety, not
   cosmetic.

2. **`getvcp 60` prints `Invalid value (sl=0x13)`** for such codes. That is not an error to
   fix — `0x13` is the real current input. Parse the `sl=0x..` value, do not trust ddcutil's
   "Invalid value" verdict.

3. **Switching away from your own input is one-way.** The DDC/CI channel rides on the display
   link. Switch the monitor from your USB-C to DP and the monitor stops showing your machine
   *and* you lose the channel to switch it back. Recovery is physical OSD buttons. Therefore:
   the UI must mark the currently-active input and confirm before switching away from it.

   Two separate rules, do not conflate them:

   - **Agent rule (absolute).** You, the agent, never issue `setvcp 60` without explicit user
     confirmation in the moment — not even to test, not in automated runs. `confirm_switch =
     false` in the user's config, and the CLI's `-y` / `--yes` / `--force` flags, are **user**
     affordances; they are never permission for an agent to skip asking. Test with `getvcp`
     (read-only) instead.
   - **Product rule (configurable).** Per ADR-0002, the shipped tool confirms by default
     before switching away from the Host Input, and the user may opt out via
     `confirm_switch = false` or the `-y` flags.

4. **A Ulauncher extension runs as the user with no TTY**, so it can never prompt for a sudo
   password. Non-root access to `/dev/i2c-*` is a hard prerequisite for that path, not a
   convenience. Do not design around `sudo` prompts. Three routes grant it, in order of
   preference: `uaccess`/POSIX ACL on the local seat's devices (already true on the dev
   machine — see below), `i2c` group membership, or a sudoers rule scoped to
   `/usr/bin/ddcutil`.

5. **`--display N` indices are not stable** across reboots or replugs. Resolve monitors by
   `--mfg`/`--model`/`--sn` or `--bus` fresh at each run.

6. **`ddcutil detect` is slow** — it probes every I²C bus. Cache results; prefer an explicit
   `--bus` on the write path.

7. Laptop internal panels (`eDP-*`) have no DDC/CI input switching. Filter them out.

## Dev machine

Ubuntu, kernel 7.0.0, Intel iGPU (`i915`). Internal panel `eDP-1`; HP monitor on `DP-1`,
which *is* the USB-C port in DP alt mode. `ddcutil` 1.4.1, Python 3.12, Ulauncher 5.16.2
(extension API v2 — manifest needs `"required_api_version": "^2.0.0"`).

Local setup state (verified 2026-11-09): `ddcutil` installed; `/dev/i2c-*` present. The user
is **not** in the `i2c` group and **does not need to be** — logind `uaccess` already grants
POSIX ACLs `user:devuser:rw-` on `/dev/i2c-6` through `/dev/i2c-19`, which are the DRM-connector
buses. Non-root `ddcutil` works as-is; `sudo` is not required. (An earlier version of this file
claimed the opposite. Re-verify with `getfacl /dev/i2c-16` rather than trusting either claim.)

## Project origin (context not in the artifacts)

The project grew out of a concrete goal: the user's laptop drives an HP monitor over USB-C and
they want to flip the monitor to its DisplayPort input from software.

**No `setvcp` has ever been issued on this machine.** All probing to date has been read-only.
Keep it that way absent explicit, in-the-moment confirmation (fact 3 above).

## Conventions

- Language: Python 3.12. GTK3 via PyGObject (`python3-gi`) for the picker.
- Documentation: Save all project documentation, ADRs, and specs in the `./docs/` directory.
- Keep a core module that both front-ends (CLI/GTK and Ulauncher) call. The Ulauncher
  extension must be a thin wrapper, not a reimplementation.
- Shell out to `ddcutil`; do not reimplement DDC/CI over raw I²C.

## Safety

Read operations (`detect`, `capabilities`, `getvcp`) are safe to run freely. `setvcp 60`
changes physical hardware state the user may not be able to undo from software — ask first,
every time. Other `setvcp` features (brightness, colour) are recoverable but still out of
scope; do not touch them.
