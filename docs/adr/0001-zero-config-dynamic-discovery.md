# Zero-Config Dynamic Input Discovery over Mandatory `learn`

Monitors often omit vendor codes (such as USB-C `0x13`) from their DDC/CI capabilities report. We decided to abandon the requirement for an upfront, mandatory `monswitch learn` calibration step in favor of zero-config dynamic discovery that merges the live input (`getvcp 60`), reported `capabilities`, and a fallback table of known VESA/vendor codes. `~/.config/monswitch/config.toml` is retained solely as an optional override for custom display names and input aliases.

### Considered Options
- **Mandatory `monswitch learn` wizard:** Required the user to physically cycle monitor inputs in a terminal wizard before the UI or launcher could function.
- **Zero-config dynamic synthesis (Chosen):** Works immediately on first invocation without configuration friction.
