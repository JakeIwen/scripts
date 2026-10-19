"""Manage Mac Panel's login LaunchAgent without sudo."""
import argparse
import json
import sys

from log_redaction import redact
from service_controller import PanelService

VERBS = ('prepare', 'install', 'status', 'start', 'stop', 'restart', 'verify', 'uninstall')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb', choices=VERBS)
    args = parser.parse_args(argv)
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
