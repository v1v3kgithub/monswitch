# Architecture Decision Records

Design decisions for monswitch. These are the authoritative design record — they
supersede any older sketch in `README.md` or the agent docs. Domain vocabulary lives in
[`../CONTEXT.md`](../CONTEXT.md).

Read 0009 through 0017 before writing code: they carry the safety-critical rules and they
amend several of the earlier eight.

| ADR | Decision | Status | Relations |
| --- | --- | --- | --- |
| [0001](0001-zero-config-dynamic-discovery.md) | Zero-config dynamic input discovery; `learn` is optional, not mandatory | **Superseded in part** | Refined by 0009; superseded in part by 0014 |
| [0002](0002-switch-confirmation-and-safety.md) | Confirm before switching away from the Host Input; `confirm_switch` opt-out | **Superseded in part** | Refined by 0009; superseded in part by 0017 (graphical only) |
| [0003](0003-optimistic-bus-caching.md) | Optimistic I²C bus cache with detect fallback | **Superseded in part** | Superseded in part by 0010 |
| [0004](0004-auto-targeting-flat-presentation.md) | Auto-target a single monitor; flat badge presentation | Accepted | Refined by 0013 |
| [0005](0005-direct-python-import-for-extension.md) | Ulauncher extension imports the core directly | Accepted | Refined by 0012 |
| [0006](0006-input-resolution-and-disambiguation.md) | Input lookup priority and terminal disambiguation | Accepted | Refined by 0011 |
| [0007](0007-config-schema.md) | Serial-keyed TOML config schema | **Superseded in part** | Superseded in part by 0013, 0014 |
| [0008](0008-gtk-popup-picker-ux.md) | GTK3 Spotlight-style popup picker | **Superseded in part** | Refined by 0009, 0011; superseded in part by 0017 |
| [0009](0009-input-provenance-and-trust-tiers.md) | Input provenance (`confirmed`/`live`/`reported`/`guessed`) and trust-tiered confirmation | **Superseded in part** | Refines 0001, 0002, 0008; superseded in part by 0015 |
| [0010](0010-sysfs-first-monitor-resolution.md) | Resolve monitors from sysfs; `ddcutil detect` is the fallback | **Superseded in part** | Supersedes in part 0003; superseded in part by 0016 |
| [0011](0011-graphical-disambiguation-and-failure-surfacing.md) | Explicit interaction strategy; typed errors rendered per frontend | Accepted | Refines 0006, 0008 |
| [0012](0012-packaging-and-module-resolution.md) | Extension-directory install with path wiring; no import-time side effects | Accepted | Refines 0005 |
| [0013](0013-identity-host-input-and-target-resolution.md) | Host Input derived not stored; identity fallback chain; `set` arity | Accepted | Supersedes in part 0007; refines 0004, 0010 |
| [0014](0014-report-only-what-the-monitor-says.md) | No speculative input rows; config is opt-in | Accepted | Supersedes in part 0001, 0007; refines 0009 |
| [0015](0015-confirm-replaces-learn.md) | `learn` withdrawn as unbuildable; `confirm` records the user's assertion | Accepted | Supersedes in part 0001, 0009 |
| [0016](0016-cached-capabilities-on-the-launcher-path.md) | `capabilities` on the launcher path, cached by monitor identity | Accepted | Supersedes in part 0010; refines 0012, 0014 |
| [0017](0017-single-enter-in-the-graphical-front-ends.md) | Single Enter in the picker and launcher; no confirmation step | Accepted | Supersedes in part 0002, 0008, 0009 (graphical only) |

## The safety spine

Five decisions carry the weight, because `setvcp 60` cannot be undone from software:

- **0002** — confirm before leaving the Host Input.
- **0009** — the destination's provenance tiers that confirmation; an unverified destination
  always prompts, even with `confirm_switch = false`. **Note:** ADR-0017 removed that prompt
  from the graphical front-ends at the user's request. It still holds in the CLI.
- **0013** — the Host Input is derived from a live read and never stored; no live read means no
  switch. Identity collisions discard confirmed maps rather than misapply them.
- **0012** — a snapshot-rendered marker is provisional, so stale state cannot make a switch
  look safe.
- **0014** — nothing speculative is ever offered as a destination, so the warning in 0009 is
  spent only on rows the monitor itself reported.

Separately and unconditionally: **an agent never issues `setvcp 60` without explicit,
in-the-moment human confirmation.** `confirm_switch` and `-y/--force` are user affordances and
are not agent permission. See the canonical agent doc, `../AGENTS.md`.

## Conventions

- One decision per file, named `NNNN-kebab-title.md`, numbered sequentially.
- Each file opens with an H1 title, then a metadata blockquote: `**Status:**` ·
  `**Date:**`, plus `**Supersedes/Superseded in part by/Refines/Refined by:**` where
  applicable.
- Body: the problem in prose, the decision(s), `## Considered Options` with what was rejected
  and why, and `## Consequences` for what the decision costs.
- A superseded ADR is never deleted or rewritten to agree with its successor. It keeps its
  original reasoning, with a blockquote saying what changed and why.
- Record the status change in **both** files and in the table above, or an agent reading one
  file in isolation will follow a stale decision — which has already happened once in this
  project's history.
