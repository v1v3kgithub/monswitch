# Direct Python Module Import for Ulauncher Extension

> Refined by [ADR-0012](0012-packaging-and-module-resolution.md), which specifies how the core
> reaches the daemon's `sys.path` (PEP 668 blocks `pip install --user` on the reference
> machine) and replaces the per-keystroke latency argument with measured DDC/CI costs.

Ulauncher extensions run in a separate daemon process using the system's Python interpreter. We considered whether the extension should shell out to `monswitch` CLI commands via subprocess or import the core Python module directly.

We decided on direct Python import (`from monswitch.core import ...`):
- Because `monswitch` relies solely on standard library modules (Python 3.12 `tomllib`, `argparse`, `dataclasses`, `subprocess`), it introduces zero third-party dependency conflicts.
- Direct import eliminates subprocess spawning overhead on every keystroke, keeping launcher query latency under 100ms.
- It enables rich domain objects (`Monitor`, `InputSource`) and in-process cache sharing across the extension lifecycle.

### Considered Options
- **Subprocess CLI with JSON output:** Isolates environments but adds 50-80ms process spawn overhead per search query and depends on GUI session `$PATH`.
- **Direct Python import (Chosen):** Fastest responsiveness, native structured objects, and zero external runtime dependencies.
