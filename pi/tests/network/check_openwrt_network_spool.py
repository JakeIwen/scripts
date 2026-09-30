#!/usr/bin/env python3
"""Exercise a private loopback rsyslog receiver, without changing live services.

Usage: python3 check_openwrt_network_spool.py RECEIVER_CONFIG ROTATION_HELPER
Requires the Pi's installed rsyslogd; all files/listener/processes are temporary.
"""
import json
import grp
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile
import time


def check(config_path, rotation_path):
    with tempfile.TemporaryDirectory(prefix="network-rsyslog-check-") as directory:
        root = Path(directory)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        helper = root / "rotate.py"
        helper.write_text(Path(rotation_path).read_text().replace("Path('/run/vanpi-network/spool')", f'Path({str(root)!r})'))
        helper.chmod(0o755)
        config = Path(config_path).read_text()
        config = config.replace("192.168.6.1", "127.0.0.1")
        config = config.replace('/run/vanpi-network/spool', str(root))
        config = config.replace('port="514"', f'address="127.0.0.1" port="{port}"')
        config = config.replace('fileOwner="root"', f'fileOwner="{pwd.getpwuid(os.getuid()).pw_name}"')
        config = config.replace('fileGroup="adm"', f'fileGroup="{grp.getgrgid(os.getgid()).gr_name}"')
        config = config.replace('rotation.sizeLimit="1048576"', 'rotation.sizeLimit="4096"')
        config = config.replace('/usr/local/libexec/vanpi-rotate-network-log',
                                str(helper))
        target = root / "receiver.conf"
        target.write_text(config)
        validation = subprocess.run(['/usr/sbin/rsyslogd', '-N1', '-f', str(target)],
                                    capture_output=True, text=True)
        assert validation.returncode == 0, validation.stderr
        daemon = subprocess.Popen(['/usr/sbin/rsyslogd', '-n', '-i', str(root / 'pid'),
                                   '-f', str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(0.4)
            assert daemon.poll() is None, daemon.communicate()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                messages = [
                    '<30>Sep 29 22:10:01 fixture uplink-https: interface=wan state=offline google=000/6 cloudflare=000/6',
                    '<30>Sep 29 22:10:02 fixture hostapd: quoted="line\\value"',
                    '<30>Sep 29 22:10:03 fixture dnsmasq[1]: query[A] ordinary-browsing.example from 192.0.2.1',
                    '<30>Sep 29 22:10:04 fixture dnsmasq[1]: no servers found, will retry',
                    '<30>Sep 29 22:10:05 fixture dropbear[1]: irrelevant login',
                ]
                for message in messages:
                    sender.sendto(message.encode(), ('127.0.0.1', port))
                spool = root / 'network.jsonl'
                for _ in range(100):
                    if spool.exists() and len(spool.read_text().splitlines()) >= 3:
                        break
                    time.sleep(0.02)
                rows = [json.loads(line) for line in spool.read_text().splitlines()]
                assert len(rows) == 3, rows
                assert rows[0]['reported_at'][11:19] == '22:10:01', rows[0]
                assert rows[0]['received_at'] != rows[0]['reported_at'], rows[0]
                assert rows[0]['protocol_version'] == '0', rows[0]
                assert 'quoted="line\\value"' in rows[1]['message'], rows[1]
                for number in range(450):
                    message = f'<30>Sep 29 22:10:06 fixture hostapd: burst={number} ' + 'x' * 400
                    sender.sendto(message.encode(), ('127.0.0.1', port))
                    time.sleep(0.004)
                time.sleep(0.3)
                files = list(root.glob('network.jsonl*'))
                assert len(files) == 4, files
                legacy_files = list(root.glob('dendelion.log*'))
                assert len(legacy_files) == 4, legacy_files
                all_rows = [json.loads(line) for path in files for line in path.read_text().splitlines()]
                assert all('ordinary-browsing' not in row['message'] for row in all_rows)
                total = sum(path.stat().st_size for path in files)
                assert total < 4 * (4096 + 2048), total
                return {'valid_json_rows_retained':len(all_rows), 'generations':len(files),
                        'bytes_retained':total, 'isolated_size_limit':4096,
                        'legacy_generations':len(legacy_files),
                        'legacy_bytes_retained':sum(path.stat().st_size for path in legacy_files),
                        'dual_clocks':True, 'ordinary_dns_and_login_excluded':True}
        finally:
            daemon.terminate()
            daemon.communicate(timeout=5)


if __name__ == '__main__':
    print(json.dumps(check(*sys.argv[1:]), indent=2))
