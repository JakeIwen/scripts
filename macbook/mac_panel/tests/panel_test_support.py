"""Isolated CLI and HTTP fixtures; no live tools or home configuration."""
from contextlib import contextmanager
import http.client
import json
import threading

from cli_runner import CommandResult
from panel_contracts import Reply
from panel_paths import HOST
from server import PanelServer


class FakeRunner:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    def run(self, argv, timeout=30):
        self.calls.append((tuple(argv), timeout))
        if not self.results:
            raise AssertionError(f'Unexpected command: {argv}')
        result = self.results.pop(0)
        return result() if callable(result) else result


def json_command(value, code=0):
    return CommandResult(code, json.dumps(value))


class EmptyRoutes:
    def dispatch(self, method, path, body=None):
        return Reply({'method': method, 'path': path})


@contextmanager
def running_server(routes=None):
    server = PanelServer(routes or EmptyRoutes(), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def request(server, path, method='GET', body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    connection.putrequest(method, path, skip_host=True)
    for key, value in (headers or {}).items():
        if value is not None:
            connection.putheader(key, value)
    raw = body.encode() if isinstance(body, str) else body
    if raw is not None and 'Content-Length' not in (headers or {}):
        connection.putheader('Content-Length', str(len(raw)))
    connection.endheaders(raw)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read().decode()
    connection.close()
    return result


def auth(server, post=False):
    value = {'Host': HOST, 'X-Panel-Token': server.record['token']}
    if post:
        value.update({'Origin': f'http://{HOST}', 'Content-Type': 'application/json'})
    return value
