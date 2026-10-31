"""Public-entry metering fixture: real files and wire records, fake OS commands."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

from shared.metered.config import ConfigStore
from shared.metered.records import Effective, EffectiveSource, Host, HostState, SyncRequest, SyncResponse, Verdict
from macbook.metered.config import Paths
from macbook.metered.controller import Controller
from macbook.metered.low_data_mode import LowDataMode
from macbook.metered.network import collect
from macbook.metered.sync import PiTransport


class MacFixture:
    def __init__(self, root):
        self.paths = Paths(Path(root) / 'state', Path(root) / 'settings/metered.json')
        self.remote = ConfigStore(Path(root) / 'pi/config.json')
        self.now = 1000.0
        self.ssid = self.remote.load().home_network.ssid
        self.bssid = '11:22:33:44:55:66'
        self.lan = True
        self.online = True
        self.pi_metered = True
        self.pi_deadline = None
        self.pi_evidence = None
        self.flag = False
        self.fail_sudo = False
        self.lose_reply = False
        self.calls = []
        self.reports = []
        self.controller = Controller(
            paths=self.paths, collector=lambda: collect(self.run, self.wifi, lambda: self.now),
            transport=PiTransport(self.run), low_data=LowDataMode(self.paths, self.run),
            clock=lambda: self.now)

    def wifi(self):
        return {'interface': 'en0', 'ssid': self.ssid, 'bssid': self.bssid,
                'status': 'ok', 'detail': 'fixture'}

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        text, code = '', 0
        if argv[0] == '/sbin/route':
            text = 'gateway: ' + (self.remote.load().home_network.gateway if self.lan else '10.0.0.1')
        elif argv[0] == '/usr/sbin/arp':
            mac = self.remote.load().home_network.gateway_mac if self.lan else 'aa:bb:cc:dd:ee:ff'
            text = '? (gateway) at ' + mac + ' on en0 ifscope [ethernet]'
        elif argv[0] == '/usr/sbin/networksetup':
            text = 'Hardware Port: Wi-Fi\nDevice: en0\n'
        elif argv[0] == '/sbin/ifconfig':
            text = 'en0: flags=8863<UP,RUNNING> mtu 1500\n\teflags=1<' + ('CONSTRAINED' if self.flag else 'TXSTART') + '>\n'
        elif argv[0] == '/usr/bin/sudo':
            code = 1 if self.fail_sudo else 0
            if not code:
                self.flag = argv[-1] == 'constrained'
        elif argv[0] == '/usr/bin/ssh':
            if not self.online:
                code = 255
            else:
                text = self.ssh(argv[-1], json.loads(kwargs['input']))
                if self.lose_reply:
                    code = 255
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, code, text, '')

    def ssh(self, verb, payload):
        if verb == 'edit-ssids':
            config = self.remote.edit_ssid(payload['op'], payload['ssid'], payload['revision'])
            return json.dumps(config.as_dict())
        request = SyncRequest.from_dict(payload)
        self.reports.append(request.mac_report)
        config, applied = self.remote.apply_ops(request.ops)
        verdict = Verdict(self.pi_metered, 'fixture uplink', [], self.pi_evidence or self.now)
        state = HostState(Host.PI, self.now, verdict,
                          Effective(self.pi_metered is not False, EffectiveSource.VERDICT, None), {},
                          config_revision=config.revision,
                          valid_until=self.pi_deadline or self.now + 30)
        return json.dumps(SyncResponse(config, state, applied).as_dict())

    def advance(self, seconds=60):
        self.now += seconds

    def sudo_calls(self):
        return [argv for argv, kwargs in self.calls if argv[0] == '/usr/bin/sudo']

    def ssh_calls(self):
        return [argv for argv, kwargs in self.calls if argv[0] == '/usr/bin/ssh']
