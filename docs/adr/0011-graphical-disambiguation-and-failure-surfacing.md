# Graphical Disambiguation and Failure Surfacing

> **Status:** Accepted · **Date:** 2026-11-09
> **Refines:** [ADR-0006](0006-input-resolution-and-disambiguation.md), [ADR-0008](0008-gtk-popup-picker-ux.md)

> ADR-0006 specified resolution for terminal contexts only; this ADR adds the graphical ones.

ADR-0006 branches on `sys.stdin.isatty()`: interactive terminals get a numbered selection
prompt for an ambiguous input query, and non-interactive contexts "immediately abort with exit
code 1 and list candidate matches to stderr".

Both of the graphical frontends are non-interactive by that test. The GTK popup is launched
from a hotkey or desktop entry with no controlling terminal; the Ulauncher extension runs
inside a daemon. So under ADR-0006 as written, an ambiguous query — or any resolution failure —
takes the scripted branch: the process exits 1 and writes candidates to a stderr nobody is
watching. The user presses Enter and the picker closes with no monitor change and no
explanation. The same silent-failure path swallows every other error class too: DDC/CI disabled
in the OSD, no connected external monitor, a `ddcutil` write that fails.

The root problem is that `isatty()` conflates two different questions — *can I ask the user
something?* and *is a human watching?* A GTK picker answers no to the first and yes to the
second.

### Decisions

1. **Resolution context is an explicit capability, not an `isatty()` guess.** The shared core
   is handed an interaction strategy by its caller. `isatty()` is consulted only by the CLI
   frontend, to decide which of the terminal strategies to construct — it never appears in core
   resolution logic.

   | Context                | Can prompt | Human watching | Ambiguity behaviour                     |
   | ---------------------- | ---------- | -------------- | --------------------------------------- |
   | Interactive terminal   | yes        | yes            | Numbered prompt (ADR-0006, unchanged)   |
   | Script / pipe          | no         | no             | Abort, exit 1, candidates to stderr (ADR-0006, unchanged) |
   | GTK picker             | yes        | yes            | Narrow the list in place                |
   | Ulauncher              | yes        | yes            | Return candidates as the result set     |

2. **Graphical ambiguity narrows, it does not fail.** Ambiguity in a picker is not an error
   state — it is the normal condition of a list that has not been filtered yet. When a query
   matches several inputs, the GTK picker keeps the popup open with the matches shown and the
   selection on the first; the Ulauncher extension returns the matches as its result items.
   Neither aborts, and neither picks one implicitly, which ADR-0006 already rejected as
   dangerous.
3. **Every error reaching a graphical frontend is rendered in that frontend.** The core raises
   typed domain errors rather than writing to stderr or calling `sys.exit`. Each frontend
   renders them: the CLI to stderr with an exit code, the GTK picker as an in-window message
   row, Ulauncher as a non-actionable result item carrying the message. Exiting silently is
   never an acceptable graphical outcome.
4. **Actionable errors say what to do.** The three failure modes known from probing get
   specific text, not a generic failure: no external monitor detected suggests checking the
   cable and that DDC/CI is enabled in the monitor's OSD; a permission failure on `/dev/i2c-*`
   points at the non-root access routes in `README.md`; a `ddcutil` write failure states that
   the monitor may not have switched and that the current input can be re-read with
   `monswitch list`.
5. **`setvcp` is never fire-and-forget silently.** A write that `ddcutil` reports as failed
   surfaces in the frontend. A write that succeeds is not re-read to confirm — when the switch
   worked and it left the Host Input, the DDC/CI channel is gone by definition (ADR-0002), so
   absence of confirmation is expected and must not be reported as an error.

### Considered Options

- **Keep the `isatty()` split and let graphical frontends inherit the scripted branch
  (ADR-0006 as written):** No new abstraction, but it silently does nothing in the two
  frontends that are the product's whole point.
- **Force a pseudo-terminal, or prompt via a separate terminal window:** Would let the existing
  numbered prompt be reused verbatim in graphical contexts. Rejected as absurd UX for a
  Spotlight-style picker (ADR-0008) and impossible in the Ulauncher daemon.
- **Let the core render errors itself, branching on frontend:** Puts presentation in the core
  and violates the thin-wrapper constraint; the core would need to know about GTK and
  Ulauncher's result API.
- **Explicit interaction strategy with typed errors rendered per frontend (Chosen):** Keeps the
  core presentation-free, makes the terminal/graphical distinction a deliberate choice by the
  caller, and makes silent failure structurally impossible.

### Consequences

- The core grows a small typed-error hierarchy and an interaction-strategy seam. Both are
  testable without hardware by driving the core with a fake strategy.
- ADR-0006's two terminal behaviours are preserved exactly; this ADR only adds the graphical
  cases and removes `isatty()` from the core.
- The GTK picker needs an in-window message row and the Ulauncher extension a non-actionable
  item type, both of which they need anyway for the unverified-input badge of ADR-0009.
