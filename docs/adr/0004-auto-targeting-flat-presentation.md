# Auto-Targeting and Flat Badge Presentation

> **Status:** Accepted · **Date:** 2026-11-09
> **Refined by:** [ADR-0013](0013-identity-host-input-and-target-resolution.md)

Multi-monitor users need to select which monitor to switch, while single-monitor users find specifying the monitor on every command cumbersome. Additionally, launcher users benefit from fuzzy-searching both monitor and input in a single query.

We decided to implement auto-targeting with flat badge presentation:
- When only one external DDC/CI monitor is detected, commands automatically target it without requiring a monitor argument (e.g. `monswitch set dp1`).
- When multiple monitors are present, the CLI requires or prompts for the target monitor, and the Ulauncher/GTK interfaces present a unified flat list prefixed by monitor badges (e.g. `[HP Z38c] DisplayPort 1`), enabling single-query fuzzy searching.

### Considered Options
- **Strict two-stage drilldown:** Forces users to choose a monitor before viewing inputs, adding friction to single-monitor setups and slowing down launcher workflows.
- **Single-monitor only:** Simpler, but permanently locks out multi-monitor users.
- **Auto-targeting & flat presentation (Chosen):** Optimizes single-monitor common case while enabling fast single-query fuzzy matching for multi-monitor setups.
