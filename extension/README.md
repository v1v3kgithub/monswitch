# monswitch Ulauncher extension

Switch monitor inputs from [Ulauncher](https://ulauncher.io/) without a terminal.

## Install

Ulauncher runs extensions under the **system** interpreter, so it cannot see a virtualenv
(ADR-0012). The extension therefore puts `monswitch` on `sys.path` itself, and all it needs is
the path to a checkout.

```bash
# 1. symlink this directory into Ulauncher's extension folder
mkdir -p ~/.local/share/ulauncher/extensions
ln -s "$PWD" ~/.local/share/ulauncher/extensions/monswitch

# 2. restart Ulauncher
pkill ulauncher; setsid ulauncher --no-window-shadow >/dev/null 2>&1 &
```

Because the extension directory sits inside the checkout, `../src` is found automatically. If
you put it elsewhere, set the **monswitch source path** preference to the checkout's `src/`
directory, or export `MONSWITCH_SRC`.

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
- `ddcutil capabilities` is never called here; it costs ~3.1s, which has no place on a path
  you are typing into. Run `monswitch list` for the fuller picture.
- Needs non-root access to `/dev/i2c-*`, since a launcher has no terminal to ask for a sudo
  password on. See the main README.
