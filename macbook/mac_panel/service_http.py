"""Ownership-aware local HTTP probes; no proxy or PID guessing."""
import json
import socket
from urllib.request import build_opener, ProxyHandler, Request

from ownership_record import read_record
from panel_paths import HOST, PORT, RECORD, URL


class ServiceHttp:
    def __init__(self, record_path=RECORD):
        self.record_path = record_path
        self.opener = build_opener(ProxyHandler({}))

    def health(self):
        try:
            with self.opener.open(URL + '/healthz', timeout=1) as response:
                value = json.loads(response.read(4096))
            if (not isinstance(value, dict) or value.get('ok') is not True
                    or type(value.get('pid')) is not int or not isinstance(value.get('instance'), str)):
                return None
            return value
        except (OSError, ValueError):
            return None

    def occupied(self):
        with socket.socket() as sock:
            sock.settimeout(1)
            return sock.connect_ex(('127.0.0.1', PORT)) == 0

    def assert_owned_listener(self):
        health, record = self.health(), read_record(self.record_path)
        if self.occupied() and not (health and record and health['pid'] == record['pid']
                                    and health['instance'] == record['instance']):
            raise RuntimeError(f'Port {PORT} has a foreign listener; nothing was stopped')
        return record if health and record else None

    def stop(self):
        record = self.assert_owned_listener()
        if record is None:
            return
        request = Request(URL + '/api/stop', data=b'{}', method='POST', headers={
            'Content-Type': 'application/json', 'X-Panel-Token': record['token'],
            'Origin': URL, 'Host': HOST})
        with self.opener.open(request, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError('The owned server refused graceful shutdown')
