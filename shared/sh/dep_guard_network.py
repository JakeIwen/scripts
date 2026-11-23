"""Bounded public-registry HTTP for one dependency check. No proxies or redirects."""

from collections import deque
from dataclasses import dataclass
import http.client
import json
import os
import stat
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit


HOSTS = {'registry.npmjs.org', 'api.npmjs.org', 'pypi.org', 'pypistats.org', 'api.osv.dev'}
SMALL = 256 * 1024
METADATA = 2 * 1024 * 1024
TOTAL = 8 * 1024 * 1024
MAX_REQUESTS = 40
CONCURRENT = 6
DEADLINE_SECONDS = 5
COOLDOWN = 120
CHUNK = 64 * 1024


@dataclass(eq=False)
class _Request:
    host: str
    operation: str
    limit: int
    received: int = 0
    status: int = 0
    traced: bool = False


def _trace(path, request):
    if not path:
        return
    # Single private append; no query, body, headers or error text on any path.
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags, 0o600)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                row = {'host': request.host, 'operation': request.operation, 'bytes': request.received}
                os.write(fd, (json.dumps(row, ensure_ascii=True) + '\n').encode('ascii'))
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass  # a trace destination cannot change a verdict


def _target(url):
    if not isinstance(url, str) or any(not 33 <= ord(c) <= 126 for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    # No userinfo, port override, alternative scheme, fragment or foreign host.
    if parts.scheme == 'https' and parts.netloc in HOSTS and not parts.fragment:
        return parts
    return None


def _operation(parts, method):
    if parts.netloc == 'registry.npmjs.org':
        return 'exists' if parts.path.startswith('/-/') else 'metadata'
    if parts.netloc == 'pypi.org':
        return 'exists' if method == 'HEAD' else 'metadata'
    return {'api.npmjs.org': 'downloads', 'pypistats.org': 'stats', 'api.osv.dev': 'osv'}[parts.netloc]


class Network:
    def __init__(self, cache, transport=None, trace=None, seconds=DEADLINE_SECONDS):
        self.cache = cache
        self.transport = transport
        self.trace = trace if trace is not None else os.environ.get('DEP_INSTALL_GUARD_TRACE')
        self._deadline = time.monotonic() + min(DEADLINE_SECONDS, seconds)
        self._condition = threading.Condition()
        self._workers = threading.local()
        self._active = set()
        self._skipped_hosts = set()
        self._available = TOTAL
        self.requests = 0
        self.bytes_received = 0

    def remaining(self):
        return max(0.0, self._deadline - time.monotonic())

    def map(self, tasks):
        """Do not join unfinished workers: a DNS resolver needn't honor socket timeouts."""
        pending = deque(tasks.items())
        done = threading.Condition()
        results = {}
        left = len(pending)

        def run():
            nonlocal left
            self._workers.inside = True
            while self.remaining():
                with done:
                    if not pending:
                        return
                    key, task = pending.popleft()
                try:
                    value = task()
                except Exception:
                    pass  # an unfinished check isn't evidence against completed checks
                else:
                    with done:
                        if self.remaining():
                            results[key] = value
                finally:
                    with done:
                        left -= 1
                        done.notify_all()

        for _ in range(min(left, CONCURRENT)):
            if not self.remaining():
                break
            threading.Thread(target=run, daemon=True).start()
        with done:
            done.wait_for(lambda: left == 0, timeout=self.remaining())
            completed = results.copy()
        if not self.remaining():
            with self._condition:
                for request in self._active:
                    self._trace_once(request)
                    if not request.status:
                        self._offline(request.host)
        return completed

    @property
    def cooldown_hosts(self):
        """Hosts where this run actually skipped a request due to cooldown."""
        with self._condition:
            return tuple(sorted(self._skipped_hosts))

    def _offline(self, host):
        self.cache.put('offline:' + host, True, COOLDOWN)

    def _trace_once(self, request):
        if not request.traced:
            _trace(self.trace, request)
            request.traced = True

    def _reserve(self, parts, operation, limit):
        # Reserve the entire read allowance before opening a socket. Unused
        # bytes return to the pool, even on 404, malformed JSON or timeout.
        with self._condition:
            while self.remaining() and self.requests < MAX_REQUESTS:
                if self.cache.get('offline:' + parts.netloc):
                    self._skipped_hosts.add(parts.netloc)
                    return None
                if not self._active or (len(self._active) < CONCURRENT and self._available >= limit):
                    allowance = min(self._available, limit)
                    if allowance <= 0:
                        return None
                    request = _Request(parts.netloc, operation, allowance)
                    self._active.add(request)
                    self._available -= allowance
                    self.requests += 1
                    return request
                self._condition.wait(timeout=self.remaining())
        return None

    def _http(self, request, method, parts, body):
        connection = http.client.HTTPSConnection(request.host, timeout=self.remaining())
        try:
            connection.connect()
            if not self.remaining():
                raise TimeoutError
            connection.sock.settimeout(self.remaining())
            target = parts.path or '/'
            if parts.query:
                target += '?' + parts.query
            data = None if body is None else json.dumps(body, allow_nan=False).encode('utf-8')
            headers = {'Accept': 'application/json'}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            connection.request(method, target, body=data, headers=headers)
            with connection.getresponse() as response:
                request.status = response.status
                if method == 'HEAD' or 300 <= response.status < 400:
                    return None
                return self._read(request, response)
        finally:
            connection.close()

    def _read(self, request, response):
        if response.length is not None and response.length > request.limit:
            return None
        chunks = []
        while request.received < request.limit and self.remaining():
            chunk = response.read1(min(CHUNK, request.limit - request.received))
            if not chunk:
                return None if response.length else b''.join(chunks)
            chunks.append(chunk)
            request.received += len(chunk)
            if response.length == 0 or response.isclosed():
                return None if response.length else b''.join(chunks)
        # At the cap without proven EOF: do not spend another byte to test it.
        return None

    def _fetch(self, parts, url, method, body, limit):
        operation = _operation(parts, method)
        limit = min(limit, METADATA if operation == 'metadata' else SMALL)
        request = self._reserve(parts, operation, limit)
        if request is None:
            return 0, None
        try:
            if self.transport is None:
                raw = self._http(request, method, parts, body)
            else:
                status, raw = self.transport(method, url, body, self.remaining(), request.limit)
                request.status = status
                request.received = len(raw)
                if status == 0:
                    self._offline(request.host)
                if method == 'HEAD' or request.received > request.limit:
                    raw = None
            if not raw:
                return request.status, None
            try:
                return request.status, json.loads(raw)
            except (ValueError, RecursionError):
                return request.status, None
        except HTTPError as error:  # a transport may surface e.g. 429 as an exception
            if error.fp is not None:
                error.close()
            return error.code, None
        except OSError:
            self._offline(request.host)
            return request.status, None
        except (http.client.HTTPException, ValueError, TypeError):
            return request.status, None
        finally:
            with self._condition:
                self._trace_once(request)
                self.bytes_received += request.received
                self._available += request.limit - request.received
                self._active.remove(request)
                self._condition.notify_all()

    def get_json(self, url, *, method='GET', body=None, limit=SMALL):
        """Return observed status even for an empty, oversized or non-JSON response."""
        parts = _target(url)
        if parts is None or method not in ('GET', 'HEAD', 'POST') or limit <= 0:
            return 0, None
        if getattr(self._workers, 'inside', False):
            return self._fetch(parts, url, method, body, limit)
        result = self.map({0: lambda: self._fetch(parts, url, method, body, limit)})
        return result.get(0, (0, None))
