# GEMINI.md

**The agent brief for this project is [`docs/AGENTS.md`](docs/AGENTS.md). Read it first.**

It is vendor-neutral and canonical: project overview, the hard-won probing facts, the CLI
surface, conventions and the safety rules all live there. This file is a pointer so the brief
cannot drift between agents — it has drifted twice before.

Also read, before writing code:

- [`docs/adr/README.md`](docs/adr/README.md) — the 13 ADRs that are the authoritative design
  record, with their supersession graph. ADR-0009 through ADR-0013 carry the safety-critical
  rules and amend several of the earlier eight.
- [`docs/CONTEXT.md`](docs/CONTEXT.md) — domain vocabulary.

One rule is repeated here because it is absolute and must not depend on following a link:

> **Never issue `ddcutil setvcp 60` without explicit, in-the-moment user confirmation** — not
> to test, not in an automated run. Switching a monitor away from this machine's own input
> kills the display link *and* the DDC/CI channel with it; recovery needs the monitor's
> physical OSD buttons. `confirm_switch = false` in the user's config and the CLI's
> `-y`/`--yes`/`--force` flags are user affordances, never agent permission. Probe read-only
> with `getvcp`, `detect`, `capabilities` and sysfs instead.
