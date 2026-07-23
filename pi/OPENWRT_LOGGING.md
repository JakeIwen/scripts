# OpenWrt remote logging

OpenWrt sends classic syslog over the LAN to vanpi. The durable log is:

```text
/var/log/openwrt/dendelion.log
```

The router also retains a 256 KiB in-memory ring, readable with `logread`, for
short vanpi outages. That ring still disappears when OpenWrt reboots. Remote
delivery is UDP and best-effort so an unavailable Pi can never block routing.

Vanpi's rsyslog listener starts on UDP/514 before DHCP is available, but its
dedicated ruleset discards messages unless their source address is
`192.168.6.1`. The log directory is `0750 root:adm`, the file is
`0640 root:adm`, and logrotate retains 30 daily compressed generations while
also capping an active generation at 5 MiB. The normal Borg archive includes
this path because it backs up vanpi's root filesystem.

## Install or restore the receiver

The templates deploy with the rest of `/home/pi/scripts`:

```bash
sudo /home/pi/scripts/setup_openwrt_logging.sh
```

The script installs `rsyslog` and `logrotate`, validates both configurations,
enables the service, and verifies the UDP listener. Then configure the router
from the tracked `vanrouter/etc/config/system` or with UCI and restart `log`.

## Verify

```bash
# vanpi
sudo systemctl status rsyslog
sudo ss -lunp | grep ':514'
sudo tail -n 30 /var/log/openwrt/dendelion.log
sudo logrotate --debug /etc/logrotate.d/openwrt-dendelion

# OpenWrt
uci show system | grep -E 'log_(size|ip|port|proto)'
logger -t remote-log-test 'dendelion remote logging test'
```

The test line should appear on vanpi within a second. If it does not, first
confirm that vanpi still owns `192.168.6.103` and that the router can reach it.
Do not expose UDP/514 through the WAN firewall.
