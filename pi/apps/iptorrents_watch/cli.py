"""JSON-only command contract for the dashboard and scheduled checks."""
import argparse
import json
import sys
from . import check, download, status, wantlist


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def print_help(self, file=None):
        print(json.dumps({'help': self.format_help()}), file=file or sys.stdout)


def parser():
    root = Parser(prog='iptorrents-watch')
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('status', 'candidates'):
        commands.add_parser(name)
    commands.add_parser('check').add_argument('--show')
    for name in ('download', 'dismiss'):
        commands.add_parser(name).add_argument('torrent_id')
    wanted = commands.add_parser('wantlist').add_subparsers(dest='action')
    for action in ('add', 'remove', 'pause', 'resume'):
        sub = wanted.add_parser(action)
        sub.add_argument('name')
        if action == 'add':
            sub.add_argument('--season', type=int)
            sub.add_argument('--episode', type=int)
    return root


def wanted_result(args):
    rows = (wantlist.listing() if args.action is None else
            wantlist.edit(args.action, args.name, getattr(args, 'season', None), getattr(args, 'episode', None)))
    return {'wanted': [row.to_dict() for row in rows]}


COMMANDS = {
    'status': lambda args: status.status().to_dict(),
    'wantlist': wanted_result,
    'check': lambda args: check.run(args.show).to_dict(),
    'candidates': lambda args: {'candidates': [row.to_dict() for row in status.candidates()]},
    'download': lambda args: download.download(args.torrent_id).to_dict(),
    'dismiss': lambda args: download.dismiss(args.torrent_id).to_dict(),
}


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result, code = COMMANDS[args.command](args), 0
    except Exception as error:
        result, code = {'error': str(error)}, 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code
