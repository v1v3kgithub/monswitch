# MonitorSwitcher Domain Context

Core domain language for MonitorSwitcher, a tool to inspect and switch external monitor video inputs over DDC/CI.

## Language

**Monitor**:
An external physical display device communicating over I2C/DDC/CI, identified persistently by its EDID synopsis (manufacturer, model, and serial number).
_Avoid_: Screen, Display (when referring to the physical unit)

**Input Source**:
A physical or logical video port on a **Monitor** selectable via VESA MCCS VCP feature code `0x60`.
_Avoid_: Channel, Port, Device

**Host Input**:
The specific **Input Source** currently receiving the video feed and DDC/CI link from the local computer running MonitorSwitcher.
_Avoid_: Active input (when referring specifically to the host's machine connection)

**VCP Code**:
The single-byte hexadecimal value (e.g., `0x13`, `0x0f`) representing an **Input Source** in MCCS VCP code `0x60`.
_Avoid_: Port number, input ID
