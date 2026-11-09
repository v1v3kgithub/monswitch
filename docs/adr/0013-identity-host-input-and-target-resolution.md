# Monitor Identity, Host Input Derivation, and CLI Target Resolution

> **Status:** Accepted · **Date:** 2026-11-09
> **Supersedes in part:** [ADR-0007](0007-config-schema.md) (drops `host_input`; replaces the
> serial-only config key) · **Refines:** [ADR-0004](0004-auto-targeting-flat-presentation.md),
> [ADR-0010](0010-sysfs-first-monitor-resolution.md)

Three loose ends from the earlier ADRs all turn on the same question — how a monitor and an
input are named and pinned down before an irreversible write. Resolved together because the
answers interlock: identity keys the confirmed code map, the code map feeds provenance, and
provenance gates the confirmation.

## 1. The Host Input is derived, never stored

ADR-0007's schema includes `host_input = "0x13"` per monitor. It is redundant, and dangerous
for being redundant.

DDC/CI rides the display link. If `getvcp 60` on a monitor's bus returns a value at all, the
monitor must currently be driving its picture from this machine — otherwise the link, and the
I²C channel with it, would be gone (this is the same property that makes switching away
one-way). So **a successful read of `0x60` is itself proof that the returned value is the Host
Input.** No stored value can be more authoritative than that, and a stored value can be wrong:
move the cable to another port and the config still names the old code, so ADR-0002's
"switching away from the Host Input" check compares against the wrong input and stays silent on
precisely the switch that cannot be undone.

Decisions:

1. **`host_input` is removed from the config schema.** The Host Input is the value of a
   successful live `getvcp 60` read on that monitor's bus, computed fresh, never persisted.
2. **No live read means no Host Input, and no switch.** If the read fails, the tool does not
   guess, fall back to a cached value, or assume the host is elsewhere — `set` refuses with a
   typed error (ADR-0011). Failing closed is correct here: the only thing a wrong guess buys is
   a missing warning on an unrecoverable action.
3. **A snapshot's Host Input marker is not a Host Input.** ADR-0012 renders the picker from a
   snapshot before the live read lands. Until it lands, the marker is provisional and every
   destination is treated as unverified per ADR-0009, so the confirmation cannot be skipped on
   the strength of stale state.

## 2. Identity is a fallback chain, and collisions never merge

`docs/CONTEXT.md` defines a Monitor by its EDID synopsis (manufacturer, model, serial);
ADR-0007 keys config on the serial alone. ADR-0010 established that the serial comes from the
EDID `0xff` descriptor block and that the offset-12 field is a `0x01010101` placeholder on the
reference panel. Some panels carry no `0xff` descriptor at all, leaving nothing to key on, and
two identical such panels are indistinguishable by any EDID-derived key.

The failure this creates is not cosmetic. Config holds the *confirmed* code map (ADR-0009). If
two panels share a key, one panel's confirmed codes are applied to the other, so codes that
were never verified against that panel are presented as verified — and a `confirmed`
destination skips the warning that a `guessed` one would raise.

Decisions:

1. **Resolution order for the config key:** the EDID serial when present and non-placeholder;
   otherwise `(manufacturer, model, DRM connector name)`. The connector name is read for free
   from sysfs per ADR-0010 and is stable per physical port.
2. **Placeholder serials do not count as serials.** An all-equal-bytes offset-12 value such as
   `0x01010101`, an empty `0xff` descriptor, or a whitespace-only string are treated as absent,
   not as a key.
3. **On collision, fail safe rather than merge.** If two connected monitors resolve to the same
   key, both are kept as distinct monitors for `list` and `set`, and **all serial-keyed
   overrides are ignored for both** — names, aliases and confirmed maps alike. Their inputs
   therefore fall back to the `live`/`reported`/`guessed` tiers and keep their unverified
   badges. The condition surfaces as a typed warning (ADR-0011), since it is user-fixable by
   keying on the connector form instead.
4. **The connector name is a key component, not an identity.** It may appear inside a composite
   key, but a connector-only key is never synthesised on its own: it names a port, so a cable
   move would silently transfer one panel's confirmed map to another.

## 3. `set` target resolution is decided by arity

ADR-0004 shows `monswitch set dp1` (auto-target); GEMINI.md documents
`monswitch set <monitor> <input>`. Both are needed, and the gap was how to tell them apart —
in `monswitch set hp dp1`, `hp` could be a monitor or a mistyped input. Guessing risks
switching the wrong panel.

Decisions:

1. **Arity alone decides, with no heuristics.** One positional is an input, auto-targeted per
   ADR-0004. Two positionals are a monitor followed by an input. The parser never inspects the
   value to decide which role it plays, so `set hp dp1` is always monitor-then-input and never a
   guess that could land on the wrong hardware.
2. **`-m` / `--monitor` is always available and always unambiguous.** `monswitch -m <monitor>
   set <input>` works regardless of positional count, and is the form to use in scripts and
   when an input alias could be mistaken for a monitor name. Supplying both `-m` and two
   positionals is an error, not a precedence puzzle.
3. **Auto-targeting still refuses to guess between monitors.** With one positional and multiple
   external monitors connected, ADR-0004's behaviour stands: prompt in an interactive terminal,
   abort otherwise, per ADR-0006 and ADR-0011.
4. **Input resolution is unchanged.** ADR-0006's priority order (nicknames → built-in slugs →
   hex/decimal codes) and its disambiguation rules apply to the input positional exactly as
   written.

## Considered Options

Each sub-question's rejected alternatives, in brief:

- **Host Input:** keeping `host_input` as an optional override with the live read winning
  (rejected — two sources feeding one safety decision, and the fallback case is the risky one);
  as a last-known-good cache for instant first paint (rejected — reintroduces the stale-marker
  risk ADR-0012 flagged, to save 0.35 s); as user-declared and authoritative over the live read
  (rejected — no evidence the live read is ever wrong, and it inverts the only trustworthy
  source).
- **Identity:** requiring a serial and simply denying overrides to panels without one (rejected
  — workable and safe, but permanently strands those panels in the guessed tier with no path to
  confirmation); hashing the full EDID (rejected — identical serial-less panels often have
  byte-identical EDIDs, so it does not resolve the collision, and it makes the hand-edited TOML
  of ADR-0007 unreadable); keying on the DRM connector alone (rejected — identifies the port,
  so a cable move misapplies a confirmed map).
- **`set` arity:** flag-only with no positional monitor (rejected — taxes the multi-monitor case
  most); two positionals always, with `auto`/`-` as a placeholder (rejected — taxes the
  single-monitor case ADR-0004 optimised for); arity with no `-m` escape hatch (rejected — no
  unambiguous form for scripts, and no recourse if a nickname collides with an alias).

## Consequences

- ADR-0007's example schema loses `host_input`, and its key is documented as the fallback chain
  rather than a bare serial. The TOML remains hand-editable, which was that ADR's goal.
- The core needs a key-derivation function and a collision check across the resolved monitor
  set, both pure and testable from EDID fixtures. The collision path needs a synthetic
  two-identical-panels fixture, which the reference machine cannot produce.
- `set` refusing to act without a live read makes the DDC/CI read path load-bearing for safety,
  not just for display. A monitor that is detectable via sysfs but unreadable via `ddcutil` is
  listable but not switchable, and must say so clearly rather than appearing inert.
- Three documents describe the CLI surface (README.md, the canonical agent doc, and ADR-0004);
  the arity rule has to be stated once and referenced, or it will drift again.
