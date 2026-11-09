# Serial-Keyed TOML Configuration Schema

> **Status:** Superseded in part · **Date:** 2026-11-09
> **Superseded in part by:** [ADR-0013](0013-identity-host-input-and-target-resolution.md)

MonitorSwitcher supports optional user customizations for friendly display names, input aliases, and safety preferences in `~/.config/monswitch/config.toml`.

We decided on a serial-keyed nested dictionary schema:
- Global settings (like `confirm_switch = true`) live at the root table.
- Monitor overrides are keyed by hardware serial number (e.g. `[monitors."6CM9181R3N"]`), preventing collisions between identical models. (ADR-0013 generalises this to a fallback chain for panels that report no serial, and defines what happens when two panels collide on a key.)
- Input mappings are declared as a flat dictionary of hex code to friendly name (`"0x13" = "Work Laptop"`).

### Example

The serial below is illustrative. The reference HP Z38c's actual serial is `DEMOSERIAL`, read
from the EDID `0xff` descriptor block and not from the EDID offset-12 serial field, which reads
`0x01010101` on that panel (see ADR-0010).

```toml
confirm_switch = true

[monitors."6CM9181R3N"]
name = "HP UltraWide"

[monitors."6CM9181R3N".inputs]
"0x13" = "Work Laptop"
"0x0f" = "Desktop PC"
"0x11" = "PlayStation 5"
```

`host_input` was removed from this schema by ADR-0013: a successful `getvcp 60` read proves
the value it returns *is* the Host Input, so storing it adds a source that can go stale and be
trusted by the confirmation logic.

### Considered Options
- **Array of tables (`[[monitors]]`):** Verbose and awkward to edit by hand in TOML.
- **Model-name keys (`[monitors."HP Z38c"]`):** Fragile when two identical monitor panels are connected.
- **Serial-keyed dictionary (Chosen):** Cleanest syntax for human editing, guaranteed uniqueness.
