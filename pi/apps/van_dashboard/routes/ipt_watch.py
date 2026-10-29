"""Watchlist HTTP boundary; the CLI owns all IPT and watch-folder operations."""
from functools import partial

from flask import Blueprint

from ..http import runtime_proxy
from .cli_boundary import body as cli_body, endpoint

bp = Blueprint('ipt_watch', __name__, url_prefix='/api/ipt-watch')
control = runtime_proxy('ipt_watch')
body = partial(cli_body, label='Watchlist')


@bp.get('/status')
@endpoint
def status():
    return control.status()


@bp.post('/check')
@endpoint
def check():
    payload = body(optional=('show',))
    return control.check(**payload), 202


@bp.post('/wantlist')
@endpoint
def wantlist():
    payload = body(('action', 'name'), ('season', 'episode'))
    return control.wantlist(**payload)


@bp.post('/download')
@endpoint
def download():
    payload = body(('torrent_id',))
    return control.candidate('download', payload['torrent_id'])


@bp.post('/dismiss')
@endpoint
def dismiss():
    payload = body(('torrent_id',))
    return control.candidate('dismiss', payload['torrent_id'])
