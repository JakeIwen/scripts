"""Manage Mac Panel's login LaunchAgent without sudo."""
import argparse
import json
import sys

from log_redaction import redact
from service_controller import PanelService

VERBS = ('prepare', 'install', 'status', 'start', 'stop', 'restart', 'verify', 'uninstall', 'sudoers')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb', choices=VERBS)
    options = parser.add_mutually_exclusive_group()
    options.add_argument('--print', dest='print_rule', action='store_true')
    options.add_argument('--script', action='store_true')
    args = parser.parse_args(argv)
    if args.verb == 'sudoers':
        from sudoers_rule import installer, rule
        if not (args.print_rule or args.script):
            parser.error('sudoers requires --print or --script')
        print(installer() if args.script else rule(), end='\n' if args.print_rule else '')
        return 0
    if args.print_rule or args.script:
        parser.error('--print and --script require sudoers')
    service = PanelService()
    try:
        method = service.restart if args.verb == 'verify' else getattr(service, args.verb)
        result = method()
        print(json.dumps(result, indent=2))
        return 1 if args.verb == 'status' and not result['healthy'] else 0
    except Exception as exc:
        print(redact(str(exc)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
