"""IPT search and download endpoints; never retain response bodies in state."""
from urllib.parse import quote
from . import config, parser, transport


class Site:
    def __init__(self, get=None):
        self.get = get or transport.site_get

    def search(self, name):
        body = self.get('/t?q=' + quote(name, safe='') + ';qf=ti', config.MAX_RESPONSE_BYTES)
        text = transport.decode_body(body, config.MAX_RESPONSE_BYTES).decode('utf-8', errors='replace')
        try:
            parser.table(text)
        except parser.ParseError:
            raise transport.LoginNeeded('IPTorrents login or browser verification is needed') from None
        if not parser.logged_in(text):
            raise transport.LoginNeeded('IPTorrents login or browser verification is needed')
        return parser.parse(text)

    def download(self, path):
        return self.get(path, config.MAX_TORRENT_BYTES)
