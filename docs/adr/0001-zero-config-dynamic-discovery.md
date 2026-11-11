# Zero-Config Dynamic Input Discovery over Mandatory `learn`

> **Status:** Superseded in part · **Date:** 2026-11-09
> **Refined by:** [ADR-0009](0009-input-provenance-and-trust-tiers.md)
> **Superseded in part by:** [ADR-0014](0014-report-only-what-the-monitor-says.md), which
> removes the fallback code table: it manufactured rows for inputs the panel never mentioned,
> and selecting one of those is the unrecoverable case. Also by
> [ADR-0015](0015-confirm-replaces-learn.md), which withdraws `learn` entirely — the link
> dies the moment the monitor leaves your input, so there is nothing left to observe.

Monitors often omit vendor codes (such as USB-C `0x13`) from their DDC/CI capabilities report. We decided to abandon the requirement for an upfront, mandatory `monswitch learn` calibration step in favor of zero-config dynamic discovery that merges the live input (`getvcp 60`), reported `capabilities`, and a fallback table of known VESA/vendor codes. `~/.config/monswitch/config.toml` is retained solely as an optional override for custom display names and input aliases.

### Considered Options
- **Mandatory `monswitch learn` wizard:** Required the user to physically cycle monitor inputs in a terminal wizard before the UI or launcher could function.
- **Zero-config dynamic synthesis (Chosen):** Works immediately on first invocation without configuration friction.
