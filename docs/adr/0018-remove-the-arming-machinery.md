# Remove the Arming Machinery

> **Status:** Accepted · **Date:** 2026-11-11
> **Supersedes in part:** [ADR-0008](0008-gtk-popup-picker-ux.md) (the two-step Enter, already
> withdrawn by ADR-0017), [ADR-0012](0012-packaging-and-module-resolution.md) (the provisional
> stale-snapshot marker) · **Follows:** [ADR-0017](0017-single-enter-in-the-graphical-front-ends.md)

ADR-0017 stopped the graphical front-ends asking for a second Enter, but left the mechanism in
place behind `require_confirmation`, defaulting to `True`, on the reasoning that this kind of
decision gets reversed. With no caller using it, that is dead code that merely looks live. It
is now removed.

## Decision

Deleted entirely:

- `PickerModel`'s `_armed` state, and `armed_row`, `is_armed`, `warning_for`, `disarm`,
  `arms_before_switching`, `require_confirmation`, and the `preserve_arm` parameter on
  `select`.
- `ActivationKind.ARM` and `ActionKind.ARM`. A caller can no longer branch on a state that
  cannot occur.
- `PickerRow.needs_confirmation`, `LauncherItem.is_warning`, `LauncherSession.rerender`, the
  armed branch of `_subtitle_for`, and the `.monswitch-warning` CSS class.
- The arming branches in the GTK key handler and the Ulauncher enter handler, and the
  Escape-disarms-before-closing behaviour, which now has nothing to cancel.

`PickerModel.activate` reduces to: nothing selected → `NOTHING`; monitor unreadable →
`BLOCKED`; otherwise `SWITCH`.

Kept deliberately:

- **The `BLOCKED` refusal.** Failing closed on a monitor whose current input could not be read
  is not a confirmation step — it exists because the consequences cannot be assessed at all
  (ADR-0013) — so it survives the removal of all asking.
- **`switch_confirmation` and `PickerRow.confirmation`.** The verdict is still computed and
  still correct. It is what the row's `unverified` and `current` notes are drawn from, and the
  CLI still acts on it. Only the graphical front-ends ignore it.
- **The query/activation split in `LauncherSession`.** Its original reason is gone — Enter no
  longer has arming to lose — but re-querying on activation would still reset the selection out
  from under the row being acted on.

## Consequences

- **ADR-0012's stale-snapshot protection is now inert.** It built the model with confirmation
  forced on when discovery could not be refreshed, so that stale data could not combine with
  an opt-out to switch away from the Host Input unwarned. With nothing acting on the verdict in
  the launcher, that protection no longer does anything: a stale row switches like any other.
  The model is still built the conservative way and a test pins it, because the CLI reads the
  same verdict — but the guarantee ADR-0012 described is gone, and saying so here is better
  than leaving a test that implies otherwise.
- 49 tests that covered arming were deleted, and 22 added for what replaced them: that every
  row switches on the first activation regardless of provenance, that repeated presses do not
  accumulate state, that an unreadable monitor is still refused, that `ARM` no longer exists in
  either enum, and that the front-end sources contain no arming references.
- Restoring confirmation later means writing it again rather than flipping a flag. That is the
  honest cost, and it is lower than it looks: the policy that decides *whether* confirmation is
  warranted is untouched, so what would need rebuilding is only the interaction.

## A note on how this was done

The removal was carried out with scripted edits, and two of them used `str.index` to find the
end of a block — which returned the *first* match in the file rather than the one after the
start, silently duplicating a region. It happened twice: once producing two `LauncherSession`
definitions, and once moving `DiscoverySnapshot.invalidate` and `.model` inside
`LauncherSession` while deleting the session's own `invalidate`. The first was caught by `ruff`
(F811 redefinition), the second only by a test calling the method that had vanished. Both are
repaired. Worth recording because a scripted refactor that *mostly* works is the kind that
ships: the lint and test gates, not the script, were what caught it.
