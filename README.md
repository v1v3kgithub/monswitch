# monswitch

[![CI](https://github.com/v1v3kgithub/monswitch/actions/workflows/ci.yml/badge.svg)](https://github.com/v1v3kgithub/monswitch/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Switch your monitor's input from the keyboard, on Linux — without guessing which inputs exist.**

One monitor, two machines. Your laptop is on USB-C, the desktop is on DisplayPort, and the
only way to swap is the four buttons hidden under the monitor's bottom bezel. `monswitch` does
it over DDC/CI instead: a popup picker on a hotkey, or one command.

```console
$ monswitch list
HP Z38c  (HPN)
  bus 16 · connector DP-1 · key DEMOSERIAL

    0x0f  DisplayPort-1          reported   (unverified)
    0x11  HDMI-1                 reported   (unverified)
  * 0x13  0x13                   live       (current input, this machine)
    0x1f  0x1f                   guessed    (unverified)
```

Four inputs, and every one of them is something the monitor actually said. That is the point
of this project, and it is harder than it sounds.

---

## Why another one of these?

There are plenty of DDC/CI input switchers (see [Alternatives](#alternatives)). Nearly all of
them are a two-line wrapper around `ddcutil setvcp 60 <value>`, where `<value>` is a constant
you worked out once for your monitor and hardcoded.

That works until it doesn't, for two reasons.

**Monitors lie about their inputs — in both directions.** Here is the reference HP panel's own
capability report:

```
Feature: 60 (Input Source)
   Values:
      0f: DisplayPort-1
      11: HDMI-1
      1f: Unrecognized value
```

The machine writing this is attached over USB-C, and `getvcp 60` returns `sl=0x13` — a value
absent from that list. So the list **omits a real input**, and the `1f` it does offer is
something the firmware itself cannot name. Build your input list from `capabilities` and you
get a dead entry and a missing one.

**Switching away from yourself is a one-way door.** DDC/CI rides the display link. Tell the
monitor to show DisplayPort and it stops showing your machine *and* takes the channel you
would need to switch it back. Recovery is the physical buttons. Pick the wrong code — a
plausible-looking one that this panel doesn't implement — and you get a black screen and a
hunt for the bezel.

So `monswitch` tracks **where every input came from** and refuses to pretend otherwise.

---

## What makes it different

**It never offers an input your monitor didn't mention.** No built-in table of codes that
"usually work". Every row is traceable to the live read, the monitor's own capability report,
or something you told it. An early version padded the list from a cross-vendor table and
cheerfully offered a second DisplayPort that didn't exist — exactly the row whose selection
blanks your screen. [ADR-0014](docs/adr/0014-report-only-what-the-monitor-says.md) explains
why that was removed rather than merely marked.

**Every input carries its provenance, and confirmation is tiered on it:**

| Tier | Means | Switching to it |
| --- | --- | --- |
| `confirmed` | You verified it | Confirm once, because it leaves this machine |
| `live` | It is driving this panel right now | No confirmation; it's where you already are |
| `reported` | The firmware listed it, unverified | Stronger warning first |
| `guessed` | The firmware listed it and couldn't name it | Stronger warning first |

An unverified destination always warns — even with `confirm_switch = false`, even with `-y`.
That opt-out is for inputs you use daily, not an assertion that a guessed code is safe.

**It fails closed.** If the current input can't be read, `monswitch` won't switch at all: with
no idea where you are, it cannot tell whether a switch would cut your display link.

**Zero config, and it stays that way.** No setup step, no file to write, nothing read unless
you ask for it. Useful if you hot-desk and meet a different monitor every day.

**Fast enough for a hotkey.** Monitors are resolved from `/sys/class/drm` — the kernel already
publishes the connector-to-I²C mapping — in about 20ms, instead of the ~1.5s `ddcutil detect`
spends probing every bus.

---

## Install

Needs `ddcutil`, and GTK only if you want the popup.

```bash
sudo apt install ddcutil python3-gi gir1.2-gtk-3.0   # Debian/Ubuntu
sudo dnf install ddcutil python3-gobject gtk3        # Fedora
sudo pacman -S ddcutil python-gobject gtk3           # Arch
```

```bash
git clone https://github.com/v1v3kgithub/monswitch.git
cd monswitch
python3 -m venv --system-site-packages .venv   # --system-site-packages so GTK stays visible
.venv/bin/pip install -e .
.venv/bin/monswitch list
```

The core is standard-library-only Python 3.12+, so there is nothing else to install.

<details>
<summary><strong>Two things that catch everyone</strong></summary>

**1. `i2c-dev` must be loaded**, or `/dev/i2c-*` won't exist:

```bash
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
```

**2. You need non-root access to `/dev/i2c-*`.** Check before changing anything — on a modern
systemd desktop you may already have it, via a `uaccess` ACL on your local seat:

```bash
ddcutil detect     # if this works without sudo, you are done
```

If not, join the `i2c` group (the `ddcutil` package ships the udev rule):

```bash
sudo usermod -aG i2c "$USER"   # then log out and back in
```

Note this is seat-bound: it applies to your graphical session, not over SSH.

**Also:** some monitors ship with DDC/CI *disabled*. If `ddcutil detect` finds nothing, look
for it in the monitor's own menu, usually under *Management*, *Settings* or *Other*.

</details>

---

## Use

```bash
monswitch list                  # what's connected, which input is live
monswitch set dp1               # switch, with confirmation
monswitch set --dry-run dp1     # resolve and explain, change nothing
monswitch                       # popup picker (bind it to a hotkey)
monswitch confirm 0x0f --as "Desktop PC"   # record that a code is real
```

Inputs can be named however you think of them — `dp1`, `hdmi`, `usbc`, `0x13`, or a nickname
you set. Ambiguity is never guessed: `dp` on a monitor with two DisplayPorts asks which one.

With several monitors, name one — `monswitch set 'Dell U24' hdmi`, or `-m` in scripts. One
positional is an input; two are a monitor then an input. The parser never inspects the values
to decide which is which, so it cannot target the wrong panel.

### From Ulauncher

In Ulauncher's preferences, **Extensions → Add extension**, paste this repository's URL.
Ulauncher clones it and finds `manifest.json` at the root.

Then type `mon`. Same filtering and the same two-step confirmation as the picker, because both
drive the same model. Details in [`docs/ulauncher.md`](docs/ulauncher.md).

### The picker

`monswitch` with no arguments opens a Spotlight-style popup: type to filter, arrows to move,
Enter to pick, Escape to leave. The active input is marked, unverified ones are badged, and
anything needing confirmation turns the row into the warning and waits for a second Enter.
Escape backs out of that without closing the window.

### Optional config

Nothing is read unless you ask. `--config PATH`, or `--use-default-config` for
`~/.config/monswitch/config.toml`:

```toml
confirm_switch = true

[monitors."DEMOSERIAL"]
name = "Desk Ultrawide"

[monitors."DEMOSERIAL".inputs]
"0x13" = "This Laptop"
"0x0f" = "Desktop PC"
```

Naming an input under `[inputs]` marks it **confirmed** — you are asserting the code drives a
real port — which clears the unverified warning for it. The key is whatever `monswitch list`
prints as `key`.

`monswitch confirm` writes those entries for you:

```console
$ monswitch confirm 0x0f --as "Desktop PC"
HP Z38c: 0x0f (Desktop PC) confirmed

[monitors."DEMOSERIAL".inputs]
"0x0f" = "Desktop PC"
```

It prints the snippet by default; `--write` saves it and keeps the previous file as `.bak`.
It accepts codes your monitor never reported, which is the point — if the firmware omits an
input, confirming it by hand is the only way in.

Why "assert" rather than "detect": the obvious design was a `learn` command that watches
`getvcp 60` while you cycle inputs on the monitor's buttons. That cannot work. The moment the
monitor leaves your input it drops the link, and DDC/CI rides that link, so the tool goes
blind exactly when it would need to be watching —
[ADR-0015](docs/adr/0015-confirm-replaces-learn.md) has the measurements.

---

## How it works

Monitors implement VESA **MCCS** over **DDC/CI**, a channel on the I²C lines of the display
cable — DP AUX for DisplayPort, and for USB-C in DP alt mode. Feature code **`0x60`** is Input
Source: read it for the current input, write it to switch. `ddcutil` speaks the protocol;
`monswitch` decides what is safe to ask it.

Worth knowing if you're poking at this yourself:

- `ddcutil` prints `Invalid value (sl=0x13)` for vendor-specific codes. That is not an error —
  `0x13` is the real current input and `ddcutil` just has no name for it. Parse the `sl=` value.
- `--display N` indices are not stable across reboots or replugs. Resolve by EDID or bus.
- `--nodetect` does not exist (in 1.4.1 at least); an explicit `--bus` is what skips the probe.
- Laptop internal panels (`eDP-*`) have no DDC/CI input switching at all.

The full set of findings, and every design decision with its rejected alternatives, is in
[`docs/adr/`](docs/adr/README.md).

---

## Alternatives

Genuinely useful projects in the same space — if one fits you better, use it:

| Project | Shape |
| --- | --- |
| [ddcutil](https://www.ddcutil.com/) | The actual DDC/CI implementation. Everything here, including `monswitch`, is a wrapper around it |
| [nemofq/monitor-input-switch](https://github.com/nemofq/monitor-input-switch) | GNOME Shell extension, switches between fixed inputs |
| [interrupt21h/ddc-input-switch](https://github.com/interrupt21h/ddc-input-switch) | Python/Tk GUI + CLI, toggles between two inputs |
| [3urobeat/display-input-switcher](https://github.com/3urobeat/display-input-switcher) | Script, Linux and Windows |
| [qddcswitch](https://codeberg.org/Okxa/qddcswitch) | Qt GUI, grid of screens and inputs |
| [ruzaq/ddc_switch](https://github.com/ruzaq/ddc_switch) | Bash toggle with a zenity picker |
| [pkielblock/monitor-switch](https://github.com/pkielblock/monitor-switch) | Self-hosted web UI |
| [monitorcontrol](https://pypi.org/project/monitorcontrol/) | Python library for MCCS generally, not input-focused |

Most are built for a known monitor and a known code, which is the right call for a fixed desk.
`monswitch` is aimed at the case where you don't know the monitor yet, and where being wrong
about an input code costs you your screen.

---

## Status

Everything on the roadmap below is built. Read-only operations are safe to run freely.

- [x] `monswitch list`
- [x] `monswitch set` with tiered confirmation
- [x] GTK popup picker
- [x] [Ulauncher](https://ulauncher.io/) extension — see [`docs/ulauncher.md`](docs/ulauncher.md)
- [x] `monswitch confirm` — record that an input code is real, clearing its warning

486 tests, none of which need a monitor: the parsers are pure functions over captured
fixtures, and the write path is tested through a fake runner. No test has ever issued a
`setvcp`, and CI asserts it cannot.

Tested against an HP Z38c over USB-C on Intel `i915`, Ubuntu, `ddcutil` 1.4.1. **Reports from
other hardware are the most useful thing you can contribute** — especially the output of
`ddcutil capabilities` and `getvcp 60` from a panel whose input codes are unusual.

## License

MIT
