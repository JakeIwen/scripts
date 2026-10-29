"""JSON-only command boundary shared by the dashboard and completion hook."""
import argparse
import contextlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from . import records, plans, runs, settings, link_rebuild
from .apply import apply_plan
from .busy import operation
from .hook import post_download
from .scan import scan
from .status import status
from .undo import undo_run


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def print_help(self, file=None):
        print(json.dumps({'help': self.format_help()}), file=file or sys.stdout)


def parser():
    root = Parser(prog='media-organizer')
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('status', 'scan', 'plans', 'runs', 'post-download'):
        commands.add_parser(name)
    commands.add_parser('propose').add_argument('--classifier', choices=records.CLASSIFIERS)
    for name in ('plan', 'run', 'undo'):
        commands.add_parser(name).add_argument('id')
    apply = commands.add_parser('apply')
    apply.add_argument('id')
    apply.add_argument('--auto', action='store_true')
    decide = commands.add_parser('decide')
    decide.add_argument('id')
    source = decide.add_mutually_exclusive_group(required=True)
    source.add_argument('--decisions-json')
    source.add_argument('--decisions-file', type=Path)
    commands.add_parser('settings').add_argument('--set', action='extend', nargs='+', default=[], dest='assignments')
    commands.add_parser('alias').add_argument('mode', nargs='?', choices=['new'])
    return root


def scan_result(_):
    entries, folders = scan()
    return {'entries': [entry.to_dict() for entry in entries], 'existing_shows': folders}


def propose_result(args):
    preferences = settings.load()
    if args.classifier:
        preferences = replace(preferences, classifier=args.classifier)
    return plans.create_plan(preferences).to_dict()


def decide_result(args):
    payload = (args.decisions_file.read_text(encoding='utf-8')
               if args.decisions_file else args.decisions_json)
    return plans.decide(plans.load(args.id), json.loads(payload)).to_dict()


def alias_result(args):
    with operation(records.JOB_ALIAS):
        # The alias entrypoint owns its child and the same descriptor lock.
        with contextlib.redirect_stdout(sys.stderr):
            return link_rebuild.alias([args.mode] if args.mode else [])


COMMANDS = {
    'status': lambda args: status().to_dict(),
    'scan': scan_result,
    'propose': propose_result,
    'plans': lambda args: {'plans': [plan.summary() for plan in plans.list_plans()]},
    'plan': lambda args: plans.load(args.id).to_dict(),
    'decide': decide_result,
    'apply': lambda args: apply_plan(plans.load(args.id),
                                    records.RUN_AUTO if args.auto else records.RUN_MANUAL,
                                    settings.load()).to_dict(),
    'runs': lambda args: {'runs': [run.summary() for run in runs.list_runs()]},
    'run': lambda args: runs.load(args.id).to_dict(),
    'undo': lambda args: undo_run(runs.load(args.id)).to_dict(),
    'settings': lambda args: (settings.update(args.assignments) if args.assignments
                              else settings.load()).to_dict(),
    'post-download': lambda args: post_download(settings.load()),
    'alias': alias_result,
}


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result = COMMANDS[args.command](args)
        code = 1 if isinstance(result, dict) and result.get('error') else 0
    except Exception as error:
        result, code = {'error': str(error)}, 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code
