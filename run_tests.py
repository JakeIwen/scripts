#!/usr/bin/env python3
"""Run the repository's automated suites, stopping at the first failed command."""
from pathlib import Path
import os
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
FRONTEND = Path('pi/apps/van_dashboard/frontend')
# Test-file prefix -> import roots its tests need, in search order.
PYTHON_IMPORT_ROOTS = {
    'macbook/bettertouchtool/tests': ('macbook/bettertouchtool',),
    'macbook/mac_panel/tests': ('macbook/mac_panel', 'shared/python'),
    'macbook/tests': ('shared/python', 'macbook/bettertouchtool'),
    'pi/tests': ('pi',),
    'pi/tests/backup': ('pi/scripts/backup',),
    'pi/tests/price_check': ('pi/scripts/price_check',),
    'pi/projects/phone-drop': ('pi/projects/phone-drop',),
    'shared/tests': ('shared/python',),
}


def test_files():
    """Include new worktree tests, but exclude ignored data and deleted files."""
    result = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT, check=True, capture_output=True)
    paths = {Path(os.fsdecode(name)) for name in result.stdout.split(b'\0') if name}
    return sorted(path for path in paths if (ROOT / path).is_file()
                  and (path.name.startswith('test_') or '.test.' in path.name))


def suite_commands(paths):
    plugins = set()
    frontend = False
    for path in paths:
        if path.suffix == '.py':
            # Independent projects import similarly named modules (including
            # other checkouts); a fresh interpreter prevents namespace leakage.
            module = '.'.join(path.with_suffix('').parts)
            yield str(path), [sys.executable, '-B', '-m', 'unittest', '-f', module], ROOT / path.parent
        elif path.suffix in {'.cjs', '.mjs'}:
            yield str(path), ['node', '--test', str(path)], ROOT
        elif path.suffix in {'.sh', '.zsh'}:
            interpreter = 'zsh' if path.suffix == '.zsh' else 'bash'
            yield str(path), [interpreter, str(path)], ROOT
        elif path.suffix in {'.ts', '.tsx'}:
            plugin = next((parent for parent in path.parents
                           if (ROOT / parent / '.claude-plugin/plugin.json').is_file()), None)
            if FRONTEND in path.parents:
                frontend = True
            elif plugin is not None:
                plugins.add(plugin)
            else:
                raise ValueError('No suite configured for ' + str(path))
        else:
            raise ValueError('No test runner configured for ' + str(path))
    for plugin in sorted(plugins):
        yield str(plugin), ['claude', 'plugin', 'test', str(plugin)], ROOT
    if frontend:
        yield str(FRONTEND), ['npm', 'test', '--', '--bail=1'], ROOT / FRONTEND


def main():
    paths = test_files()
    if not paths:
        raise RuntimeError('No tests discovered; refusing an empty success.')
    commands = list(suite_commands(paths))
    print(f'Discovered {len(paths)} test files; running {len(commands)} commands.', flush=True)
    for name, command, directory in commands:
        if not shutil.which(command[0]):
            print(f'ERROR: {name} requires {command[0]}.', file=sys.stderr)
            return 1
        print(f'\nRUN {name}', flush=True)
        # Standalone scripts have their own import roots; keep this contract in
        # one runner instead of adding sys.path workarounds to individual tests.
        environment = dict(os.environ)
        roots = [str(ROOT)] + [str(ROOT / directory) for prefix, directories in PYTHON_IMPORT_ROOTS.items()
                              if name.startswith(prefix + '/') for directory in directories]
        environment['PYTHONPATH'] = os.pathsep.join(roots + [environment.get('PYTHONPATH', '')])
        result = subprocess.run(command, cwd=directory, env=environment)
        if result.returncode:
            print(f'FAILED: {name} (exit {result.returncode}); remaining suites were not run.',
                  file=sys.stderr)
            return 1
    print(f'PASS: all {len(commands)} suite commands completed.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
