import json
import subprocess
import unittest
from unittest import mock

from flask import Flask

from pi.apps.van_dashboard import cloud_backup_details as details
from pi.apps.van_dashboard.routes import backups


ATTEMPT = 'a' * 32


class CloudBackupDetailsTests(unittest.TestCase):
    def setUp(self):
        self.attempt = {'id': ATTEMPT, 'started_at': 1000, 'ended_at': 1100,
                        'phase': 'error', 'work_phase': 'preparing',
                        'message': 'Attempt failed; see the private service log for details.'}
        self.loader = mock.Mock(return_value={
            key: {'available': True, 'attempts': [self.attempt]} for key, _ in details.JOBS.values()})
        self.command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, ''))

    def journal(self, rows):
        self.command.return_value.stdout = '\n'.join(json.dumps({
            '__REALTIME_TIMESTAMP': str(int(at * 1_000_000)), 'MESSAGE': message,
        }) for at, message in rows)

    def test_selected_attempt_has_real_reason_without_neighbor_or_private_lines(self):
        self.journal([(999, 'Failed: OSError'),
                      (1001, 'provider token=PRIVATE password=PRIVATE'),
                      (1099, 'Failed: unsafe staging object; cleanup refused'),
                      (1101, 'Failed: insufficient staging disk headroom')])
        result = details.attempt_details('time-machine', ATTEMPT, self.loader, self.command)
        self.assertEqual(len(result['entries']), 1)
        self.assertIn('cleanup refused', result['entries'][0]['message'])
        self.assertIn('protect backup data', result['entries'][0]['explanation'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertIsNone(result['note'])
        args = self.command.call_args.args[0]
        self.assertIn('--unit=vanpi-time-machine-icloud.service', args)
        self.assertIn('--since=@1000.000000', args)
        self.assertIn('--until=@1100.000000', args)
        self.assertIn('--lines=200', args)
        self.assertEqual(self.command.call_args.kwargs['timeout'], 5)

    def test_pi_command_failure_preserves_only_fixed_program_operation_and_exit_code(self):
        self.journal([(1001, 'Failed: rclone copy failed (exit 1)'),
                      (1002, 'Failed: unsafe staging object; cleanup refused password=PRIVATE'),
                      (1003, 'Failed: rclone copy failed (exit 1) token=PRIVATE')])
        result = details.attempt_details('pi', 'latest', self.loader, self.command)
        self.assertEqual([r['message'] for r in result['entries']], ['Failed: rclone copy failed (exit 1)'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertIn('--unit=vanpi-icloud-backup.service', self.command.call_args.args[0])

    def test_missing_unknown_and_unreadable_logs_are_explicit_and_keep_saved_attempt(self):
        for output in ('', json.dumps({'MESSAGE': 'Failed: PRIVATE', '__REALTIME_TIMESTAMP': '1001000000'}), '{broken'):
            self.command.return_value.stdout = output
            result = details.attempt_details('pi', ATTEMPT, self.loader, self.command)
            self.assertEqual(result['entries'], [])
            self.assertTrue(result['note'])
            self.assertEqual(result['attempt'], self.attempt)
            self.assertNotIn('PRIVATE', json.dumps(result))
        self.command.side_effect = subprocess.TimeoutExpired('journalctl', 5, stderr='PRIVATE')
        result = details.attempt_details('pi', ATTEMPT, self.loader, self.command)
        self.assertIn('unavailable', result['note'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_authentication_text_is_not_returned(self):
        self.journal([(1001, 'Authentication required: user@example.com password=PRIVATE')])
        result = details.attempt_details('pi', ATTEMPT, self.loader, self.command)
        self.assertEqual(result['entries'][0]['message'], 'Authentication required')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('example.com', json.dumps(result))

    def test_rejects_unselected_job_and_invalid_ids_before_reading_anything(self):
        for kind, attempt in [('sshd', ATTEMPT), ('pi', '--unit=sshd'), ('pi', '../state.json')]:
            with self.assertRaises(ValueError):
                details.attempt_details(kind, attempt, self.loader, self.command)
        self.loader.assert_not_called()
        self.command.assert_not_called()
        with self.assertRaises(LookupError):
            details.attempt_details('pi', 'b' * 32, self.loader, self.command)
        self.command.assert_not_called()

    def test_unfinished_historical_attempt_cannot_include_the_next_worker(self):
        self.attempt['ended_at'] = None
        self.loader.return_value['icloud']['attempts'].insert(0, {
            **self.attempt, 'id': 'b' * 32, 'started_at': 1100})
        details.attempt_details('pi', ATTEMPT, self.loader, self.command)
        self.assertIn('--until=@1100.000000', self.command.call_args.args[0])

    def test_route_is_read_only_and_reports_missing_attempts(self):
        app = Flask(__name__)
        app.register_blueprint(backups.bp)
        client = app.test_client()
        service = mock.Mock(status=self.loader)
        with mock.patch.object(backups, 'backups', service), \
                mock.patch.object(backups, 'attempt_details', side_effect=lambda *args:
                                  details.attempt_details(*args, command=self.command)):
            path = '/api/backups/cloud/pi/attempts/' + ATTEMPT
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['details']['attempt']['id'], ATTEMPT)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(client.get(path + '?unit=sshd').status_code, 400)
            self.assertEqual(client.get('/api/backups/cloud/sshd/attempts/latest').status_code, 400)
            self.assertEqual(client.get('/api/backups/cloud/pi/attempts/' + 'b' * 32).status_code, 404)
            self.assertEqual(client.post(path).status_code, 405)
            self.loader.side_effect = RuntimeError('PRIVATE')
            response = client.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('PRIVATE', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
