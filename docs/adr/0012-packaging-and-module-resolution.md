# Packaging and Module Resolution for the Ulauncher Extension

> **Status:** Accepted · **Date:** 2026-11-09
> **Refines:** [ADR-0005](0005-direct-python-import-for-extension.md)

> ADR-0005 chose direct import but did not say how the module becomes importable.
>
> Two parts are superseded: ADR-0016 puts `capabilities` on the launcher path through a cache,
> and ADR-0018 makes the provisional stale-snapshot marker inert — the verdict is still
> computed, but no graphical front-end acts on it now that arming is gone.

ADR-0005 decided the Ulauncher extension imports the core directly
(`from monswitch.core import ...`) rather than shelling out to the CLI, on the grounds that the
core is standard-library-only and that subprocess spawning costs 50–80 ms per keystroke. It did
not specify how `monswitch` lands on the `sys.path` of the Ulauncher daemon's interpreter —
which is the only thing that makes the decision implementable.

Probing the reference machine (2026-11-09) shows the obvious route is blocked:

- `/usr/bin/ulauncher` is a `#!/usr/bin/python3` script, so the extension runs under the
  **system** interpreter, Python 3.12.3. A virtualenv is therefore invisible to it.
- `site.ENABLE_USER_SITE` is true and user-site resolves to
  `~/.local/lib/python3.12/site-packages`, but that directory **does not exist**, so it is
  absent from `sys.path` today. It appears only once something installs into it.
- `/usr/lib/python3.12/EXTERNALLY-MANAGED` is present (PEP 668). So `pip install --user .`
  refuses on this machine without `--break-system-packages`, which is not something a tool
  should ask a user to type to install a monitor switcher.
- `PYTHONPATH` is empty, and the daemon inherits the session environment, so setting it would
  mean editing the user's shell or systemd user environment — fragile and invisible.
- GTK is only available as system `python3-gi` at `/usr/lib/python3/dist-packages/gi`. A plain
  venv cannot see it without `--system-site-packages`, which rules out a venv for the picker
  as well as for the extension.

So the two interpreters that must import the core are both the system interpreter, and the
sanctioned mechanism for putting code on its path is unavailable to an unprivileged installer.

### Decisions

1. **The extension directory is the installation unit, and it carries the path wiring.** The
   Ulauncher extension is installed the normal way, into
   `~/.local/share/ulauncher/extensions/<ext-id>/`, and its `main.py` prepends the core's
   location to `sys.path` before importing it. This needs no `pip`, no venv, no `PYTHONPATH`
   and no root, and works regardless of the PEP 668 marker.
2. **The core is referenced, not vendored.** The extension resolves the core by reading a path
   recorded at install time, falling back to a `monswitch` already importable from `sys.path`
   (for users who did install it conventionally). The core is not copied into the extension
   directory: a vendored copy would drift from the CLI's copy, and ADR-0005's whole point is one
   shared core rather than two implementations.
3. **A `pyproject.toml` exists and conventional installs are supported, but are not the
   documented path.** `pipx install .` and `pip install --user .` both work where the
   environment permits them, and if `monswitch` is importable the extension uses it directly.
   The documented default for this distribution remains a checkout plus the extension's own
   path wiring, because that is what works on an externally-managed Ubuntu without special
   flags.
4. **The GTK picker runs on the system interpreter too.** `monswitch` with no arguments imports
   `gi` from system `dist-packages`, so the entry point must not be placed in a virtualenv that
   lacks `--system-site-packages`. `python3-gi` stays an apt-installed prerequisite, as
   `README.md` already states, and is never a `pip` dependency.
5. **Import of the core must stay free of side effects.** No `ddcutil` invocation, no sysfs
   walk, no config read at import time. The Ulauncher daemon imports extensions at startup, and
   ADR-0010 measures `getvcp` at 0.35 s and `detect` at 1.5 s — any of that at import would
   stall the daemon's startup for every user of the launcher, whether or not they ever type the
   keyword.
6. **The first keystroke never blocks on hardware.** ADR-0005 justified direct import by
   latency, but the real cost is the DDC/CI round trip, not process spawn: 0.35 s bus-direct,
   1.5 s for the fallback `detect`, 3.1 s for `capabilities`. The extension therefore renders
   from an in-process snapshot of the last discovery result, refreshing it in a worker thread
   and updating the result list when it lands. A cold cache shows the monitor and its inputs
   from sysfs — 0.02 s per ADR-0010, cheap enough to be synchronous — with the live input marker
   filled in when the `getvcp` read returns. `capabilities` is never fetched on this path
   (ADR-0010).
7. **Provenance survives the snapshot.** A stale snapshot's live-input marker is itself stale,
   and the Host Input marker is what the confirmation logic of ADR-0002 and ADR-0009 depends
   on. An input whose `live` status comes from a snapshot rather than a completed read is
   treated as unverified until the refresh lands, so a stale cache can never cause a switch
   away from the Host Input to be presented as safe.

### Considered Options

- **`pip install --user` and rely on user-site (the implicit assumption in ADR-0005):** The
  cleanest story on paper. Rejected: PEP 668 blocks it on the reference distribution, and the
  user-site directory does not yet exist, so it is not currently on the daemon's path either.
- **Virtualenv for the project:** Standard Python practice, and rejected twice over — the
  Ulauncher daemon runs the system interpreter and cannot see a venv at all, and the GTK picker
  needs system `gi`, which a venv excludes unless built with `--system-site-packages`.
- **Vendor a copy of the core into the extension directory:** Self-contained and needs no path
  wiring. Rejected as a silent fork of the core, which is exactly the duplication the
  unified-core constraint exists to prevent.
- **Set `PYTHONPATH` in the user's environment:** Would work for both interpreters, but
  requires editing shell profiles or systemd user environment, breaks for non-shell session
  startup, and is invisible when it fails.
- **Revert to subprocess-calling the CLI:** Sidesteps `sys.path` entirely, and is more tempting
  than ADR-0005 allowed, since the 50–80 ms spawn it rejected is small next to a 350 ms DDC/CI
  read. Still rejected: it depends on `monswitch` being on the GUI session's `$PATH`, which has
  the same installation problem one layer down, and loses the structured domain objects —
  including the provenance field ADR-0009 requires — unless a JSON protocol is defined and
  maintained.
- **Extension-directory install with path wiring and a referenced core (Chosen):** Works
  unprivileged on an externally-managed distribution, keeps one core, and keeps both
  interpreters on system Python where `gi` is available.

### Consequences

- The extension gains a small bootstrap in `main.py` that must run before the core import, and
  an install step that records the core's location. Both need to fail loudly per ADR-0011 if the
  core cannot be found, rather than raising a bare `ImportError` into the daemon's log.
- The core must be lazily initialised, which is a real constraint on its structure: module-level
  discovery is forbidden.
- Snapshot-backed rendering means the Ulauncher frontend holds mutable state, making it slightly
  thicker than a pure wrapper. The snapshot and the refresh policy belong in the core so the GTK
  picker gets the same behaviour; only the result-list update is extension-specific.
- Keeping `pyproject.toml` honest without it being the documented path risks it rotting. It
  should be exercised in CI even though the recommended install does not use it.
