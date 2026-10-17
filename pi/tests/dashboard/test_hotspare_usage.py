import subprocess
import unittest
from unittest import mock

from pi.apps.van_dashboard.van_dashboard_hotspares import build_hotspares, hotspare_used_bytes


class HotspareUsageTests(unittest.TestCase):
    def setUp(self):
        self.row = {'label': 'hotspare-a', 'fstype': 'ext4', 'size': 4096000, 'mountpoints': []}
        self.header = ('Filesystem volume name: hotspare-a\nFilesystem state: clean\n'
                       'Block count: 1000\nFree blocks: 250\nBlock size: 4096\n')
        self.command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, self.header))

    def test_reads_unmounted_allocation_by_label_without_mounting(self):
        self.assertEqual(hotspare_used_bytes(self.row, 'hotspare-a', self.command, 8), 3072000)
        self.assertEqual(self.command.call_args.args[0][2:], [
            '/usr/bin/env', 'LC_ALL=C', '/usr/sbin/dumpe2fs', '-h', '/dev/disk/by-label/hotspare-a'])
        self.assertEqual(self.command.call_args.kwargs['timeout'], 2)

    def test_mounted_volume_uses_live_fsused_instead_of_superblock(self):
        self.row.update(mountpoints=['/mnt/clone'], fsused=3000000)
        self.assertEqual(hotspare_used_bytes(self.row, 'hotspare-a', self.command, 8), 3000000)
        self.command.assert_not_called()

    def test_missing_dirty_mismatched_and_failed_probes_are_unknown(self):
        for header in ('', self.header.replace('hotspare-a', 'other'),
                       self.header.replace('clean', 'not clean'),
                       self.header.replace('250', '1001'), self.header.replace('4096', 'invalid')):
            self.command.return_value = subprocess.CompletedProcess([], 0, header)
            self.assertIsNone(hotspare_used_bytes(self.row, 'hotspare-a', self.command, 8))
        self.command.return_value = subprocess.CompletedProcess([], 1, self.header)
        self.assertIsNone(hotspare_used_bytes(self.row, 'hotspare-a', self.command, 8))
        self.command.side_effect = subprocess.TimeoutExpired('dumpe2fs', 2)
        self.assertIsNone(hotspare_used_bytes(self.row, 'hotspare-a', self.command, 8))

    def test_rotation_reports_waiting_for_missing_peer_and_only_one_due_card(self):
        configuration = {'targets': [{'label': label, 'interval_days':14}
                                     for label in ('hotspare-a','hotspare-b')],
                         'clone_stale_factor':2,
                         'clone_rotation': {'target':'hotspare-b','next_at':900,
                                            'message':None,'incomplete':[]}}
        a = dict(self.row, path='/dev/sda2')
        b = dict(self.row, label='hotspare-b', path='/dev/sdb2')
        args = ('/unused', lambda _: 100, 1000, lambda _: [], self.command, 8)
        missing = build_hotspares(configuration, [a], *args)
        self.assertTrue(all(not card['due'] for card in missing))
        self.assertEqual(missing[0]['rotation_message'], 'Rotation waiting for both cards')
        present = build_hotspares(configuration, [a,b], *args)
        self.assertEqual([card['label'] for card in present if card['due']], ['hotspare-b'])
        configuration['clone_rotation']['next_at'] = 2000
        self.assertTrue(all(not card['due'] for card in build_hotspares(configuration, [a,b], *args)))
        configuration['clone_rotation']['incomplete'] = ['hotspare-b']
        interrupted = build_hotspares(configuration, [a,b], *args)[1]
        self.assertTrue(interrupted['stale'])
        self.assertEqual(interrupted['rotation_message'], 'Incomplete · retry scheduled')

    def test_ambiguous_labels_are_never_probed(self):
        result = build_hotspares({'targets': [{'label': 'hotspare-a', 'interval_days': 7}],
                                 'clone_stale_factor': 2}, [self.row, dict(self.row)],
                                '/unused', lambda _: None, 1000, lambda _: [], self.command, 8)
        self.assertIsNone(result[0]['used_bytes'])
        self.command.assert_not_called()
