# Input Provenance Tracking and Trust-Tiered Confirmation

> **Status:** Superseded in part · **Date:** 2026-11-09
> **Refines:** [ADR-0001](0001-zero-config-dynamic-discovery.md), [ADR-0002](0002-switch-confirmation-and-safety.md), [ADR-0008](0008-gtk-popup-picker-ux.md)
> **Superseded in part by:** [ADR-0015](0015-confirm-replaces-learn.md), which withdraws
> `learn` — the promotion path named below — as unbuildable, and replaces it with
> `monswitch confirm`. The tiers themselves stand unchanged.

ADR-0001 replaced the mandatory `learn` calibration step with zero-config dynamic discovery:
the input list is synthesised by merging the live `getvcp 60` value, the `capabilities` report,
and a fallback table of known VESA/vendor codes. That removed real friction, but it also
removed the only mechanism that proved a code corresponds to a physical port — and it did so
for a tool whose primary action is irreversible from software (ADR-0002).

The merge is unreliable in three distinct ways, all observed on the reference HP Z38c:

- **`capabilities` omits real inputs.** The live USB-C input is `0x13`, absent from the report.
- **`capabilities` contains entries that are not usable inputs.** It lists `1f` as
  "Unrecognized value".
- **The fallback table is vendor-specific but applied generically.** `0x13` means USB-C on HP;
  on another vendor's panel the same byte may mean something else or nothing at all.

So a synthesised list can contain a phantom input (nothing is attached to it, or the code is
not a port at all) and a mislabelled input (the name shown comes from a guess, not the panel).
Selecting either, when the current selection is the Host Input, switches the monitor to a dead
or wrong source *and* kills the DDC/CI channel. Recovery is the monitor's physical OSD buttons.
Treating a guessed entry and an observed entry as interchangeable in the UI therefore converts
a labelling weakness into an unrecoverable hardware state.

We decided to make provenance a first-class attribute of every `InputSource` and to tier
confirmation on it.

### Provenance tiers

Each discovered `InputSource` carries exactly one provenance, highest wins when sources are
merged:

| Provenance   | Origin                                                                       | Meaning                                              |
| ------------ | ---------------------------------------------------------------------------- | ---------------------------------------------------- |
| `confirmed`  | User-confirmed via `monswitch learn`, or an explicit `[monitors.*.inputs]` entry in `config.toml` | The code is known to drive a real port on this panel |
| `live`       | Returned by `getvcp 60` as the current value                                  | The code is real; it is driving this panel right now |
| `reported`   | Listed in `capabilities` with a recognised name                               | The firmware admits it; existence plausible, unverified |
| `guessed`    | Present only in the built-in fallback code table                              | Inferred from other vendors' panels; may not exist    |

A code reported by `capabilities` as `Unrecognized value` (e.g. `1f`) is **not** promoted to
`reported`; it is either dropped or carried as `guessed`, never shown with an invented name.
Once `getvcp 60` observes a code, that code is `live` regardless of whether `capabilities`
mentioned it — this is what makes `0x13` appear at all. A `confirmed` entry outranks everything
and also supplies the display name.

### Decisions

1. **The domain model carries provenance.** `InputSource` gains a provenance field; it is not a
   presentation detail computed in a frontend. Discovery in the shared core returns it, so the
   CLI, GTK picker and Ulauncher extension cannot each invent their own notion of trust.
2. **Provenance is visible in every frontend.** `confirmed` and `live` inputs render plainly.
   `reported` and `guessed` inputs are visually marked as unverified — a badge in the GTK
   picker and Ulauncher list, a marker column in `monswitch list`. A user must never be unable
   to tell a guess from a fact.
3. **Confirmation is tiered by provenance, not only by Host Input.** ADR-0002 requires
   confirmation when switching *away from* the Host Input. This ADR adds a second axis — how
   trustworthy the *destination* is:
   - Destination `confirmed` or `live`, not leaving the Host Input → no prompt.
   - Leaving the Host Input, destination `confirmed` or `live` → the ADR-0002 confirmation.
   - Destination `reported` or `guessed` → confirmation that names the risk specifically
     ("this input was inferred, not verified; the monitor may go dark with no way back"),
     **even when `confirm_switch = false`**.
4. **`confirm_switch = false` does not silence the unverified-destination warning.** The opt-out
   in ADR-0002 exists so power users can switch quickly between inputs they use daily, which
   are `confirmed` or `live` by construction. It is not an assertion that guessed codes are
   safe. The only way to make a `guessed` input prompt-free is to confirm it — via `learn` or a
   `config.toml` entry — which is exactly the information the tool lacks.
5. **`learn` is the promotion path, and stays optional.** `monswitch learn` walks the user
   through cycling inputs on the OSD while reading `getvcp 60`, and writes the resulting map to
   `config.toml` as `confirmed`. It is never a prerequisite for `list` or `set`; it is how a
   user retires the warnings on a panel they use often. Codes present in the fallback table but
   never observed during a completed `learn` pass are recorded as absent and dropped from that
   monitor's list.
6. **Agent rule unchanged and unconditional.** None of this tiering grants an automated agent
   permission to issue `setvcp 60`. That requires explicit, in-the-moment human confirmation
   regardless of provenance or configuration.

### Considered Options

- **No provenance; show the merged list flat (status quo after ADR-0001):** Simplest model and
  the fastest UI. Rejected: it presents guesses and observations identically, so the user's
  only defence against an unrecoverable switch is a label that may be fabricated.
- **Show only `live` and `confirmed` inputs; hide everything unverified:** Safe by
  construction, and needs no new UI vocabulary. Rejected because on a cold first run only the
  Host Input is `live`, so the picker would show a single entry and the tool would appear
  broken — reintroducing the mandatory-calibration friction ADR-0001 set out to remove, with
  worse ergonomics.
- **Restore mandatory `learn` before `set` is usable:** Maximum safety. Rejected as a direct
  reversal of ADR-0001; the friction is real and most switches target inputs the panel already
  advertises correctly.
- **Probe each candidate code by writing it and reading it back:** Would turn guesses into
  facts automatically. Rejected outright — probing *is* switching. The write is the
  irreversible act, so a verification pass built on `setvcp` is the exact failure it would be
  trying to prevent.
- **Provenance tracking with tiered confirmation (Chosen):** Keeps ADR-0001's zero-config first
  run, while ensuring the irreversible action is gated in proportion to how much the tool
  actually knows about its destination.

### Consequences

- Discovery, config parsing and the merge logic all gain a provenance dimension; merge
  precedence (`confirmed` > `live` > `reported` > `guessed`) must be covered by parser unit
  tests, which stay hardware-free per the existing testing convention.
- All three frontends need an unverified affordance, including Ulauncher, where the
  double-`Enter` pattern from ADR-0008 is the natural fit.
- ADR-0002 is refined, not superseded: its default-on confirmation and `confirm_switch` opt-out
  stand, with the opt-out now explicitly scoped to trusted destinations.
- First-run UX on an unknown panel is noisier than ADR-0001 alone implies: several inputs will
  carry an unverified badge until the user runs `learn`. This is the intended trade — the noise
  is proportional to the genuine uncertainty.
