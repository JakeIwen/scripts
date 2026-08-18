# Van Dashboard USB port controls

[Pi documentation index](../../README.md)

The USB Devices sheet passively polls only `usb_watch.py --json` (plus Linux
sysfs data used by that monitor). It supplies readable `lsusb` names,
filesystem labels, and stable Linux topology locations for each present
device. Passive page load, the two-second open-sheet refresh, the thirty-second
dashboard refresh, tab visibility changes, and any number of browser clients
never execute `uhubctl`.

Hub controls are deliberately separate. **Load controls** sends an explicit
POST request that runs one serialized `sudo -n /usr/sbin/uhubctl` status query.
The returned topology snapshot expires after five minutes. It is never renewed
automatically. All dashboard `uhubctl` status, action, and USB 2 recovery
processes share one backend lock, so browser clients cannot overlap them.

Ports marked **POWER + DATA** are controlled with a fixed `uhubctl` command.
`uhubctl` handles paired USB 2 and USB 3 logical hubs automatically. Hardware
can still disconnect data without actually removing VBUS even when it advertises
power switching. The displayed **USB ENABLED** state describes the Linux hub
port state. It cannot report the position of an external hub's physical power
switch and must not be read as confirmation that VBUS is physically present.

On Raspberry Pi 4, matching smart-hub routes below the internal USB 2 hub and
the USB 3 root are presented as one physical port. Chained controller uplinks
are omitted and their remaining ports are flattened into physical socket order.
For example, the replacement ten-port Realtek hub is internally six logical
hub controllers (three USB 2 and three USB 3), but the main view shows ten
controls. Pi root and internal-hub ports remain available in a collapsed
**Advanced / internal ports** section.

Ports marked **DATA ONLY** belong to hubs that do not advertise independent
power switching. Those controls write only `0` or `1` to the already-discovered
Linux `...-portN/disable` file through `sudo tee`. They disconnect and reconnect
the logical USB path; they do not claim to remove electrical power. A USB 3 hub
can expose separate USB 2 and USB 3 logical DATA ONLY controls.

The browser sends only a server-issued port key and one of `on`, `off`, or
`cycle`. The backend requires an unexpired explicit topology snapshot, looks up
the key there, and never uses request text as a path, location, port number,
executable, or action. Before a disconnecting action, it refreshes only the
passive device monitor and fails closed if current mounted-storage state cannot
be verified. It does not run another `uhubctl` status query. Actions run in a
background thread, and completion invalidates the control snapshot so another
action requires another explicit **Load controls** request.

**Restore USB 2** runs the fixed `recover_usb2.sh` helper in a background
thread under the same serialization lock. The helper verifies the Raspberry Pi
4 internal VIA `2109:3431` hub at
location `1-1`, refuses to continue if any block device below that hub is
mounted, and power-cycles only root-hub location `1`, port `1`. This briefly
disconnects every USB 2 socket while leaving the independent USB 3 bus online.
It never removes or rescans the VL805 PCIe controller.

Before Disable or Cycle, the backend refreshes passive device state and rejects
the action if a filesystem label anywhere downstream currently resolves to a
mounted block device. If passive inspection itself reports an error, the action
is rejected. Use the Disks & Torrents policy/lifecycle controls to unmount
storage first. This protection is independent of the browser confirmation
dialog.

The control sheet is intentionally explicit about location and method. USB
topology locations are stable for a physical socket but device numbers are not;
therefore commands target discovered locations rather than `lsusb` device
numbers.
