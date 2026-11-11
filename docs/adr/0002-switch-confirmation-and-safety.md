# Configurable Host Disconnect Confirmation

> **Status:** Accepted · **Date:** 2026-11-09
> **Refined by:** [ADR-0009](0009-input-provenance-and-trust-tiers.md)
> **Superseded in part by:** [ADR-0017](0017-single-enter-in-the-graphical-front-ends.md) —
> the graphical front-ends no longer confirm before leaving the Host Input. The CLI does.

Switching away from the Host Input (the connection currently driving the monitor from this machine) breaks the physical DDC/CI channel, making software recovery impossible without physical monitor button access.

We decided to require explicit confirmation by default across all frontends (CLI, GTK picker, and Ulauncher) before issuing a `setvcp 60` command away from the Host Input. Power users can disable this confirmation globally by setting `confirm_switch = false` in `~/.config/monswitch/config.toml`, and CLI scripts can bypass it with `-y` / `--yes` / `--force`.

### Considered Options
- **One-click instant switch:** Fastest UX, but high risk of accidental disconnects from stray keypresses in Ulauncher.
- **Mandatory prompt with no override:** Safest, but frustrates experienced users who want instant switching shortcuts.
- **Configurable safety mode (Chosen):** Safe by default with opt-out configuration.
