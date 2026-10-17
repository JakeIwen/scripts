"""Exercise rotation with real timestamps and shell dispatch, never actual disks."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from hotspare_rotation import evidence_path, mark_clone, rotation_plan


LABELS = ('hotspare-a', 'hotspare-b')
DAY = 86400
SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts' / 'backup'


class RotationFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = int(time.time())
        self.stamp(LABELS[0], 3)
        self.stamp(LABELS[1], 6)

    def stamp(self, label, days):
        path = evidence_path(self.root, label)
        path.write_text('success\n')
        when = self.now - days * DAY
        os.utime(path, (when, when))

    def plan(self, interval=7):
        return rotation_plan(self.root, LABELS, interval, self.now)


class RotationTests(RotationFixture):
    def test_normal_rotation_waits_from_latest_success_and_selects_older(self):
        plan = self.plan()
        self.assertEqual(plan.target, LABELS[1])
        self.assertEqual(plan.next_at, self.now + 4 * DAY)
        self.assertEqual(self.plan(10).next_at, self.now + 7 * DAY)

    def test_missed_runs_refresh_one_then_wait_full_interval(self):
        self.stamp(LABELS[0], 100)
        self.stamp(LABELS[1], 110)
        self.assertEqual(self.plan().target, LABELS[1])
        self.stamp(LABELS[1], 0)
        plan = self.plan()
        self.assertEqual(plan.target, LABELS[0])
        self.assertEqual(plan.next_at, self.now + 7 * DAY)

    def test_interrupted_manual_refresh_retries_that_card_not_the_older_peer(self):
        mark_clone(self.root, LABELS[0], complete=False)
        plan = self.plan()
        self.assertEqual(plan.target, LABELS[0])
        self.assertEqual(plan.next_at, self.now)
        self.assertEqual(plan.incomplete, (LABELS[0],))
        # Re-reading after a process restart must retain the same target.
        self.assertEqual(self.plan(), plan)
        self.stamp(LABELS[0], 0)
        mark_clone(self.root, LABELS[0], complete=True)
        self.assertEqual(self.plan().target, LABELS[1])
        self.assertEqual(self.plan().next_at, self.now + 7 * DAY)

    def test_two_incomplete_cards_or_no_success_history_need_manual_recovery(self):
        for label in LABELS:
            mark_clone(self.root, label, complete=False)
        self.assertIsNone(self.plan().target)
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(rotation_plan(empty, LABELS, 7, self.now).target)

    def test_missing_success_stamp_selects_only_that_card_after_spacing(self):
        evidence_path(self.root, LABELS[0]).unlink()
        self.assertEqual(self.plan().target, LABELS[0])
        self.assertEqual(self.plan().next_at, self.now + DAY)

    def test_future_or_symlink_history_refuses_rotation(self):
        self.stamp(LABELS[0], -1)
        self.assertIsNone(self.plan().target)
        evidence_path(self.root, LABELS[0]).unlink()
        evidence_path(self.root, LABELS[0]).symlink_to(evidence_path(self.root, LABELS[1]))
        self.assertIsNone(self.plan().target)

    def test_bad_labels_and_intervals_cannot_select_a_target(self):
        for labels, interval in ((LABELS, 0), (LABELS, 366), (LABELS[:1], 7),
                                 ((LABELS[0], LABELS[0]), 7)):
            with self.assertRaises(ValueError):
                rotation_plan(self.root, labels, interval, self.now)
        self.assertIsNone(rotation_plan(self.root, ('../bad', LABELS[1]), 7, self.now).target)


class RotationDispatchTests(RotationFixture):
    def setUp(self):
        super().setUp()
        self.stamp(LABELS[0], 21)
        self.stamp(LABELS[1], 28)
        self.env = {**os.environ, 'STAMP_DIR': str(self.root), 'CLONE_ROTATION_DAYS': '7',
                    'PYTHONPATH': str(SCRIPTS), 'HOTSPARE_ROTATION_PYTHON': sys.executable}
        self.fixture('POLICY', '''disk_policy_resolve_exact_label() {
  [[ ${TEST_DISCOVERY_ERROR:-} != 1 ]] || return 2
  [[ ${TEST_MISSING:-} != "$1" ]] || return 1
  DISK_POLICY_RESOLVED_DEVICE=/dev/$1
}''')
        self.fixture('LSBLK', '''#!/bin/bash
[[ ${TEST_LSBLK_ERROR:-} != 1 ]] || exit 1
if [[ $* == *NAME,MOUNTPOINTS* ]]; then
  [[ ${TEST_PARTITION_SCAN_ERROR:-} != 1 ]] || exit 1
  [[ ${TEST_EMPTY_PARTITION_SCAN:-} != 1 ]] || exit 0
  echo "${@: -1}"
  if [[ ${TEST_BOOT_MOUNTED:-} == "${@: -1}" ]]; then
    echo "${@: -1}1 /mnt/boot"
  fi
  exit 0
fi
[[ ${TEST_SAME_DISK:-} != 1 ]] || { echo same-disk; exit; }
part=${@: -1}
echo "${part##*/}-disk"
''')
        self.fixture('FINDMNT', '''#!/bin/bash
[[ ${TEST_MOUNT_ERROR:-} != 1 ]] || exit 2
[[ ${TEST_MOUNTED:-} != "${@: -1}" ]] || { echo mounted; exit; }
exit 1
''')
        self.fixture('CLONE', f'''#!{sys.executable}
import os, sys
from pathlib import Path
from hotspare_rotation import mark_clone, evidence_path
root = Path(os.environ['STAMP_DIR'])
label = sys.argv[1]
with (root/'calls').open('a') as out: out.write(label+'\\n')
mark_clone(root, label, complete=False)
if os.environ.get('TEST_CLONE_FAIL'): sys.exit(1)
evidence_path(root, label).write_text('success\\n')
mark_clone(root, label, complete=True)
''')

    def fixture(self, name, contents):
        path = self.root / name.lower()
        path.write_text(contents)
        path.chmod(0o700)
        self.env['HOTSPARE_ROTATION_' + name] = str(path)

    def run_rotation(self, **environment):
        return subprocess.run(['bash', '-c', '''
source "$1"
CLONE_TARGETS=(hotspare-a:14 hotspare-b:14)
log() { echo "$*"; }
run() { "$@"; }
backup_progress_update() { :; }
run_hotspare_rotation
''', 'test', str(SCRIPTS / 'hotspare_rotation.sh')],
            env={**self.env, **environment}, capture_output=True, text=True)

    def calls(self):
        path = self.root / 'calls'
        return path.read_text().splitlines() if path.exists() else []

    def test_dispatch_never_catches_up_both_overdue_cards(self):
        self.assertEqual(self.run_rotation().returncode, 0)
        self.assertEqual(self.calls(), [LABELS[1]])
        self.assertEqual(self.run_rotation().returncode, 3)
        self.assertEqual(self.calls(), [LABELS[1]])

    def test_missing_either_card_never_substitutes_the_remaining_card(self):
        for label in LABELS:
            self.assertNotEqual(self.run_rotation(TEST_MISSING=label).returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_rotation().returncode, 0)
        self.assertEqual(self.calls(), [LABELS[1]])

    def test_failed_copy_retries_itself_and_does_not_advance_the_rotation(self):
        original = evidence_path(self.root, LABELS[0]).stat().st_mtime
        self.assertEqual(self.run_rotation(TEST_CLONE_FAIL='1').returncode, 1)
        self.assertEqual(self.run_rotation().returncode, 0)
        self.assertEqual(self.calls(), [LABELS[1], LABELS[1]])
        self.assertEqual(evidence_path(self.root, LABELS[0]).stat().st_mtime, original)

    def test_discovery_errors_mounted_peer_and_duplicate_disk_all_refuse_writes(self):
        cases = [{'TEST_DISCOVERY_ERROR':'1'}, {'TEST_MOUNT_ERROR':'1'},
                 {'TEST_LSBLK_ERROR':'1'}, {'TEST_SAME_DISK':'1'},
                 {'TEST_MOUNTED':'/dev/'+LABELS[0]},
                 {'TEST_BOOT_MOUNTED':'/dev/'+LABELS[0]+'-disk'},
                 {'TEST_PARTITION_SCAN_ERROR':'1'}, {'TEST_EMPTY_PARTITION_SCAN':'1'}]
        for case in cases:
            self.assertNotEqual(self.run_rotation(**case).returncode, 0, case)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
