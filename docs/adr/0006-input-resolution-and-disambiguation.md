# Input Resolution and Disambiguation Strategy

When invoking `monswitch set <input>`, the target input can be specified in multiple formats: raw hexadecimal VCP codes (`0x0f`, `15`), normalized standard slugs (`dp`, `dp1`, `hdmi`, `usbc`), or custom nicknames defined in `~/.config/monswitch/config.toml`.

We decided on a prioritized lookup order with context-aware disambiguation:
1. Lookup priority: Custom user nicknames → Normalized built-in slugs → Hexadecimal / Decimal codes.
2. In interactive terminals (`sys.stdin.isatty()`): If a query matches multiple inputs (e.g. `dp` matching both `dp1` and `dp2`), the CLI presents an interactive numbered selection prompt.
3. In non-interactive contexts (scripts, pipes, shortcuts): Ambiguous queries immediately abort with exit code 1 and list candidate matches to stderr, guaranteeing deterministic script execution.

### Considered Options
- **Strict error always:** Safe, but forces interactive terminal users to re-run commands with exact syntax.
- **Greedy first match:** Non-deterministic and dangerous; could switch the monitor to the wrong physical port without user awareness.
- **Context-aware disambiguation (Chosen):** Interactive convenience for manual terminal usage, strict failure for scripted automations.
