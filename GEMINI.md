# GEMINI.md — Agent Guidelines for MonitorSwitcher

This file provides context, architectural constraints, and safety guidelines for Gemini / Antigravity agents working in this repository.

---

## 1. Project Overview & Architecture

**MonitorSwitcher** is a Linux tool to switch an external monitor's **input source** over DDC/CI (MCCS VCP code `0x60`), including over USB-C DP alt-mode connections.

### Key Targets
1. `monswitch` CLI: Subcommands `list`, `set`, and the optional `learn`. Invoking `monswitch` with no arguments triggers a GTK3 popup picker.
2. **Ulauncher Extension**: A launcher plugin (API v2) wrapping the same core logic without a terminal.

### Architectural Constraint: Unified Core
All frontends (CLI, GTK3 popup, Ulauncher extension) **must** share a single core Python module (`src/monswitch/core/`):
- `ddc.py`: Subprocess wrapper for `ddcutil` output parsing and execution.
- `config.py`: Parser and manager for `~/.config/monswitch/config.toml`.
- `models.py`: Clean domain models (`Monitor`, `InputSource`, etc.).
Frontends must remain thin presentation wrappers over this shared core.

---

## 2. Hard-Won Probing Facts (Ground Truth)

Do not make assumptions contrary to these verified findings:

1. **`ddcutil capabilities` misreports vendor codes in both directions:**
   - On the reference HP Z38c panel, `capabilities` lists only `0f`, `11`, `1f`.
   - The live USB-C input code is `0x13`, which is completely absent from the capabilities table.
   - Conversely `1f` is reported as "Unrecognized value" — junk, not a usable input.
   - **Rule:** Never trust `capabilities` as the definitive list of available inputs. Per **ADR-0001**, build the input list by zero-config dynamic synthesis (live `getvcp 60` + `capabilities` + fallback code table); per **ADR-0009**, tag every entry with its provenance (`live` / `reported` / `guessed`) and gate switching on that tag. A mandatory `learn` calibration step is **not** part of the design — `learn` is an optional verification pass, and `config.toml` holds only overrides.
2. **`getvcp 60` output format:**
   - On vendor-specific inputs (like `0x13`), `ddcutil` reports:
     `VCP code 0x60 (Input Source ): Invalid value (sl=0x13)`
   - **Rule:** "Invalid value" is not an error. Parse `sl=0x..` directly via regex/string extraction.
3. **Addressing monitors:**
   - `--display N` indices are **unstable** across reboots and cable replugs.
   - Detect monitors by reading EDID synopsis (Mfg, Model, Serial number) and resolve to `--bus <N>`.
   - Cache bus numbers to avoid calling `ddcutil detect` repeatedly, but allow rediscovery if a cached bus fails.
4. **Internal displays:**
   - Laptop internal displays (`eDP-*`) do not support DDC/CI input switching. Filter them out during detection.
5. **Permissions & Sudo:**
   - Ulauncher runs in the user session without a TTY and cannot accept `sudo` prompts.
   - Non-root access via `/dev/i2c-*` (either user ACL/uaccess or membership in the `i2c` group) is required.
   - Verified on reference machine: `/dev/i2c-16` grants `user:devuser:rw-` via POSIX ACL.

---

## 3. Strict Safety Protocol

> [!CAUTION]
> **Switching away from the host's own input is a physical, one-way action.**
> When you switch the monitor away from the connected machine (e.g. from USB-C to DisplayPort), the display link and the DDC/CI channel terminate immediately. Software **cannot** switch it back. Recovery requires physically pressing the monitor's OSD buttons or switching back from another connected computer.

### Rules of Engagement:
1. **Never execute `ddcutil setvcp 60` without explicit, in-the-moment user confirmation.** This applies to you, the agent, unconditionally. The product's `confirm_switch = false` setting and the CLI's `-y` / `--yes` / `--force` flags (ADR-0002) are user affordances and never grant an agent permission to skip asking.
2. **Never issue `setvcp` during automated tests or test runs.**
3. Test purely with read-only commands: `ddcutil detect`, `capabilities`, and `getvcp 60`.
4. The UI and CLI must clearly indicate the currently active input and require affirmative confirmation before switching away from the machine's current connection.
5. Do not modify or issue commands for other VCP feature codes (brightness, contrast, color profiles, etc.).

---

## 4. Development & Tech Stack Conventions

- **Language:** Python 3.12+ (standard library `tomllib`, `argparse`, `dataclasses`).
- **GUI:** GTK 3 via PyGObject (`python3-gi`, `gir1.2-gtk-3.0`).
- **Launcher:** Ulauncher Extension API v2 (`"required_api_version": "^2.0.0"`).
- **Documentation:** All project documentation, ADRs (Architecture Decision Records), and design specifications must be saved in the `./docs/` directory (e.g., `./docs/adr/`).
- **Tooling:** `pytest` for unit testing string parsers using captured fixtures (no live hardware needed for testing parsers).
- **CLI Commands:**
  - `monswitch list` — print detected monitors, inputs, and active input.
  - `monswitch learn` — *optional* guided interactive workflow that verifies guessed input codes against the real panel and records the confirmed map to `config.toml`. Not a prerequisite for `list` or `set`.
  - `monswitch set <monitor> <input>` — switch input (with safety prompt if interactive).
  - `monswitch` (no args) — launch GTK3 popup picker.
