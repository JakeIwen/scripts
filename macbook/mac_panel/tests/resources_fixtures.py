"""Process-table and probe fakes for the Resources tests; nothing here reads the live machine."""
from dataclasses import replace

from cli_runner import CommandResult
from panel_contracts import ApiError
from process_table import ProcessTable, parse_table
from system_metrics import SystemFacts

UID = 501
STARTED = 'Tue Nov 17 22:19:53 2026'
LATER = 'Wed Nov 18 04:11:36 2026'
PANEL_PID = 9000


def ps_line(pid, ppid, command, uid=UID, nice=0, state='S', cpu=0.0, rss_kb=1024,
            started=STARTED, pgid=None):
    return (f'{pid:>5} {ppid:>5} {pgid or pid:>5} {uid:>5} {nice:>2} {state:<4} {cpu:>5.1f} '
            f'{rss_kb:>7} {started}     {command}')


def make_table(*specs, is_file=lambda _path: False):
    """specs: (pid, ppid, command) or (pid, ppid, command, {ps_line keyword overrides})."""
    lines = [ps_line(*spec[:3], **(spec[3] if len(spec) > 3 else {})) for spec in specs]
    return parse_table('\n'.join(lines), is_file)


class FakeReader:
    """A mutable process world: `table()` and `row()` read it; tests edit it between calls."""

    def __init__(self, table):
        self.rows = dict(table.rows)
        self.table_calls = 0
        self.row_calls = []
        self.fail_table = False
        # Runs after a table read: lets a test change the world between snapshot and signal.
        self.after_table = None

    def table(self, timeout=5):
        self.table_calls += 1
        if self.fail_table:
            raise ApiError(502, 'Process table unavailable')
        snapshot = ProcessTable(dict(self.rows))
        if self.after_table:
            self.after_table(self)
        return snapshot

    def row(self, pid, timeout=5):
        self.row_calls.append(pid)
        return self.rows.get(pid)

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
