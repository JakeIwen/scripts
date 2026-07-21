# Van Dashboard USB port controls

The USB Devices sheet combines two passive data sources:

- `usb_watch.py --json` supplies readable `lsusb` names, filesystem labels,
  and stable Linux topology locations for each present device.
- `sudo -n /usr/sbin/uhubctl` identifies hubs that advertise port power
  switching and reports their current port state.

Ports marked **POWER + DATA** are controlled with a fixed `uhubctl` command.
`uhubctl` handles paired USB 2 and USB 3 logical hubs automatically. Hardware
can still disconnect data without actually removing VBUS even when it advertises
power switching.

Ports marked **DATA ONLY** belong to hubs that do not advertise independent
power switching. Those controls write only `0` or `1` to the already-discovered
Linux `...-portN/disable` file through `sudo tee`. They disconnect and reconnect
the logical USB path; they do not claim to remove electrical power. A USB 3 hub
can expose separate USB 2 and USB 3 logical DATA ONLY controls.

The browser sends only a server-issued port key and one of `on`, `off`, or
`cycle`. The backend looks up the key in its latest topology snapshot and never
uses request text as a path, location, port number, executable, or action.
Actions run in a background thread so a slow re-enumeration does not block the
dashboard request.

Before Off or Cycle, the backend refreshes topology and rejects the action if a
filesystem label anywhere downstream currently resolves to a mounted block
device. Use the Disks & Torrents policy/lifecycle controls to unmount storage
first. This protection is independent of the browser confirmation dialog.

The control sheet is intentionally explicit about location and method. USB
topology locations are stable for a physical socket but device numbers are not;
therefore commands target discovered locations rather than `lsusb` device
numbers.
