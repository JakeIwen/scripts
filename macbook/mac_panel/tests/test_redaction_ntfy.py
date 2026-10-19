import json
from pathlib import Path
import tempfile
import unittest

from log_redaction import log_tail, redact, redact_data
from ntfy_adapter import NtfyAdapter


class RedactionTests(unittest.TestCase):
    def test_urls_credentials_and_assignments(self):
        text = ('https://username:password@example.org/topic Authorization: Bearer topsecret\n'
                'Bearer secondsecret token=thirdsecret password="fourth secret" secret: fifthsecret\n'
                'NTFY_WARNING_URL=https://host/secret-topic api_key=sixthsecret')
        result = redact(text)
        for secret in ('username', 'topsecret', 'secondsecret', 'thirdsecret', 'fourth secret',
                       'fifthsecret', 'host/', 'secret-topic', 'sixthsecret'):
            self.assertNotIn(secret, result)
        self.assertNotIn('topsecret', json.dumps(redact_data({'nested': ['token=topsecret']})))

    def test_bounded_log_tail_and_forbidden_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'log'
            path.write_text(('old line\n' * 20000) + 'new line\npassword=do-not-show\n')
            result = log_tail(str(path))
            self.assertLessEqual(len(result['text']), 65536)
            self.assertLessEqual(len(result['text'].splitlines()), 30)
            self.assertIn('new line', result['text'])
            self.assertNotIn('do-not-show', result['text'])
            secret = root / 'pi/secrets/value'
            secret.parent.mkdir(parents=True)
            secret.write_text('secret file contents')
            alias = root / 'alias'
            alias.symlink_to(secret)
            self.assertEqual(log_tail(str(alias))['text'], '')
            self.assertIn('not permitted', log_tail(str(alias))['error'])
            self.assertTrue(log_tail(str(root))['error'])


class NtfyTests(unittest.TestCase):
    def test_only_routing_metadata_never_settings_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude, codex = root / 'settings.json', root / 'config.toml'
            secret = 'https://private-host.invalid/unique-secret-topic'
            claude.write_text(json.dumps({'env': {'NTFY_WARNING_URL': secret, 'SOMETHING': 'sensitive-plain-value'},
                'hooks': {'Notification': [{'matcher': 'permission_prompt', 'hooks': [
                    {'type': 'command', 'command': '/usr/bin/python3 /fixture/claude_ntfy_notify.py --hook'},
                    {'type': 'command', 'command': '/fixture/not-an-alert'}]}]}}))
            codex.write_text('# notify = ["/fixture/commented-notifier"]\n')
            adapter = NtfyAdapter(claude, codex)
            value = adapter.read()
            rendered = json.dumps(value)
            self.assertNotIn(secret, rendered)
            self.assertNotIn('sensitive-plain-value', rendered)
            self.assertNotIn('NTFY_WARNING_URL', rendered)
            self.assertEqual(value['alerts'][0]['event'], 'Notification')
            self.assertEqual(value['alerts'][0]['matcher'], 'permission_prompt')
            self.assertEqual(value['alerts'][1]['event'], 'Off')
            codex.write_text('notify = ["/usr/bin/python3", "/fixture/codex_notify.py"]\n')
            value = adapter.read()
            self.assertEqual(value['alerts'][1]['script'], '/fixture/codex_notify.py')
            self.assertEqual(value['errors'], [])


if __name__ == '__main__':
    unittest.main()
