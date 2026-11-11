# monswitch Ulauncher extension

Switch monitor inputs from [Ulauncher](https://ulauncher.io/) without a terminal.

## Install

Ulauncher runs extensions under the **system** interpreter, so it cannot see a virtualenv
(ADR-0012). The extension therefore puts `monswitch` on `sys.path` itself, and all it needs is
the path to a checkout.

### From Ulauncher (normal install)

Preferences → **Extensions** → **Add extension**, and paste the repository URL. Ulauncher
clones it, reads `manifest.json` from the root, and the extension finds the core in the `src/`
directory of that same clone — nothing to configure.

### From a local checkout (development)

```bash
mkdir -p ~/.local/share/ulauncher/extensions
ln -s /path/to/monswitch ~/.local/share/ulauncher/extensions/monswitch
pkill ulauncher; setsid ulauncher >/dev/null 2>&1 &
```

Symlink the repository root, not a subdirectory. If you place it somewhere the `src/`
directory is not alongside, set the **monswitch source path** preference, or export
`MONSWITCH_SRC`.

## Use

Type the keyword (default `mon`), then filter:

```
mon            → every input on every connected monitor
mon hdmi       → just HDMI
mon dell hdmi  → HDMI on the Dell, if you have more than one monitor
```

The input your machine is currently driving is marked **current input**. Inputs the monitor
reported but that have not been verified are marked **unverified**.

**Enter twice to switch.** The first Enter turns the row into the specific warning — switching
away means the monitor stops showing this machine and cannot be switched back from here — and
the second carries it out. An input you are already on switches immediately, because nothing
changes.

## Preferences

| Preference | Default | Meaning |
| --- | --- | --- |
| Monitor input | `mon` | The keyword |
| monswitch source path | *(empty)* | Checkout's `src/`, if not auto-found |
| Config file | *(empty)* | Empty means show only what the monitor reports |

## Notes

- Results render from a cached snapshot so typing never waits on a DDC/CI read (~0.35s per
  monitor). A row served from data that could not be refreshed asks for confirmation even if
  you have turned confirmation off — stale data must not make a switch look safe.
- `ddcutil capabilities` **is** consulted, through a cache (ADR-0016). It costs ~3.1s the
  first time a given monitor is seen and nothing afterwards, because it describes firmware
  that does not change. Without it the list would hold only the input you are already on,
  since ADR-0014 removed the built-in table of guessed codes. Delete
  `~/.cache/monswitch/capabilities.json` to re-probe.
- Needs non-root access to `/dev/i2c-*`, since a launcher has no terminal to ask for a sudo
  password on. See the main README.
