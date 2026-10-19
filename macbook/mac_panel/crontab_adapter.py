"""Read-only crontab parser, ownership attribution and tracked-file drift."""
from collections import Counter
import os
from pathlib import Path
import re
import shlex

from cli_runner import checked
from cron_schedule import describe_schedule
from log_redaction import safe_display_path
from panel_contracts import CronJob, CrontabPayload, OwnerKind
from panel_paths import HOME, ROOT

ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')
VARIABLE = re.compile(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))')
BEGIN = re.compile(r'^# BEGIN managed (.+)$')
END = re.compile(r'^# END managed (.+)$')
MANAGED_OWNERS = {'llm-restore': 'shared/sh/llm_restore.py --cron',
                  'codex-release-cleanup': 'macbook/crontab'}


def normalized_lines(text):
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def drift(live, repo):
    a, b = Counter(normalized_lines(live)), Counter(normalized_lines(repo))
    return list((a - b).elements()), list((b - a).elements())


def expand_path(value, variables, home):
    value = VARIABLE.sub(lambda m: variables.get(m[1] or m[2], m[0]), value)
    if value == '~' or value.startswith('~/'):
        value = str(home) + value[1:]
    return value


def redirected_log(command, variables, home):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='><&|;')
        lexer.whitespace_split = True
        tokens = list(lexer)
        for i, token in enumerate(tokens[:-1]):
            if token not in ('>', '>>'):
                continue
            value = expand_path(tokens[i + 1], variables, home)
            if '$' in value or value == '/dev/null':
                continue
            path = Path(value)
            if not path.is_absolute() or not safe_display_path(path):
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = None
            return str(path), modified
    except ValueError:
        pass
    return None, None


def cron_job(line, comment, block, tracked, variables, home) -> CronJob | None:
    parsed = line.lstrip()
    parts = parsed.split(None, 1 if parsed.startswith('@') else 5)
    if len(parts) != (2 if parsed.startswith('@') else 6):
        return None
    expression, command = ' '.join(parts[:-1]), parts[-1]
    if block:
        owner_kind = OwnerKind.MANAGED
        owner = f'{block} — {MANAGED_OWNERS.get(block, "managed block")}'
    else:
        owner_kind = OwnerKind.TRACKED if line.rstrip() in tracked else OwnerKind.LIVE
        owner = 'macbook/crontab' if owner_kind == OwnerKind.TRACKED else 'live only'
    log_path, modified = redirected_log(command, variables, home)
    return CronJob(expression=expression, schedule=describe_schedule(expression), command=command,
                   description=comment, owner_kind=owner_kind, owner=owner,
                   log_path=log_path, log_modified=modified)


def parse_crontab(live, repo, home=HOME) -> CrontabPayload:
    variables = {'HOME': str(home)}
    tracked = set(normalized_lines(repo))
    jobs, comment, block = [], '', None
    for original in live.splitlines():
        line = original.strip()
        if match := BEGIN.fullmatch(line):
            block, comment = match[1], ''
        elif END.fullmatch(line):
            block, comment = None, ''
        elif line.startswith('#'):
            comment = line[1:].strip()
        elif not line:
            comment = ''
        elif match := ASSIGNMENT.fullmatch(line):
            raw = match[2]
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
                raw = raw[1:-1]
            variables[match[1]] = expand_path(raw, variables, home)
            comment = ''
        else:
            job = cron_job(original.rstrip(), comment, block, tracked, variables, home)
            if job:
                jobs.append(job)
            comment = ''
    live_only, repo_only = drift(live, repo)
    in_sync = normalized_lines(live) == normalized_lines(repo)
    return CrontabPayload(jobs=jobs, in_sync=in_sync,
                          order_changed=not in_sync and not live_only and not repo_only,
                          live_only=live_only, repo_only=repo_only)


class CrontabAdapter:
    def __init__(self, runner, tracked=None, home=HOME):
        self.runner, self.home = runner, home
        self.tracked = tracked if tracked is not None else ROOT / 'macbook/crontab'

    def read(self):
        result = self.runner.run(('/usr/bin/crontab', '-l'))
        if result.returncode == 1 and 'no crontab for' in result.stderr.lower():
            live = ''
        else:
            live = checked(result).stdout
        return parse_crontab(live, self.tracked.read_text(), self.home)
