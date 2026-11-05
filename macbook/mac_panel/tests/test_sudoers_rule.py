"""Sudoers generation is offline; these labels are an explicit installation fixture."""
import re
import subprocess
import unittest
from unittest.mock import patch

from panel_service import main
from sudoers_rule import ARGUMENT_PATTERN, installer, rule

DAEMON_LABELS = ('codexbar-gateway-usage', 'electronics-inventory', 'fieldwork',
                 'lionfeather-org', 'promaster-explorer', 'time-machine-offsite', 'visual-guides')


class SudoersTests(unittest.TestCase):
    def test_rule_allows_only_one_owned_daemon_restart(self):
        for suffix in DAEMON_LABELS:
            self.assertRegex('kickstart -k system/com.jacobr.' + suffix, ARGUMENT_PATTERN)
        for args in ('kickstart -k system/com.jacobr.fieldwork second',
                     'kickstart -k system/com.apple.x',
                     'kickstart -k system/com.jacobr.UPPER',
                     'kickstart -k system/com.jacobr.fieldwork\n'):
            self.assertIsNone(re.fullmatch(ARGUMENT_PATTERN, args))
        with patch('sudoers_rule.getpass.getuser', return_value='fixture'):
            self.assertEqual(rule(), 'fixture ALL=(root) NOPASSWD: /bin/launchctl ' + ARGUMENT_PATTERN)
        with self.assertRaises(ValueError):
            rule('user ALL=(ALL)')

    def test_installer_parses_without_executing_privileged_commands(self):
        script = installer('fixture')
        result = subprocess.run(('/bin/zsh', '-n'), input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(script.startswith('#!/bin/zsh\nset -eux -o pipefail\nsudo -v\n'))
        self.assertIn('sudo /usr/sbin/visudo -cf "$temporary"', script)
        self.assertIn('sudo /usr/bin/install -m 440 -o root -g wheel', script)
        self.assertIn('/etc/sudoers.d/mac-panel-daemons', script)

    def test_cli_print_and_script_do_not_construct_the_service_controller(self):
        with patch('panel_service.PanelService') as service, patch('builtins.print') as output:
            self.assertEqual(main(['sudoers', '--print']), 0)
            self.assertIn('NOPASSWD:', output.call_args.args[0])
            self.assertEqual(main(['sudoers', '--script']), 0)
            self.assertIn('visudo', output.call_args.args[0])
            service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
