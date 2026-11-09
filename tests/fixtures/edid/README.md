# EDID fixtures

Raw EDID blobs captured from the reference machine on 2026-11-09, read from
`/sys/class/drm/card1/card1-*/edid`. 256 bytes each: a 128-byte base block plus one
extension block.

| File | Panel | Serial source | Why it is here |
| --- | --- | --- | --- |
| `hp-z38c-dp1.bin` | HP Z38c on `DP-1` (USB-C, DP alt mode) | `0xff` descriptor → `DEMOSERIAL` | The offset-12 numeric field is `0x01010101`, a placeholder — the case that proves the descriptor must win |
| `lgd-edp1.bin` | LG internal laptop panel on `eDP-1` | none | No string descriptors at all and offset 12 is zero: a panel with no serial to key on, which ADR-0013's identity fallback chain exists for |

`lgd-edp1.bin` is from an internal panel, which the tool filters out of monitor discovery
(`eDP-*` has no DDC/CI input switching). It is kept purely as a parser fixture.
