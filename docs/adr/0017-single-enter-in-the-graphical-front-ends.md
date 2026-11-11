# Single Enter in the Graphical Front-Ends

> **Status:** Accepted · **Date:** 2026-11-11
> **Supersedes in part:** [ADR-0008](0008-gtk-popup-picker-ux.md) (the two-step Enter),
> [ADR-0009](0009-input-provenance-and-trust-tiers.md) (an unverified destination always
> prompts), [ADR-0002](0002-switch-confirmation-and-safety.md) (confirmation before leaving
> the Host Input) — **in the graphical front-ends only**

The GTK picker and the Ulauncher extension now switch on the first Enter. The two-step arming
of ADR-0008 is withdrawn from both. **This is the user's decision, taken against the
recommendation recorded here.**

## What was traded away

The arming step existed because a popup cannot show a modal dialog without ceasing to be a
popup, and because two separate rules wanted a deliberate second act: ADR-0002 for leaving the
Host Input, and ADR-0009 for a destination whose existence is unverified.

Both risks are unchanged by this decision. Switching away from this machine still tears down
the display link and the DDC/CI channel with it, so it cannot be undone from software —
recovery is the monitor's physical buttons. An input code the panel does not implement still
blanks the screen. What changes is that a single keypress can now cause either, on a row the
user may have arrowed onto by accident.

The case against keeping it, which the user made and which is sound: the warning fires on
every non-current input, on every monitor, forever — because nothing is ever `confirmed` on
hardware met for the first time, and the user hot-desks. A confirmation that always fires is a
confirmation nobody reads, and the keypress it costs buys attention it no longer has. The
earlier phrasing of the prompt was also misread as a refusal rather than a question
(see the ADR-0014 era fixes), which is evidence it was not communicating.

## Decision

1. **`PickerModel` gains `require_confirmation`, default `True`.** The arming machinery stays,
   keeps its tests, and remains the default for any caller that wants it. Only the front-ends
   opt out, and they do so explicitly at the call site rather than by changing a default, so
   the choice is visible in the code that made it.
2. **The GTK picker and the Ulauncher extension pass `require_confirmation=False`.** Source
   guards in the tests assert they do, so the behaviour cannot drift back silently.
3. **Nothing else changes.** In particular:
   - A monitor whose current input could not be read is still refused outright (ADR-0013).
     Failing closed is not a confirmation step: it exists because the consequences cannot be
     assessed at all, and removing the asking must not remove the refusing.
   - Rows still show provenance. `unverified` still appears in the picker and in the launcher
     subtitles; the information survives, only the keypress is gone.
   - `switch_confirmation` still returns `LEAVING_HOST` and `UNVERIFIED_DESTINATION`
     correctly. The policy still knows the switch is irreversible; the graphical front-ends
     simply no longer ask about it.
   - **The CLI is unchanged.** `monswitch set` still prompts on a terminal, still requires
     `--yes` for a verified destination and `--force` for an unverified one. A terminal can
     show a full warning without the cost that makes a popup unusable, and a script that
     switches unasked is a different hazard.
4. **An agent still may not issue `setvcp 60` without explicit, in-the-moment human
   confirmation.** This ADR changes what the product asks of its user, not what an automated
   caller may do on its behalf.

## Considered Options

- **Keep the two-step Enter:** safest, and the user reports it as friction they will not
  tolerate daily. A safety measure the user routes around is worse than one that was never
  there, because it also carries the illusion of protection.
- **Confirm the inputs instead, then set `confirm_switch = false`:** the designed path to one
  keypress, and it works — but per ADR-0015 `confirm` records per *unit*, so a hot-desking
  user redoes it at every new monitor. Recommended to the user and rejected on that basis. It
  remains the better answer if confirmation ever becomes model-keyed.
- **Single Enter for inputs the monitor reported, arming only for guessed ones:** a middle
  ground, and after ADR-0014 removed speculative rows almost everything in the list is
  reported — so it would have removed the warning in nearly every real case while keeping the
  machinery, i.e. the cost without the benefit.
- **A timed confirmation, or a modifier key:** more mechanism for a user who has said the
  mechanism is the problem.
- **Single Enter in the graphical front-ends (Chosen).**

## Consequences

- **A single keypress in the picker or launcher can now blank the monitor**, recoverable only
  from its physical buttons. This is the understood and accepted cost.
- Arrow-key navigation is now one keypress from acting. There is no undo, and the picker
  closes on the switch, so the user's own attention is the only remaining guard.
- The arming code is now exercised only by tests and by the default-`True` path. If it is
  never used by a front-end again it should be removed rather than left as plausible-looking
  dead code — but it is the kind of decision that gets reversed, so it stays for now.
- Every document promising a second Enter is now wrong and is updated.
