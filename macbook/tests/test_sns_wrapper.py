"""Exercise the GUI launcher environment using a fake Sonos module; no playback."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/sns.sh'


class SonosWrapperTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sns-wrapper-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/'checkout with spaces'
        self.script = self.root/'macbook/scripts/sns.sh'
        self.script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, self.script)
        self.module = self.root/'shared/python/sonos_tasks.py'
        self.module.parent.mkdir(parents=True)
        for name in ('sonos_cli.py', 'sonos_cli_commands.py'):
            shutil.copy2(SCRIPT.parents[2]/'shared/python'/name, self.module.parent)
        self.module.write_text('''import json, sys
def pink_noise():
    print(json.dumps({"task": "pink_noise", "module": __file__, "path": sys.path}))
def play_soundbyte(*args):
    print(json.dumps({"args": args}))
def stop():
    raise RuntimeError("simulated Sonos failure")
''', encoding='utf-8')

    def run_wrapper(self, *args, pythonpath=None):
        # Deliberately no PYTHONPATH, login shell, or Sonos access. The only
        # sonos_tasks import possible must resolve to our fake checkout module.
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1', 'SNS_PYTHON': sys.executable}
        if pythonpath is not None:
            env['PYTHONPATH'] = pythonpath
        return subprocess.run([str(self.script), *args],
                              cwd='/',env=env,capture_output=True,text=True,timeout=10)

    def result(self, proc):
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        return json.loads(next(line for line in proc.stdout.splitlines() if line.startswith('{')))

    def test_btt_style_clean_environment_finds_module_without_login_shell(self):
        result = self.result(self.run_wrapper('pink_noise'))
        self.assertEqual(result['task'],'pink_noise')
        self.assertEqual(result['module'],str(self.module))

    def test_current_checkout_precedes_stale_inherited_pythonpath(self):
        stale = self.root/'stale'
        stale.mkdir()
        (stale/'sonos_tasks.py').write_text('raise RuntimeError("wrong module")\n')
        result = self.result(self.run_wrapper('pink_noise',pythonpath=str(stale)))
        self.assertEqual(result['module'],str(self.module))
        self.assertIn(str(stale),result['path'])
        self.assertLess(result['path'].index(str(self.module.parent)),result['path'].index(str(stale)))

    def test_existing_task_arguments_preserved(self):
        result = self.result(self.run_wrapper('play_soundbyte',"vonRear's $(false)",'left right; echo bad'))
        self.assertEqual(result['args'],["vonRear's $(false)",'left right; echo bad'])

    def test_spaced_noise_is_rejected_without_executing(self):
        proc = self.run_wrapper('pink', 'noise')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('pink_noise', proc.stdout)

    def test_help_and_invalid_input_never_import_task_module(self):
        self.module.write_text('raise RuntimeError("unexpected task import")\n')
        for arguments, code in ((('--help',), 0), (('pink',), 2),
                                (("pink_noise;exit",), 2), ((), 2)):
            with self.subTest(arguments=arguments):
                proc = self.run_wrapper(*arguments)
                self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
                self.assertNotIn('unexpected task import', proc.stdout + proc.stderr)

    def test_wrong_arity_does_not_run_task(self):
        proc = self.run_wrapper('pink_noise', 'extra')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('too many positional arguments', proc.stdout)

    def test_explicit_pi_preserves_arguments_through_remote_shell(self):
        binary = self.root / 'bin'
        binary.mkdir()
        ssh = binary / 'ssh'
        ssh.write_text('#!' + sys.executable + '\n' +
                       'import json, shlex, sys\nprint(json.dumps(shlex.split(sys.argv[-1])))\n')
        ssh.chmod(0o755)
        args = ['play_soundbyte', "a quote: ' ; $(touch nope)", 'speaker name']
        proc = subprocess.run(
            ['/bin/bash', str(self.script), '--pi', *args], cwd='/',
            env={'PATH': str(binary) + ':/usr/bin:/bin'}, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), ['/bin/bash', '/home/pi/sns.sh', *args])

    def test_python_failures_visible_in_stdout_for_btt(self):
        proc = self.run_wrapper('stop')
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('simulated Sonos failure',proc.stdout)
        self.assertNotIn('rpi', proc.stdout)
        self.assertNotIn('ssh', proc.stdout)


if __name__ == '__main__':
    unittest.main()
