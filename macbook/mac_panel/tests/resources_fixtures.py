"""Process-table and probe fakes for the Resources tests; nothing here reads the live machine."""
from dataclasses import replace
import os

from cli_runner import CommandResult
from panel_contracts import ApiError
from process_table import ProcessTable, parse_table
from system_metrics import SystemFacts

UID = 501
STARTED = 'Tue Nov 17 22:19:53 2026'
LATER = 'Wed Nov 18 04:11:36 2026'
PANEL_PID = 9000


def ps_line(pid, ppid, command, uid=UID, nice=0, state='S', cpu=0.0, rss_kb=1024,
            started=STARTED, pgid=None, tty='??'):
    """One line of the main `ps` pass (process_table.FIELDS)."""
    return (f'{pid:>5} {ppid:>5} {pgid or pid:>5} {uid:>5} {nice:>2} {state:<4} {cpu:>5.1f} '
            f'{rss_kb:>7} {tty:<8} {started}     {command}')


def name_line(pid, name, started=STARTED):
    """One line of an argv[0] (`comm`) or kernel-name (`ucomm`) pass."""
    return f'{pid:>5} {started} {name}'


def kernel_name_for(argv0):
    """What the kernel would call a process started as argv0 (its file's base name)."""
    return os.path.basename(argv0.lstrip('-'))[:16] or 'x'


def make_texts(*specs):
    """The three `ps` outputs for specs: (pid, ppid, command[, overrides]).

    Overrides are ps_line keywords plus `argv0` (default: the command's first word) and
    `ucomm` (default: argv0's base name).
    """
    main, argv0s, kernel = [], [], []
    for spec in specs:
        pid, ppid, command = spec[:3]
        options = dict(spec[3]) if len(spec) > 3 else {}
        argv0 = options.pop('argv0', command.split(' ', 1)[0])
        ucomm = options.pop('ucomm', kernel_name_for(argv0))
        started = options.get('started', STARTED)
        main.append(ps_line(pid, ppid, command, **options))
        argv0s.append(name_line(pid, argv0, started))
        kernel.append(name_line(pid, ucomm, started))
    return '\n'.join(main), '\n'.join(argv0s), '\n'.join(kernel)


def make_table(*specs):
    """specs: (pid, ppid, command) or (pid, ppid, command, {overrides}); see make_texts."""
    return parse_table(*make_texts(*specs))


class FakeReader:
    """A mutable process world: `table()` and `row()` read it; tests edit it between calls."""

    def __init__(self, table):
        self.rows = dict(table.rows)
        self.table_calls = 0
        self.fail_table = False
        # Runs once, after the next table read: lets a test change the world between the
        # request's first read and the per-target re-reads.
        self.after_table = None

    def table(self, timeout=5):
        self.table_calls += 1
        if self.fail_table:
            raise ApiError(502, 'Process table unavailable')
        snapshot = ProcessTable(dict(self.rows))
        hook, self.after_table = self.after_table, None
        if hook:
            hook(self)
        return snapshot

    def reparent_children(self, pid, new_parent=1):
        for child, row in list(self.rows.items()):
            if row.ppid == pid:
                self.rows[child] = replace(row, ppid=new_parent)


class FakeMetrics:
    def __init__(self, values=None, unavailable=()):
        self.values = values or {}
        self.unavailable = list(unavailable)
        self.calls = 0

    def collect(self):
        self.calls += 1
        return SystemFacts(dict(self.values), list(self.unavailable))


class ArgvRunner:
    """Answers by argv; an unlisted command fails the test, a listed exception is raised."""

    def __init__(self, answers):
        self.answers = dict(answers)
        self.calls = []

    def run(self, argv, timeout=30, env=None):
        self.calls.append(tuple(argv))
        answer = self.answers.get(tuple(argv))
        if answer is None:
            raise AssertionError(f'Unexpected command: {argv}')
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, CommandResult) else CommandResult(0, answer)
