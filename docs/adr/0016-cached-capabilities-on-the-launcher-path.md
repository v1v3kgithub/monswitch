# Cached `capabilities` on the Launcher Path

> **Status:** Accepted · **Date:** 2026-11-11
> **Supersedes in part:** [ADR-0010](0010-sysfs-first-monitor-resolution.md) (decision 5,
> "`capabilities` is never on the interactive path")
> **Refines:** [ADR-0014](0014-report-only-what-the-monitor-says.md),
> [ADR-0012](0012-packaging-and-module-resolution.md)

Two earlier decisions became contradictory, and the contradiction shipped.

ADR-0010 decision 5 kept `ddcutil capabilities` off interactive paths because it costs 3.1s.
ADR-0014 then removed the built-in fallback code table, which made `capabilities` the **only**
source of inputs other than the one currently live. The Ulauncher extension honoured the first
and was written before the second, so after ADR-0014 it asked for discovery without
capabilities and rendered exactly one row: the input the user is already on.

A user reported it immediately: typing the keyword showed the current monitor and no
DisplayPort to switch to. Measured:

```
discover(include_capabilities=False)  ->  ['0x13']
discover(include_capabilities=True)   ->  ['0x0f', '0x11', '0x13', '0x1f']
```

Neither ADR was wrong on its own. ADR-0010's cost concern is real — 3.1s on a keystroke is
unusable. ADR-0014's removal is right — inventing rows is worse than having few. What was
missing is that the second decision changed what the first was trading away: before ADR-0014,
skipping capabilities cost some names and a few plausible rows; after it, skipping capabilities
costs the entire list.

## Decision

**`capabilities` is consulted on the launcher path, through a cache keyed on monitor
identity.**

What makes this safe to do is that `capabilities` describes the panel's firmware, not this
session: the same monitor answers identically every time. So it is probed once per monitor and
remembered in `~/.cache/monswitch/capabilities.json`, keyed on the ADR-0013 identity key. The
3.1s is paid the first time a monitor is met and never again — which also suits a user who
moves between desks, since each new monitor costs exactly one slow query, ever.

Details that matter:

1. **The cache stores what the firmware reported, never a provenance decision.** Cached
   entries re-enter discovery as `REPORTED`, exactly as a fresh probe would, and the named and
   unnamed codes are kept apart so ADR-0009's rule against naming a disclaimed code survives
   the round trip. Nothing in the cache can promote an input past the confirmation warning.
2. **Every cache failure is a miss, not an error.** A missing, corrupt, unreadable or
   unwritable cache behaves as though nothing were cached. The worst acceptable outcome is
   paying for a probe that could have been skipped; discovery breaking because a JSON file is
   malformed is not acceptable. An unknown `version` is ignored rather than rejected.
3. **It is a cache, not configuration.** It lives under `XDG_CACHE_HOME`, holds only derived
   data, and deleting it costs one slow probe and nothing else. `refresh_capabilities` re-probes
   and overwrites.
4. **Writes are immediate and atomic.** The launcher daemon can be killed at any moment, so a
   probe that was paid for is recorded straight away, via a temporary file and a rename so a
   crash cannot leave half a cache behind.

A stale cache can cost an input the user must confirm by hand, or list one that no longer
exists. Both still face the ADR-0009 warning before any switch, so the failure mode is
friction rather than a dark screen.

## Considered Options

- **Leave the launcher without `capabilities`:** honest and fast, and useless — a picker
  listing only the input you are on cannot switch anything. This is what shipped, by accident.
- **Probe on every query:** correct and unusable, at 3.1s per keystroke.
- **Probe once per launcher session, in memory:** no cache file, and the daemon is long-lived
  so it would mostly work. Rejected because "mostly" hides the cost: the first query after
  every login, and after every daemon restart, stalls for 3.1s with no explanation.
- **Refresh in a background thread and re-render when it lands:** the best interactive
  behaviour, and it still needs somewhere to put the result between queries — i.e. this cache,
  plus concurrency. Worth doing later on top of this, not instead of it.
- **Reinstate the fallback table for the launcher only:** would fill the list instantly and
  reintroduce exactly the invented rows ADR-0014 removed, in the front-end where a stray Enter
  is most likely.
- **Cache keyed on identity (Chosen):** one slow probe per monitor, ever.

## Consequences

- The launcher's first query against an unfamiliar monitor takes about 3.5s. Subsequent
  queries are immediate. The snapshot already marks itself stale and the rows say
  "checking…", so the delay is at least explained.
- There is now a cache file where ADR-0010 deliberately removed one. The distinction worth
  keeping: ADR-0003's bus cache mapped something the kernel already publishes for free, so it
  was pure overhead; this caches something that costs 3.1s and is published nowhere else.
- `monswitch list` and the GTK picker could use the cache too, and currently re-probe. For
  `list` that is arguably correct — it is the command you run when you want the truth — but
  the picker should probably use it.
- The cache is keyed per unit, like `confirm` (ADR-0015). A model-keyed cache would let one
  probe serve every monitor of the same model, which is the same improvement already deferred
  for `confirm` and is now wanted in two places.
