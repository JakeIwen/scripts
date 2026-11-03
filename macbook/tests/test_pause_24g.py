"""pause_24g.zsh runs its router-side script against fake iw and ubus."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'macbook' / 'scripts' / 'pause_24g.zsh'
MAC = '5a:d2:10:cf:72:43'

# Stands in for ssh to the router: runs the piped script locally, with the
# router's absolute tool paths pointed at the fakes in $FAKE.
FAKE_SSH = r'''#!/bin/sh
while [ "$1" = -o ]; do shift 2; done
shift 2
sed -e "s#/usr/sbin/iw#$FAKE/iw#g" -e "s#/bin/ubus#$FAKE/ubus#g" \
    -e "s#/tmp/dhcp.leases#$FAKE/leases#g" -e "s#/bin/grep#grep#g" \
  | /bin/sh "$@"
'''

FAKE_IW = r'''#!/bin/sh
# iw dev <ifname> info | iw dev <ifname> station get <mac>
case $3 in
  info) cat "$FAKE/info-$2" 2>/dev/null ;;
  station) grep -qx "$5" "$FAKE/stations-$2" 2>/dev/null ;;
esac
'''

FAKE_UBUS = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE/ubus.log"\n'

BEACONING = '\tssid dendelion\n\ttype AP\n\tchannel 149 (5745 MHz)\n'
SILENT = '\ttype AP\n\ttxpower 27.00 dBm\n'


class Pause24gTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = Path(self.tmp.name)
        for name, body in (('ssh', FAKE_SSH), ('iw', FAKE_IW), ('ubus', FAKE_UBUS)):
            path = self.fake / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        (self.fake / 'leases').write_text(
            f'1793761054 {MAC} 192.168.6.116 m4mac 01:{MAC}\n')
        (self.fake / 'info-wl0-ap0').write_text('\tssid dendelion\n\ttype AP\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_pause(self, *, on: str, info_5g: str):
        (self.fake / f'stations-{on}').write_text(MAC + '\n')
        (self.fake / 'info-wl1-ap0').write_text(info_5g)
        env = dict(os.environ, FAKE=str(self.fake),
                   PAUSE_24G_SSH_BIN=str(self.fake / 'ssh'))
        return subprocess.run(['zsh', str(SCRIPT), '15'], env=env,
                              capture_output=True, text=True, timeout=30)

    def ubus_calls(self):
        log = self.fake / 'ubus.log'
        return log.read_text().splitlines() if log.exists() else []

    def test_bans_client_from_24g_when_5g_is_broadcasting(self):
        result = self.run_pause(on='wl0-ap0', info_5g=BEACONING)
        self.assertEqual(result.returncode, 0, result.stderr)
        [call] = self.ubus_calls()
        self.assertIn('hostapd.wl0-ap0 del_client', call)
        self.assertIn(f'"addr":"{MAC}"', call)
        self.assertIn('"ban_time":15000', call)

    def test_leaves_client_alone_when_5g_is_not_broadcasting(self):
        result = self.run_pause(on='wl0-ap0', info_5g=SILENT)
        self.assertEqual(result.returncode, 1)
        self.assertIn('not broadcasting dendelion', result.stderr)
        self.assertEqual(self.ubus_calls(), [])

    def test_reports_client_already_on_5g(self):
        result = self.run_pause(on='wl1-ap0', info_5g=BEACONING)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already associated with the 5 GHz AP', result.stdout)
        self.assertEqual(self.ubus_calls(), [])


if __name__ == '__main__':
    unittest.main()
