"""Every grouping fixture must keep membership, group members and totals in agreement."""
import unittest

from resource_groups import TOTAL_KINDS, build_grouping
from resources_fixtures import PANEL_ROW, make_table
from test_resource_groups import WORLD

LAUNCHD = (1, 0, '/sbin/launchd', {'uid': 0})
SHELL_JOB = '/bin/sh /Users/me/Library/Caches/van-compute/jobs/job-1/run.sh'

# Each fixture is a spec tuple for make_table.
FIXTURES = {
    'world': WORLD,
    'checker under a job (round 5)': (
        LAUNCHD, PANEL_ROW, (200, 1, 'claude'),
        (210, 200, 'python3 -m van_compute.worker'), (211, 210, SHELL_JOB),
        (212, 211, 'python3 check.py van_compute.worker'), (213, 212, 'sleep 1')),
    'checker under a job, no session': (
        LAUNCHD, (210, 1, 'python3 -m van_compute.worker'), (211, 210, SHELL_JOB),
        (212, 211, 'python3 check.py van_compute.worker'), (213, 212, 'sleep 1'),
        (214, 211, 'python3 -m van_compute.worker'), (215, 214, 'sleep 2')),
    'nested sessions': (
        LAUNCHD, (200, 1, 'claude'), (201, 200, '/bin/zsh -c x'), (210, 201, 'claude -p y'),
        (211, 210, 'sleep 1'), (220, 211, 'codex exec z'), (221, 220, 'node a.js')),
    'worker inside a session with a nested worker': (
        LAUNCHD, (200, 1, 'claude'), (210, 200, 'python3 -m van_compute.worker'),
        (211, 210, SHELL_JOB), (212, 211, 'python3 -m van_compute.worker'),
        (213, 212, SHELL_JOB), (214, 213, 'sleep 1'),
        (215, 210, '/usr/bin/ssh -N -M -S /c/s pi@x'), (216, 215, 'ssh-helper')),
    'round 3 and 4 codex shapes': (
        LAUNCHD, (101, 1, '-zsh'), (200, 101, 'claude'), (201, 200, '/bin/zsh -c x'),
        (210, 201, '/x/codex app-server --listen x'), (211, 210, 'node w.js'),
        (220, 200, 'codex app-server tests are failing'), (221, 220, 'node v.js'),
        (230, 101, 'codex -c a=1 exec-server'), (231, 230, 'node c.js'),
        (240, 101, 'codex --no-alt-screen --ask-for-approval never'),
        (250, 1, 'python3 -Bmvan_compute.worker'), (251, 1, 'python3 -IBmllm_switchboard')),
    'gateway and worker inside codex': (
        LAUNCHD, (200, 1, 'codex exec work'), (210, 200, 'node /x/model-gateway.js serve'),
        (211, 210, '/x/claude-code-proxy serve'), (212, 200, 'python3 -Bmvan_compute.worker'),
        (213, 212, '/bin/sh job.sh'), (214, 200, 'grep model-gateway.js'),
        (215, 200, 'codex app-server x'), (216, 215, 'node y.js')),
}


def check_invariants(test, table):
    grouping = build_grouping(table)
    seen = {}
    for gid, group in grouping.groups.items():
        for member in group.members:
            pid = member.row.pid
            test.assertNotIn(pid, seen, f'{pid} listed twice: {seen.get(pid)} and {gid}')
            seen[pid] = gid
    test.assertEqual(seen, grouping.membership)
    for gid, group in grouping.groups.items():
        test.assertEqual(grouping.membership[group.root.pid], gid)
    kind_totals = {kind: [0.0, 0, 0] for kind in TOTAL_KINDS}
    group_sums = {kind: [0.0, 0, 0] for kind in TOTAL_KINDS}
    for group in grouping.groups.values():
        sums = (sum(m.row.cpu for m in group.members),
                sum(m.row.rss_bytes for m in group.members), len(group.members))
        for index, value in enumerate(sums):
            if group.kind in group_sums:
                group_sums[group.kind][index] += value
    for pid, gid in grouping.membership.items():
        kind = grouping.groups[gid].kind
        if kind in kind_totals:
            row = table.rows[pid]
            kind_totals[kind][0] += row.cpu
            kind_totals[kind][1] += row.rss_bytes
            kind_totals[kind][2] += 1
    for kind in TOTAL_KINDS:
        test.assertAlmostEqual(kind_totals[kind][0], group_sums[kind][0])
        test.assertEqual(kind_totals[kind][1:], group_sums[kind][1:])
    return grouping


class GroupInvariantTests(unittest.TestCase):
    def test_every_fixture_keeps_members_and_membership_in_agreement(self):
        for name, specs in FIXTURES.items():
            with self.subTest(name):
                check_invariants(self, make_table(*specs))

    def test_a_checker_naming_the_worker_beneath_a_job_stays_in_that_job(self):
        grouping = build_grouping(make_table(*FIXTURES['checker under a job (round 5)']))
        for pid in (211, 212, 213):
            self.assertEqual(grouping.membership[pid], 'compute:211', pid)
        self.assertEqual([m.row.pid for m in grouping.groups['compute:211'].members],
                         [211, 212, 213])
        self.assertNotIn('compute:212', grouping.groups)


if __name__ == '__main__':
    unittest.main()
