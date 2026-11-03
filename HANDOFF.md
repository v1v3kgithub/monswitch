# Handoff — MonitorSwitcher

Written 2026-11-03. Focus of the next session: `/home/devuser/Development/MonitorSwitcher`.

## Read these first, in order

1. `CLAUDE.md` — the durable agent brief: mechanism, the hard-won probing facts, conventions,
   safety rules. **Everything substantive lives there; this file does not repeat it.**
2. `README.md` — user-facing install/prereqs, the input-code table, the roadmap checklist.
3. `git log` (2 commits: `87e525b`, `a491807`) — commit bodies record why each fact was captured.

## Where the work stands

Documentation only. No code. The roadmap checklist in `README.md` is the live task list; nothing
on it is started.

## Where to start

Build `monswitch learn` first, then `monswitch list`.

`learn` is the highest-value piece because of the capabilities-under-reporting problem
(see `CLAUDE.md`, fact 1): the real input codes can only be discovered by reading `getvcp 60`
while the user cycles inputs on the monitor's physical OSD. So `learn` is a guided,
interactive walk-through that records the true code map and persists it to
`~/.config/monswitch/config.toml`. `list`, the GTK picker and the Ulauncher extension all
depend on having that map.

Open design questions not yet decided:

- Config schema for the learned code map (keyed on monitor serial — shape not specified yet).
- Whether `learn` polls `getvcp` on a timer or prompts per input.
- Project layout and packaging (no `pyproject.toml`, no source tree yet).

## Conversation context not in the artifacts

- The project grew out of a concrete goal: the user's laptop drives an HP monitor over USB-C
  and they want to flip the monitor to its DisplayPort input from software.
- They were running `ddcutil` via `sudo` and asked whether that was sufficient — it is, for
  manual CLI use. That is why `CLAUDE.md` flags the `i2c` group as a *Ulauncher-path*
  requirement specifically, and why local group state must be re-verified rather than assumed.
- No `setvcp` has ever been issued on this machine. All probing was read-only. Keep it that
  way without explicit, in-the-moment confirmation (`CLAUDE.md`, fact 3).

## Suggested skills

- `mattpocock-skills:domain-modeling` — before writing code, pin the vocabulary: *display* vs
  *monitor* vs *input source* vs *VCP value* vs *learned code map*. These blur easily and the
  config schema depends on getting them straight.
- `mattpocock-skills:codebase-design` — for the core/front-end seam. `CLAUDE.md` mandates one
  shared core with the CLI, GTK picker and Ulauncher extension as thin wrappers; that seam is
  worth designing deliberately rather than discovering.
- `mattpocock-skills:tdd` — the `ddcutil` output parsers (`detect`, `capabilities`, `getvcp`
  including the `Invalid value (sl=0x..)` case) are pure string-in/data-out functions with
  real captured sample output available in `CLAUDE.md` and `README.md`. Ideal test-first
  targets, and they need no hardware.
- `mattpocock-skills:prototype` — if the `learn` interaction flow feels uncertain, prototype
  it as a terminal app before committing to the GTK UI.
- `mattpocock-skills:wizard` — candidate for the one-time host setup (`i2c-dev` module, `i2c`
  group, OSD DDC/CI toggle, logout/login), since those are steps only the human can perform.

## Note on this file's location

The handoff skill specifies the OS temp directory. Written here instead, at the user's explicit
request, so it is found when a session starts in this folder. It is committed to the repo.
