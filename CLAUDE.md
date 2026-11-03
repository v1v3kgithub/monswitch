# MonitorSwitcher — agent handoff

Read this first. `README.md` is the user-facing doc; this file is where an agent picks up.

## What this project is

A Linux tool to switch an external monitor's **input source** over DDC/CI. Two front-ends
planned: a GTK popup picker invoked as `monswitch`, and a Ulauncher extension.

Mechanism: VESA MCCS feature code `0x60` (Input Source), written over DDC/CI via `ddcutil`,
which talks to `/dev/i2c-*` adapters exposed by the GPU driver. Works over USB-C DP-alt-mode,
not just a physical DP cable.

## State as of 2026-11-03

Documentation only. **No code has been written yet.** One commit: the README.

The next thing to build is `monswitch learn` and `monswitch list` (see Roadmap in README).

## Hard-won facts — do not re-derive these

These came out of live probing on the dev machine. Trust them over your priors.

1. **`ddcutil capabilities` lies about input values.** On the reference HP monitor it reports
   only `0f: DisplayPort-1`, `11: HDMI-1`, `1f: Unrecognized value` — while `getvcp 60` on
   the live USB-C input returns `sl=0x13`, a value absent from that list. Conclusion: never
   build the input list from `capabilities` alone. The design calls for a per-monitor
   **learned code map**, keyed on the monitor serial from `ddcutil detect`, persisted to
   `~/.config/monswitch/config.toml`.

2. **`getvcp 60` prints `Invalid value (sl=0x13)`** for such codes. That is not an error to
   fix — `0x13` is the real current input. Parse the `sl=0x..` value, do not trust ddcutil's
   "Invalid value" verdict.

3. **Switching away from your own input is one-way.** The DDC/CI channel rides on the display
   link. Switch the monitor from your USB-C to DP and the monitor stops showing your machine
   *and* you lose the channel to switch it back. Recovery is physical OSD buttons. Therefore:
   the UI must mark the currently-active input and confirm before switching away from it.
   **Never issue such a `setvcp` without explicit user confirmation in the moment** — not
   even to test. Test with `getvcp` (read-only) instead.

4. **A Ulauncher extension runs as the user with no TTY**, so it can never prompt for a sudo
   password. The `i2c` group membership (or a sudoers rule scoped to `/usr/bin/ddcutil`) is a
   hard prerequisite for that path, not a convenience. Do not design around `sudo` prompts.

5. **`--display N` indices are not stable** across reboots or replugs. Resolve monitors by
   `--mfg`/`--model`/`--sn` or `--bus` fresh at each run.

6. **`ddcutil detect` is slow** — it probes every I²C bus. Cache results; prefer an explicit
   `--bus` on the write path.

7. Laptop internal panels (`eDP-*`) have no DDC/CI input switching. Filter them out.

## Dev machine

Ubuntu, kernel 7.0.0, Intel iGPU (`i915`). Internal panel `eDP-1`; HP monitor on `DP-1`,
which *is* the USB-C port in DP alt mode. `ddcutil` 1.4.1, Python 3.12, Ulauncher 5.16.2
(extension API v2 — manifest needs `"required_api_version": "^2.0.0"`).

Local setup state: `ddcutil` installed; `/dev/i2c-*` present. The user has **not** yet joined
the `i2c` group — they have been running `sudo ddcutil`. Confirm group state before assuming
non-root access works.

## Conventions

- Language: Python 3.12. GTK3 via PyGObject (`python3-gi`) for the picker.
- Keep a core module that both front-ends (CLI/GTK and Ulauncher) call. The Ulauncher
  extension must be a thin wrapper, not a reimplementation.
- Shell out to `ddcutil`; do not reimplement DDC/CI over raw I²C.

## Safety

Read operations (`detect`, `capabilities`, `getvcp`) are safe to run freely. `setvcp 60`
changes physical hardware state the user may not be able to undo from software — ask first,
every time. Other `setvcp` features (brightness, colour) are recoverable but still out of
scope; do not touch them.
