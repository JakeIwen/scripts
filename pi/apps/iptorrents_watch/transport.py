"""Bounded HTTP reads with credential-safe diagnostics and redirect handling."""
import gzip
import io
import json
import stat
import zlib
from urllib import error, request
from urllib.parse import urljoin, urlsplit
from . import config


class LoginNeeded(RuntimeError):
    pass


class SiteUnavailable(RuntimeError):
    pass


class SameOriginRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        if urlsplit(target)[:2] != urlsplit(req.full_url)[:2]:
            raise SiteUnavailable('IPTorrents redirected outside its configured origin')
        return super().redirect_request(req, fp, code, msg, headers, target)


def decode_body(body, limit):
    if body.startswith(b'\x1f\x8b'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(limit + 1)
        except (OSError, EOFError, zlib.error):
            raise ValueError('invalid compressed response') from None
    if len(body) > limit:
        raise ValueError('response exceeds size limit')
    return body


def cookies():
    try:
        mode = config.COOKIE_PATH.stat().st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
            raise LoginNeeded('IPTorrents cookie file must have mode 600')
        data = json.loads(config.COOKIE_PATH.read_text(encoding='utf-8'))
        values = [data[key] for key in ('uid', 'pass')]
        if any(not isinstance(v, str) or not v or any(c in v for c in ';\r\n')
               or not v.isascii() for v in values):
            raise ValueError()
        return f'uid={values[0]}; pass={values[1]}'
    except LoginNeeded:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise LoginNeeded('IPTorrents cookies are missing or invalid; refresh the cookie file') from None


def site_get(path, limit=config.MAX_RESPONSE_BYTES):
    # Paths come from remote HTML: never forward the cookies to another origin.
    if not path.startswith('/') or path.startswith('//') or '\\' in path:
        raise ValueError('invalid IPTorrents request path')
    from urllib.parse import quote
    url = config.SITE_URL.rstrip('/') + quote(path, safe='/%?=;&:+#')
    req = request.Request(url, headers={'Cookie': cookies(), 'User-Agent': config.USER_AGENT})
    try:
        opener = request.build_opener(SameOriginRedirect())
        with opener.open(req, timeout=config.REQUEST_TIMEOUT) as response:
            return decode_body(response.read(limit + 1), limit)
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LoginNeeded('IPTorrents login or browser verification is needed') from None
        raise SiteUnavailable(f'IPTorrents returned HTTP {exc.code}') from None
    except (error.URLError, OSError, ValueError):
        # Never stringify a transport exception: it can contain the request headers.
        raise SiteUnavailable('IPTorrents request failed (network, timeout or invalid response)') from None


def get_json(url):
    try:
        with request.urlopen(url, timeout=config.REQUEST_TIMEOUT) as response:
            return json.loads(decode_body(response.read(config.MAX_RESPONSE_BYTES + 1),
                                          config.MAX_RESPONSE_BYTES))
    except (error.URLError, OSError, ValueError):
        raise RuntimeError('local library or qBittorrent API is unavailable or returned invalid JSON') from None
