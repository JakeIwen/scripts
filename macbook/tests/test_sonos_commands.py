"""BTT Sonos command repair is pure text; no BTT or speaker actions run."""
import shlex
import tempfile
import unittest
from pathlib import Path

from sonos_commands import rewrite_sonos_command


class SonosCommandRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.launcher = self.root / 'macbook/scripts/sns.sh'
        self.launcher.parent.mkdir(parents=True)
        self.launcher.touch()

    def rewritten(self, text):
        value, changes = rewrite_sonos_command(text, self.root)
        self.assertTrue(changes)
        return shlex.split(value)

    def test_legacy_source_and_pi_launchers_become_direct_mac_invocations(self):
        for prefix in ('. ', 'source ', 'sh ', '/bin/bash ', ''):
            for path in ('sh/sns.sh', 'pi/sns.sh', 'macbook/scripts/sns.sh'):
                with self.subTest(prefix=prefix, path=path):
                    text = prefix + '~/dev/scripts/' + path + ' rear_normal'
                    self.assertEqual(self.rewritten(text), [str(self.launcher), 'rear_normal'])

    def test_exact_noise_typo_is_fixed_in_caller_not_runtime(self):
        for color in ('pink', 'brown'):
            self.assertEqual(self.rewritten('sns ' + color + ' noise'),
                             [str(self.launcher), color + '_noise'])

    def test_retired_volume_name_and_speaker_typo(self):
        self.assertEqual(self.rewritten('sns sonos_vol_50_all'),
                         [str(self.launcher), 'vol_eql_all', '50'])
        self.assertEqual(self.rewritten('sns adjust_volume vanFront up 5'),
                         [str(self.launcher), 'adjust_volume', 'vonFront', 'up', '5'])

    def test_remote_intent_is_retained_without_remote_alias_dependency(self):
        self.assertEqual(self.rewritten("ssh pi@vanpi.local 'sns partymode'"),
                         [str(self.launcher), '--pi', 'partymode'])

    def test_quoted_arguments_and_negative_seek_remain_literal(self):
        args = ['play_from_faves', "A name with ' and $(touch nope)", 'False']
        self.assertEqual(self.rewritten(shlex.join(['sns', *args])),
                         [str(self.launcher), *args])
        self.assertEqual(self.rewritten('sns scrub "-45"'),
                         [str(self.launcher), 'scrub', '-45'])

    def test_idempotent_and_unrelated_commands_untouched(self):
        for command in (shlex.join([str(self.launcher), 'pink_noise']),
                        '/another/repo/sns.sh pink_noise', 'echo sns group down'):
            self.assertEqual(rewrite_sonos_command(command, self.root), (command, []))

    def test_compound_program_is_not_reinterpreted(self):
        with self.assertRaisesRegex(RuntimeError, 'Compound'):
            rewrite_sonos_command('sns pink_noise && echo done', self.root)
