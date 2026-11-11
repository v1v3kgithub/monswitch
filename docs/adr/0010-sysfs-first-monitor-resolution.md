# sysfs-First Monitor Resolution

> **Status:** Accepted · **Date:** 2026-11-09
> **Supersedes in part:** [ADR-0003](0003-optimistic-bus-caching.md)

> ADR-0003's bus cache is demoted to a fallback: it applies only to the `ddcutil detect` path.
>
> Decision 5 below ("`capabilities` is never on the interactive path") is superseded by
> [ADR-0016](0016-cached-capabilities-on-the-launcher-path.md): once ADR-0014 removed the
> fallback code table, `capabilities` became the only source of non-live inputs, so skipping
> it left the launcher with a one-row list. It is now consulted through a cache.

ADR-0003 chose to persist monitor-serial-to-bus mappings in `~/.cache/monswitch/bus_cache.json`,
addressing the cached bus directly and falling back to a full `ddcutil detect` when
"communication fails or returns an unexpected EDID serial". That left the central mechanism
unspecified: `ddcutil --bus N getvcp 60` returns a VCP value, not an EDID, so there was no
stated way to cheaply confirm that the cached bus still belongs to the expected monitor. Any
obvious implementation would have had to run something detect-shaped to validate, defeating the
cache.

Live probing on the reference machine (2026-11-09, i915, `ddcutil` 1.4.1) shows the cache is
largely unnecessary, because the kernel already publishes the mapping for free.

### Measured ground truth

| Operation                                                   | Cost     |
| ----------------------------------------------------------- | -------- |
| Walk `/sys/class/drm/card*/card*-*/` reading `status`+`edid` | 0.02 s   |
| `ddcutil --bus 16 getvcp 60 --terse`                         | 0.35 s   |
| `ddcutil detect --terse`                                     | 1.5 s    |
| `ddcutil --bus 16 capabilities`                              | 3.1 s    |

Each DRM connector exposes a `ddc` symlink to its I²C adapter and its raw EDID, with no I²C
traffic and no `ddcutil` invocation:

```
/sys/class/drm/card1/card1-DP-1/ddc    -> i2c-16
/sys/class/drm/card1/card1-DP-1/status -> connected
/sys/class/drm/card1/card1-DP-1/edid   -> 256 bytes
```

Parsing that EDID yields everything needed to identify the monitor: manufacturer `HPN`, model
name `HP Z38c`, and serial `DEMOSERIAL`.

Two details matter for the parser. The EDID's 4-byte serial-number field at offset 12 reads
`0x01010101` on this panel — a placeholder, not a serial. The real serial lives in an
18-byte descriptor block tagged `0xff`, which must be scanned for at offsets 54–126. And
`status` plus the connector name make filtering free: `eDP-1` is excluded by name per the
existing convention, and disconnected connectors are skipped without probing them.

### Decisions

1. **Resolve monitors from sysfs first.** Walk `/sys/class/drm/card*/card*-*/`, keep connectors
   whose `status` is `connected` and whose name is not `eDP-*`, read the bus from the `ddc`
   symlink and the identity from `edid`. This is the normal path for `list`, the GTK picker and
   the Ulauncher extension.
2. **`ddcutil detect` becomes the fallback, not the default.** The `ddc` symlink is exposed by
   the kernel's `drm_connector` DDC plumbing, which `i915` and `amdgpu` populate but the NVIDIA
   proprietary driver does not. When no usable connector has a `ddc` link — or sysfs yields no
   candidates at all — fall back to `ddcutil detect`, whose `--terse` output already carries
   the bus, the DRM connector and the monitor identity on separate lines and is the correct
   parse target. ADR-0003's cache file applies to this fallback path only.
3. **Self-healing is structural, so there is no cache to invalidate.** sysfs is read fresh each
   run at 0.02 s, so a replug or reboot is reflected immediately. This removes ADR-0003's
   unspecified validation step rather than specifying it: there is no stale mapping to detect,
   because nothing is persisted on the primary path. `--refresh` is retained only to bypass the
   fallback cache.
4. **Identity is the EDID triple, and `list` must survive a blank serial.** Monitors are keyed
   on (manufacturer, model, serial) per `docs/CONTEXT.md`, with the serial read from the `0xff`
   descriptor and never from the offset-12 field. The DRM connector name is carried alongside
   as a run-local handle for display and logging; it is not an identity, since connector names
   are stable per port but say nothing about which panel is plugged in.
5. **`capabilities` is never on the interactive path.** At 3.1 s it is slower than `detect`.
   Discovery per ADR-0001 may consult it, but only off the critical path or on explicit
   request — the first paint of a picker must not wait for it.
6. **Do not pass `--nodetect`.** The flag does not exist in `ddcutil` 1.4.1; it fails with
   `ddcutil option parsing failed: Unknown option --nodetect`. An explicit `--bus N` is what
   actually avoids the full probe. (`README.md` and `CLAUDE.md` previously recommended it; both
   are corrected.)

### Considered Options

- **Optimistic bus cache as the primary path (ADR-0003 as written):** Rejected on the grounds
  that it could not state a cheap validation step, and does not need to — the data it caches is
  already free from the kernel. Keeping it primary would mean persisting, invalidating and
  `--refresh`-ing a file to avoid a 0.02 s read.
- **`ddcutil detect` on every invocation:** Honest and driver-agnostic, but 1.5 s on this
  machine, which is a visible freeze in a launcher. Retained as the fallback only.
- **Parse `ddcutil detect` output but cache the bus (ADR-0003's intent, with validation by
  re-running detect):** Costs a detect to confirm the cache, so the cache buys nothing.
- **sysfs-first with a detect fallback (Chosen):** Zero-cost on the common path, always fresh,
  and degrades to the portable mechanism on drivers that do not expose `ddc`.

### Consequences

- The core gains an EDID parser (magic check, manufacturer-ID bit unpacking, `0xff`/`0xfc`
  descriptor scan). It is pure bytes-in/data-out and testable from a captured 256-byte fixture,
  consistent with the hardware-free testing convention.
- Two resolution backends must be kept behind one interface, and the fallback path needs
  testing on a machine or fixture without `ddc` symlinks — which the reference machine cannot
  provide, so the detect parser is tested from captured output.
- ADR-0003's performance figures (2.5–4.0 s for `detect`, ~100 ms for a bus-direct call) are
  corrected to the measured 1.5 s and 0.35 s. The ADR's conclusion — avoid repeated `detect` —
  survives; its magnitudes do not.
- A 0.35 s bus-direct `getvcp` is still too slow to block a launcher's first paint, which is
  what ADR-0012 addresses.
