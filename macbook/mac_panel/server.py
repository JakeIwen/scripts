"""Loopback-only standard-library HTTP entry point for Mac Panel."""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import threading
import traceback
from urllib.parse import urlsplit

from log_redaction import redact_data
from ownership_record import new_record, remove_record, write_record
from panel_contracts import ApiError, Health
from panel_paths import DIRECTORY, PORT, RECORD
from request_guard import SECURITY_HEADERS, guard_request, read_body

STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, routes, record=None, port=PORT):
        self.routes = routes
        self.record = record or new_record()
        super().__init__(('127.0.0.1', port), PanelHandler)

    def health(self) -> Health:
        return Health(ok=True, pid=self.record['pid'], instance=self.record['instance'],
                      started_at=self.record['started_at'])

    def graceful_stop(self):
        threading.Thread(target=self.shutdown, daemon=True).start()


class PanelHandler(BaseHTTPRequestHandler):
    server_version = 'MacPanel'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        # URLs, bodies and CLI output are not access-log material.
        pass

    def send_payload(self, status, data, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, value):
        self.send_payload(status, json.dumps(redact_data(value), allow_nan=False).encode())

    def handle_request(self, method):
        try:
            path = urlsplit(self.path).path
            size = guard_request(method, path, self.headers, self.server.record['token'])
            body = read_body(self.rfile, size) if method == 'POST' else None
            if method == 'GET' and path == '/healthz':
                self.send_json(200, self.server.health())
            elif method == 'GET' and path in STATIC:
                name, content_type = STATIC[path]
                data = (DIRECTORY / 'static' / name).read_text()
                if name == 'index.html':
                    data = data.replace('__PANEL_TOKEN__', html.escape(self.server.record['token'], quote=True))
                self.send_payload(200, data.encode(), content_type)
            elif method == 'POST' and path == '/api/stop':
                if body:
                    raise ApiError(400, 'Stop requires an empty object')
                self.send_json(200, {'stopping': True})
                self.server.graceful_stop()
            elif method in ('GET', 'POST'):
                reply = self.server.routes.dispatch(method, path, body)
                self.send_json(reply.status, reply.body)
            else:
                raise ApiError(405, 'Method not allowed')
        except ApiError as exc:
            self.send_json(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            # The private panel log is the only place a failing adapter's cause shows up.
            traceback.print_exc()
            self.send_json(500, {'error': 'Section unavailable; see tmp/mac-panel/panel.log'})

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def do_OPTIONS(self):
        self.handle_request('OPTIONS')

    def do_HEAD(self):
        self.handle_request('HEAD')

    def do_PUT(self):
        self.handle_request('PUT')

    def do_PATCH(self):
        self.handle_request('PATCH')

    def do_DELETE(self):
        self.handle_request('DELETE')

    def do_TRACE(self):
        self.handle_request('TRACE')

    def do_CONNECT(self):
        self.handle_request('CONNECT')


def main():
    from panel_routes import PanelRoutes
    os.umask(0o077)
    routes = PanelRoutes()
    httpd = PanelServer(routes)
    write_record(RECORD, httpd.record)
    signal.signal(signal.SIGTERM, lambda *_: httpd.graceful_stop())
    signal.signal(signal.SIGINT, lambda *_: httpd.graceful_stop())
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()
        remove_record(RECORD, httpd.record)
        routes.close()


if __name__ == '__main__':
    main()
