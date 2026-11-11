# MonitorSwitcher

Switch the active **input source** of an external monitor from Linux, programmatically, over
DDC/CI — including when the machine is attached via USB-C (DP alt mode).

Target UX:

1. `monswitch` — invoke it, get a popup list of inputs on the detected monitor(s), pick one.
2. A [Ulauncher](https://ulauncher.io/) extension doing the same thing without a terminal.

> Name note: the directory is `MonitorSwitcher`; the binary/entry point is `monswitch`.
> Alternative names considered: `ddc-switch`, `inputctl`. `monswitch` won on being short
> enough to type into Ulauncher.

---

## How it works

Monitors implement the VESA **MCCS** control set over the **DDC/CI** channel, which rides on
the I²C lines of the display cable (DP AUX for DisplayPort and USB-C DP-alt-mode). MCCS
feature code **`0x60` = Input Source**. Reading it tells you the current input; writing it
switches the monitor.

`ddcutil` is the userspace tool that speaks DDC/CI, via `/dev/i2c-*` adapters exposed by the
GPU driver (`i915` on Intel, `amdgpu`, `nvidia`).

---

## Prerequisites

### 1. A monitor that actually supports DDC/CI

Most HP / Dell / LG panels do. Some require **DDC/CI = On** in the monitor's own OSD menu
(often under *Management* or *Settings → DDC/CI*). If `ddcutil detect` finds nothing, check
there first.

### 2. Packages

Debian / Ubuntu:

```bash
sudo apt install ddcutil
```

For the GTK picker UI:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0
```

Fedora: `sudo dnf install ddcutil python3-gobject gtk3`
Arch: `sudo pacman -S ddcutil python-gobject gtk3`

### 3. The `i2c-dev` kernel module

Needed so `/dev/i2c-*` exists. Load now and on every boot:

```bash
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
```

### 4. Non-root access to the I²C buses

Non-root access is **required**, not optional, for the Ulauncher path: a Ulauncher extension
runs as your user with no TTY, so it can never prompt for a sudo password.

**Check whether you already have it before changing anything.** On a modern systemd desktop,
logind's `uaccess` tag often grants your seat's user a POSIX ACL on the DRM-connector buses
automatically — this is the case on the reference machine:

```bash
getfacl /dev/i2c-16 | grep "^user:"   # look for: user:<you>:rw-
ddcutil detect                         # if this works without sudo, you are done
```

If that works, skip the rest of this step.

Otherwise, the `ddcutil` package ships a udev rule granting the `i2c` group access:

```bash
sudo usermod -aG i2c "$USER"
# log out and back in (or: newgrp i2c) for the new group to take effect
```

Verify:

```bash
id -nG | tr ' ' '\n' | grep -x i2c   # should print: i2c
ddcutil detect                        # no sudo
```

Note: being absent from the `i2c` group is *not* by itself a problem — the ACL route above
can already grant access. Test with `ddcutil detect`, not with `id`.

<details>
<summary>Fallback if the <code>i2c</code> group route does not work</summary>

A narrow sudoers rule for just this one binary:

```bash
sudo tee /etc/sudoers.d/ddcutil <<'SUDOERS'
<your-username> ALL=(root) NOPASSWD: /usr/bin/ddcutil
SUDOERS
sudo chmod 0440 /etc/sudoers.d/ddcutil
```

This lets your user run `ddcutil` as root with no password. `ddcutil` can write arbitrary
monitor settings, so the grant is scoped to that binary only — do not widen it to
`ALL=(root) NOPASSWD: ALL`.

</details>

### 5. Verify end to end

```bash
ddcutil detect
ddcutil --display 1 capabilities | grep -A10 'Feature: 60'
ddcutil --display 1 getvcp 60
```

---

## Known quirks

### Vendor codes are under-reported

`capabilities` lists only the input values the firmware chooses to admit. Real example, an HP
monitor attached over USB-C:

```
Feature: 60 (Input Source)
   Values:
      0f: DisplayPort-1
      11: HDMI-1
      1f: Unrecognized value
```

…while `getvcp 60` on that same monitor, on its USB-C input, returns `sl=0x13` — a value
missing from the list. So the capability list is a hint, not the truth.

Note that `1f` in the list above is reported as "Unrecognized value" — so the list is not
merely incomplete, it also contains entries that are not usable inputs. It misreports in both
directions.

**Reliable way to map codes:** read the value while cycling inputs by hand on the OSD:

```bash
watch -n1 'ddcutil --display 1 getvcp 60'
```

Common values (standard MCCS, plus vendor extensions in the wild):

| Value  | Input                              |
| ------ | ---------------------------------- |
| `0x0f` | DisplayPort-1                      |
| `0x10` | DisplayPort-2                      |
| `0x11` | HDMI-1                             |
| `0x12` | HDMI-2                             |
| `0x13` | USB-C (HP vendor-specific)         |
| `0x1b` | USB-C / Thunderbolt (some vendors) |
| `0x1f` | vendor-specific / second USB-C     |

MonitorSwitcher therefore does not trust `capabilities`, and does not make you calibrate
before first use either. It builds the input list by merging the live `getvcp 60` value, the
`capabilities` report, and the fallback table above — tracking, per input, whether the entry
was **observed live**, **reported** by the firmware, or **guessed** from the table, and
treating a guessed entry with more caution before switching to it. `monswitch learn` is an
*optional* OSD walk-through that upgrades guesses to confirmed codes and records them per
monitor (keyed per `docs/adr/0013-identity-host-input-and-target-resolution.md`). See
`docs/adr/0001-zero-config-dynamic-discovery.md` and
`docs/adr/0009-input-provenance-and-trust-tiers.md`.

### Switching away from your own input is one-way

If your machine is the USB-C source and you switch the monitor to DisplayPort, the monitor
stops displaying your machine — and the DDC/CI channel dies with the link, so you cannot send
the code to switch back. Recovery is the monitor's physical OSD buttons, or sending the code
from whatever machine is on the input you switched to.

The UI must therefore mark the currently-active input and confirm before switching away from
it.

### Other notes

- `--display N` indices from `ddcutil detect` are not stable across reboots or replugs.
  Address monitors by `--mfg`/`--model`/`--sn` or by `--bus`, resolved fresh at each run.
- `ddcutil detect` is slow (it probes every I²C bus) — 1.5 s on the reference machine, against
  0.35 s for a bus-direct `ddcutil --bus 16 getvcp 60` and 3.1 s for `capabilities`. Use an
  explicit `--bus` on the write path. Note there is **no** `--nodetect` flag in `ddcutil` 1.4.1;
  it fails with `Unknown option --nodetect`.
- Better still, skip `ddcutil detect` on the read path entirely: the kernel already publishes
  the mapping. `/sys/class/drm/card*/card*-*/ddc` symlinks each connected output to its I²C
  adapter, and the adjacent `edid` file identifies the panel — the whole walk costs 0.02 s with
  no I²C traffic. `ddcutil detect` is then only needed on drivers that do not expose `ddc`
  (notably the NVIDIA proprietary driver). See `docs/adr/0010-sysfs-first-monitor-resolution.md`.
- DDC/CI writes are fire-and-forget; the monitor may take a second or two to re-sync.
- Laptop internal panels (`eDP-*`) have no DDC/CI input switching. Ignore them.

---

## Reference machine

Developed against:

- Ubuntu, kernel 7.0.0, Intel iGPU (`i915`)
- Internal panel on `eDP-1`, HP monitor on `DP-1` (= the USB-C port, DP alt mode)
- `ddcutil` 1.4.1, Python 3.12, Ulauncher 5.16.2 (extension API v2)

---

## Status

**Current:** `monswitch list` works. Everything shipped so far is read-only — no code in this
repo can change a monitor's input yet, by design. The design is recorded as 13 ADRs, indexed in
[`docs/adr/README.md`](docs/adr/README.md), with the domain vocabulary in
[`docs/CONTEXT.md`](docs/CONTEXT.md) and the agent brief in
[`docs/AGENTS.md`](docs/AGENTS.md).

### Try it

```bash
python3 -m venv --system-site-packages .venv   # --system-site-packages keeps system `gi` visible
.venv/bin/pip install -e .
.venv/bin/monswitch list
```

Or without installing anything: `PYTHONPATH=src python3 -m monswitch.cli list`.

### Roadmap

- [x] `monswitch list` — detect monitors, print inputs, mark the active one
- [ ] `monswitch set <input>` / `set <monitor> <input>` / `-m <monitor> set <input>` — the
      plumbing command. Arity decides: one positional is an input on the auto-targeted
      monitor, two are monitor then input
- [x] GTK popup picker (`monswitch` with no args)
- [ ] Ulauncher extension wrapping the same core
- [ ] `monswitch learn` — optional OSD walk-through that confirms guessed codes
- [ ] Config at `~/.config/monswitch/config.toml` — overrides: friendly names, aliases,
      confirmed code maps, `confirm_switch`

---

## License

MIT
