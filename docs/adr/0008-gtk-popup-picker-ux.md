# GTK3 Spotlight-Style Popup Picker UX

> **Status:** Accepted · **Date:** 2026-11-09
> **Refined by:** [ADR-0009](0009-input-provenance-and-trust-tiers.md), [ADR-0011](0011-graphical-disambiguation-and-failure-surfacing.md)

When `monswitch` is invoked without arguments (e.g. bound to a system hotkey like `Super+M`), it opens a graphical picker for fast input switching.

We decided on a minimal, keyboard-first Spotlight/Rofi-style popup:
- Frameless, centered window dismissed on `Escape` or loss of focus.
- Keyboard-first interaction: Up/Down arrow keys or typing to filter, `Enter` to select.
- Visual state: Current active host input is visually badged `(Active / Host)`.
- Safety: If `confirm_switch = true` and switching away from the host, the first `Enter` transitions the item to an inline confirmation warning (*"Press Enter again to switch away from Host"*).

### Considered Options
- **Traditional window with OK/Cancel buttons:** Clunky for quick keyboard-driven workflows.
- **System tray icon:** Consumes persistent background memory and requires mouse interaction.
- **Spotlight/Rofi-style popup (Chosen):** Instantaneous, zero background footprint, keyboard-driven.
