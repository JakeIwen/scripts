"""Exercise the GUI launcher environment using a fake Sonos module; no playback."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/sns.sh'
ACTIVATE = Path('/Users/jacobr/py3env/bin/activate')


@unittest.skipUnless(ACTIVATE.is_file(), 'requires the Mac Sonos virtualenv')
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
        self.module.write_text('''import json, sys
def pink_noise():
    print(json.dumps({"task": "pink_noise", "module": __file__, "path": sys.path}))
def record(*args):
    print(json.dumps({"args": args}))
def fail():
    raise RuntimeError("simulated Sonos failure")
''', encoding='utf-8')

    def run_wrapper(self, *args, pythonpath=None):
        # Deliberately no PYTHONPATH, login shell, or Sonos access. The only
        # sonos_tasks import possible must resolve to our fake checkout module.
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1'}
        if pythonpath is not None:
            env['PYTHONPATH'] = pythonpath
        return subprocess.run(['/bin/zsh','-c','"$@"','sns-test',str(self.script),*args],
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
        result = self.result(self.run_wrapper('record','vonRear','left right','40'))
        self.assertEqual(result['args'],['vonRear','left right','40'])

    def test_python_failures_visible_in_stdout_for_btt(self):
        proc = self.run_wrapper('fail')
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('simulated Sonos failure',proc.stdout)
        self.assertIn('Traceback',proc.stdout)


if __name__ == '__main__':
    unittest.main()
