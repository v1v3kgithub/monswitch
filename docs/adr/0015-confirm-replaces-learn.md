# `confirm` Replaces `learn`

> **Status:** Accepted · **Date:** 2026-11-11
> **Supersedes in part:** [ADR-0001](0001-zero-config-dynamic-discovery.md),
> [ADR-0009](0009-input-provenance-and-trust-tiers.md) (both describe `learn` as the promotion
> path to `confirmed`)

`learn` was specified in ADR-0001 and relied on by ADR-0009 as the only route to the
`confirmed` tier. It was never built, and it cannot be built as specified. It is withdrawn and
replaced by `monswitch confirm`.

## Why `learn` cannot work

The design was a guided walk-through: poll `getvcp 60` while the user cycles inputs on the
monitor's own buttons, and record each value as it changes. The README suggested
`watch -n1 ddcutil getvcp 60` as the manual form of the same idea.

That requires the DDC/CI channel to survive the monitor switching to another input. It does
not. Measured on the reference machine while the panel was showing DisplayPort from a
different computer:

```
card1-DP-1   status=disconnected   ddc=i2c-16   edid_bytes=0
ddcutil --bus 16 getvcp 60  →  No monitor detected on bus /dev/i2c-16
```

The kernel reports the connector **disconnected** and reads zero bytes of EDID. The monitor
did not merely stop displaying this machine; it tore down the link, and DDC/CI rides that
link. So the instant the user selects another input — the exact moment `learn` needs to
observe — the tool goes blind. It would finish having confirmed only the code it started on,
which discovery already reports for free as `live`.

This is the same one-way property ADR-0013 reasoned about, observed from the far side. It had
been understood as "you cannot switch back", and its consequence for `learn` went unnoticed
until a user asked what `learn` was.

Whether the link survives an input change is monitor- and transport-dependent; some panels
keep the upstream receiver powered. So `learn` is not universally impossible — merely
impossible on the hardware this project is developed against, and unknowable in advance for
anyone else. A feature whose central mechanism works on an unpredictable subset of monitors is
not a feature.

No software escape was found. `ddcutil` here is built with USB display support, which on some
panels allows control over the USB upstream cable instead of the video link, but no
`/dev/usb/hiddev*` device exists and `detect --verbose` reports no USB displays.

## Decision

**`learn` is withdrawn.** It is not deferred, and should not be reintroduced without first
establishing that the target hardware keeps DDC/CI alive across an input change.

**`monswitch confirm [<monitor>] <code> [--as NAME]` replaces it.** It records the user's
assertion that a code drives a real port, rather than trying to observe it. The user is in a
position to know: they switched to that input successfully, by whatever means.

Details that follow from the purpose:

1. **It does not require the code to have been reported by the monitor.** ADR-0014 removed
   speculative rows, so a code the firmware omits now appears nowhere — and that is exactly
   the code a user most needs to record. Confirming is how it gets in.
2. **It prints the config snippet by default and writes only with `--write`.** `tomllib`
   reads but does not write, so rendering is hand-rolled and cannot preserve comments or keys
   this version does not know. Rewriting a hand-edited file is not a side effect to take
   unasked. `--write` keeps the previous file as `.bak`.
3. **It consults `capabilities`**, unlike the launcher path. A deliberate one-off command can
   afford the ~3.1s, and it supplies the name to reuse when `--as` is omitted.
4. **It refuses when two connected monitors share an identity key.** Their overrides would be
   withheld anyway (ADR-0013), so writing would be a silent no-op. Note such monitors are also
   indistinguishable to the `-m` selector, so the message names that obstacle rather than
   asking the user to pick one — advice they could not follow.

## Considered Options

- **Build `learn` anyway, working only where DDC/CI survives:** rejected. It would appear to
  work on some monitors and silently do nothing on others, with no way to tell in advance
  which the user has.
- **`learn` by trial:** write a candidate code and see what happens. Rejected outright —
  probing *is* switching, so a verification pass built on `setvcp` is the exact unrecoverable
  failure it would be trying to prevent (already rejected in ADR-0009).
- **Read the code from the other machine:** running `monswitch list` on the computer attached
  to the other input reports that input's code as `live`, which is a genuine observation and
  the strongest evidence available. Not rejected — it is good practice, and it needs no
  feature. It requires the tool on both machines, so it cannot be the only route.
- **`confirm`, recording the user's assertion (Chosen):** no observation needed, works on any
  hardware, and is honest about being a human's claim — which is what the `confirmed` tier
  has always meant.

## Consequences

- The `confirmed` tier is now reachable on every monitor, which it was not before: on the
  reference hardware nothing could ever have been promoted past `reported`.
- `confirm` writes per *unit* (the ADR-0013 identity key), so a user who hot-desks must
  re-confirm at each new monitor. Input codes are a property of the panel's firmware rather
  than the individual unit, so a model-keyed map would fix this; it was raised with the user
  and deferred, and remains the obvious next improvement.
- Every reference to `learn` in the UI, README and agent brief is now wrong and is updated.
  ADR-0001 and ADR-0009 keep their original text, as the conventions require, with their
  status amended here.
