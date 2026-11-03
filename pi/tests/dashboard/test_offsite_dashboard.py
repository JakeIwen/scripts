import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from pi.apps.van_dashboard import van_dashboard_offsite as offsite
from pi.apps.van_dashboard.routes import backups

RUNNING = {
    'schema': 1, 'result': 'running', 'label': 'FORT-MPLS-5TB', 'source': 'exfat512',
    'bytes_done': 10, 'bytes_total': 100, 'percent': 10.0, 'rate': 5.5, 'eta': 90.0,
    'started': 1000.0, 'updated': 1010.0, 'target_kind': 'local', 'cleanup_errors': [],
    'sources': {'exfat512': {'result': 'running'}},
}
ENTRY = {'result': 'ok', 'snapshot': 'abcd1234', 'logical_bytes_processed': 5,
         'newly_added_packed_bytes': None, 'duration_seconds': 3.5, 'finished': 99.0,
         'target_kind': 'local'}


def done(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, '')


def script(**handlers):
    """A fake run_command keyed by the script's first argument."""
    def command(args, timeout=20, input_text=None):
        handler = handlers[args[3]]
        if isinstance(handler, Exception):
            raise handler
        return handler
    return mock.Mock(side_effect=command)


def dashboard_output(**changes):
    return json.dumps({'schema': 1, 'status': RUNNING, 'history': {
        'FORT-MPLS-5TB': {'exfat512': {'latest': ENTRY, 'last_ok': ENTRY}}},
        'evidence': {'check': None, 'restore_test': None}, **changes})


class ReadOffsiteTests(unittest.TestCase):
    def test_dashboard_command_returns_validated_sections(self):
        command = script(dashboard=done(dashboard_output()))
        data = offsite.read_offsite(command)
        self.assertEqual(command.call_args.args[0][1:], ['-n', offsite.SCRIPT, 'dashboard'])
        self.assertEqual(data['supported'], {'history': True})
        self.assertEqual(data['status']['source'], 'exfat512')
        self.assertEqual(data['history']['FORT-MPLS-5TB']['exfat512']['last_ok']['snapshot'], 'abcd1234')
        self.assertEqual(data['evidence'], {'check': None, 'restore_test': None})

    def test_unknown_fields_are_not_passed_through(self):
        noisy = dict(RUNNING, secret='x', sources={'borg': {'result': 'ok', 'path': '/etc'}})
        data = offsite.read_offsite(script(dashboard=done(dashboard_output(status=noisy))))
        self.assertNotIn('secret', data['status'])
        self.assertEqual(data['status']['sources']['borg'], {'result': 'ok'})

    def test_v1_script_falls_back_to_status_without_history(self):
        v1 = {'result': 'ok', 'label': 'FORT-MPLS-5TB', 'sources': {'borg': {'result': 'ok'}}}
        command = script(dashboard=done('', 2), status=done(json.dumps(v1)))
        data = offsite.read_offsite(command)
        self.assertEqual(data['supported'], {'history': False})
        self.assertEqual(data['history'], {})
        self.assertEqual(data['status']['target_kind'], 'local')

    def test_not_run_status_is_accepted(self):
        data = offsite.read_offsite(script(dashboard=done('', 2), status=done('{"result": "not-run"}')))
        self.assertEqual(data['status']['result'], 'not-run')

    def test_failures_raise_offsite_error(self):
        bad = ['nope', '[]', json.dumps({'status': {'result': 'weird'}}),
               json.dumps({'status': dict(RUNNING, bytes_done=-1)}),
               json.dumps({'status': dict(RUNNING, sources={'../x': {'result': 'ok'}})}),
               json.dumps({'status': RUNNING, 'history': {'a b': {}}}),
               json.dumps({'status': RUNNING, 'history': {'L': {'borg': {'latest': {'result': 'ok'}, 'last_ok': {'result': 1}}}}})]
        for stdout in bad:
            with self.assertRaises(offsite.OffsiteError, msg=stdout):
                offsite.read_offsite(script(dashboard=done(stdout)))
        with self.assertRaises(offsite.OffsiteError):
            offsite.read_offsite(script(dashboard=done('', 2), status=done('', 1)))
        with self.assertRaises(offsite.OffsiteError):
            offsite.read_offsite(script(dashboard=subprocess.TimeoutExpired('x', 8)))
        with self.assertRaises(offsite.OffsiteError):
            offsite.read_offsite(script(dashboard=OSError('no sudo')))


class RatesTests(unittest.TestCase):
    def test_samples_are_validated(self):
        command = script(rates=done(json.dumps({'schema': 1, 'source': 'borg', 'samples': [[1, 2]]})))
        self.assertEqual(offsite.offsite_rates('borg', command), {'supported': True, 'samples': [[1, 2]]})
        self.assertEqual(command.call_args.args[0][1:], ['-n', offsite.SCRIPT, 'rates', 'borg'])
        for samples in ([[1, -2]], [[1.5, 2]], [[1, 2, 3]], 'x'):
            with self.assertRaises(offsite.OffsiteError):
                offsite.offsite_rates('borg', script(rates=done(json.dumps({'samples': samples}))))

    def test_invalid_source_slug_never_reaches_the_script(self):
        command = script()
        for source in ('../etc', 'A', '', 'x' * 40, 'a b'):
            with self.assertRaises(ValueError):
                offsite.offsite_rates(source, command)
        command.assert_not_called()

    def test_v1_script_reports_unsupported(self):
        command = script(rates=done('', 2), dashboard=done('', 2))
        self.assertEqual(offsite.offsite_rates('borg', command), {'supported': False, 'samples': []})

    def test_unknown_source_on_a_v2_script_is_a_bad_request(self):
        command = script(rates=done('', 1), dashboard=done(dashboard_output()))
        with self.assertRaises(ValueError):
            offsite.offsite_rates('nothing', command)


class TargetDriveTests(unittest.TestCase):
    stats = SimpleNamespace(f_blocks=1000, f_bfree=250, f_frsize=4096)

    def test_mounted_drive_reports_usage_from_its_mountpoint(self):
        statvfs = mock.Mock(return_value=self.stats)
        drive = offsite.target_drive('X14', ['X14'], ismount=lambda p: True, statvfs=statvfs)
        self.assertEqual(drive, {'label': 'X14', 'mounted': True,
                                 'used_bytes': 750 * 4096, 'total_bytes': 1000 * 4096})
        statvfs.assert_called_once_with(os.path.join('/mnt', 'X14'))

    def test_unmounted_drive_is_never_measured(self):
        statvfs = mock.Mock()
        drive = offsite.target_drive('X14', ['X14'], ismount=lambda p: False, statvfs=statvfs)
        self.assertEqual(drive['mounted'], False)
        statvfs.assert_not_called()

    def test_unknown_label_has_no_drive(self):
        self.assertIsNone(offsite.target_drive('Other', ['X14']))
        self.assertIsNone(offsite.target_drive(None, ['X14']))


class OffsiteRouteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(backups.bp)
        self.client = app.test_client()
        self.disks = mock.Mock()
        self.disks.labels.return_value = ['FORT-MPLS-5TB']
        patcher = mock.patch.object(backups, 'disk_manager', self.disks)
        patcher.start()
        self.addCleanup(patcher.stop)

    def payload(self, **status):
        record = {'result': 'running', 'label': 'FORT-MPLS-5TB', 'target_kind': 'local'}
        return {'supported': {'history': True}, 'status': dict(record, **status), 'history': {},
                'evidence': {'check': None, 'restore_test': None}}

    def test_local_target_adds_the_drive_and_disables_caching(self):
        drive = {'label': 'FORT-MPLS-5TB', 'mounted': True, 'used_bytes': 1, 'total_bytes': 2}
        with mock.patch.object(backups, 'read_offsite', return_value=self.payload()), \
                mock.patch.object(backups, 'target_drive', return_value=drive) as probe:
            response = self.client.get('/api/backups/offsite')
        body = response.get_json()
        self.assertEqual((body['ok'], body['target_drive']), (True, drive))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        probe.assert_called_once_with('FORT-MPLS-5TB', ['FORT-MPLS-5TB'])

    def test_remote_target_skips_the_drive_probe(self):
        with mock.patch.object(backups, 'read_offsite', return_value=self.payload(target_kind='remote')), \
                mock.patch.object(backups, 'target_drive') as probe:
            body = self.client.get('/api/backups/offsite').get_json()
        self.assertIsNone(body['target_drive'])
        probe.assert_not_called()

    def test_errors_become_responses_without_leaking_detail(self):
        with mock.patch.object(backups, 'read_offsite', side_effect=offsite.OffsiteError('private')):
            response = self.client.get('/api/backups/offsite')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/backups/offsite?x=1').status_code, 400)

    def test_rates_route_maps_each_outcome(self):
        with mock.patch.object(backups, 'offsite_rates', return_value={'supported': True, 'samples': [[1, 2]]}):
            response = self.client.get('/api/backups/offsite/borg/rates')
        self.assertEqual(response.get_json(), {'ok': True, 'supported': True, 'samples': [[1, 2]]})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with mock.patch.object(backups, 'offsite_rates', return_value={'supported': False, 'samples': []}):
            self.assertEqual(self.client.get('/api/backups/offsite/borg/rates').get_json()['supported'], False)
        self.assertEqual(self.client.get('/api/backups/offsite/BAD!/rates').status_code, 400)
        with mock.patch.object(backups, 'offsite_rates', side_effect=offsite.OffsiteError('x')):
            self.assertEqual(self.client.get('/api/backups/offsite/borg/rates').status_code, 503)


if __name__ == '__main__':
    unittest.main()
