"""Offline hook boundary: an indeterminate operand never reaches even the first public probe."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import dep_guard_sources
from dep_install_guard import evaluate


class OperandBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.cwd = self.root / 'project'
        self.home = self.root / 'home'
        self.bin = self.root / 'tools/bin'
        for d in (self.cwd, self.home, self.bin):
            d.mkdir(parents=True)
        self.env = {'HOME': str(self.home), 'PATH': str(self.bin)}
        for attr, val in (('_ETC', self.root/'etc'), ('_MAC_LIBRARY', self.root/'Library'), ('_PIP_SHARE_DIRS', ())):
            stub = patch.object(dep_guard_sources, attr, val)
            stub.start()
            self.addCleanup(stub.stop)

    def hook(self, cmd):
        self.calls = []

        def transport(method, url, body, timeout, limit):
            self.calls.append(unquote(urlsplit(url).path))
            return 404, b'{}'
        return evaluate(cmd, self.cwd, env=self.env, home=self.home, transport=transport)

    def abstains(self, cmd):
        result = self.hook(cmd)
        self.assertEqual([], self.calls, cmd)
        self.assertIsInstance(result, dict, cmd)
        self.assertNotIn('permissionDecision', result, cmd)
        self.assertIn('not checked', result.get('additionalContext', ''), cmd)

    def local(self, cmd):
        self.assertIsNone(self.hook(cmd), cmd)
        self.assertEqual([], self.calls, cmd)

    def checks(self, cmd, name):
        result = self.hook(cmd)
        self.assertEqual('deny', result.get('permissionDecision'), cmd)
        self.assertEqual(1, len(self.calls), cmd)
        self.assertIn('/'+name+'/', self.calls[0], cmd)

    def test_uv_uses_verb_specific_script_options_and_runs_with_short_w(self):
        script = self.cwd / 'report.py'
        script.write_text('print(42)\n')
        self.checks('uv add --script report.py requests', 'requests')
        self.checks('uv run -w one report.py', 'one')
        self.checks('uv run --script -w=two report.py', 'two')
        self.checks('uv tool run -wthree --from ./local-tool cmd', 'three')
        external = self.root / 'external.py'
        external.write_text('print(42)\n')
        (self.cwd / 'linked.py').symlink_to(external)
        for cmd in [f'uv run --with a {external}', 'uv run -w a linked.py', 'uv run -w a --script -',
                    f'uv add --script={external} a']:
            with self.subTest(cmd=cmd):
                self.abstains(cmd)
        script.write_text('# /// script\n# dependencies = []\n# ///\nprint(42)\n')
        self.abstains('uv run --with a report.py')
        self.abstains('uv add --script report.py a')

    def test_prior_config_writes_or_exports_invalidate_routes(self):
        for writer in ('npm config set registry xyz', 'npm set registry xyz', 'pnpm config set registry xyz',
                       'yarn config set npmRegistryServer xyz', '/opt/venv/bin/python -m pip config set global.index-url xyz',
                       'printf xyz > .npmrc', 'tee ~/Library/pip/pip.conf', 'cp template uv.toml',
                       'printf xyz >> bunfig.toml', 'echo xyz > .yarnrc.yml', 'echo xyz > "$DEST"',
                       'export PIP_INDEX_URL=https://pypi.org/simple', "bash -c 'touch .npmrc'"):
            with self.subTest(writer=writer):
                self.abstains(writer+' && npm install example')

    def test_login_and_interactive_shells_are_indeterminate(self):
        for cmd in ("bash -lc 'npm i confidential'", "bash -l -c 'pip install confidential'",
                    "zsh -ic 'npx confidential'", "sh --login -c 'npm i confidential'",
                    "bash -l <<'EOF'\nnpm i confidential\nEOF"):
            with self.subTest(cmd=cmd):
                self.abstains(cmd)
        self.checks("bash -c 'npm i public'", 'public')

    def test_noexec_and_function_declarations_do_not_execute_bodies(self):
        for cmd in ("bash -nc 'npx definitely-not-run'", "bash -n -c 'pip install not-run'",
                    'helper() { npx never; }; printf ok', 'function helper { pip install never; }; :',
                    "helper ()\n{ printf '}'; npx never; }; helper", 'f() ( npx never; ); :'):
            with self.subTest(cmd=cmd):
                self.local(cmd)
        self.checks('helper() { npx body; }; npm i next', 'next')

    def test_project_global_and_pipx_bins_are_local_but_explicit_specs_are_not(self):
        manifest = self.cwd / 'package.json'
        manifest.write_text(json.dumps({'name':'@example/own', 'bin':'./main.js'}))
        self.local('npx own')
        self.checks('npx --package=own own', 'own')
        manifest.write_text(json.dumps({'name':'different', 'bin':{'own-object':'./main.js'}}))
        self.local('npm exec -- own-object')
        for exe in ('node', 'npx', 'global-cli', 'npm'):
            p = self.bin / exe
            p.touch()
            p.chmod(0o700)
        self.local('npm exec global-cli')
        self.checks('npx -p global-cli global-cli', 'global-cli')
        prefix = self.root / 'other/bin'
        prefix.mkdir(parents=True)
        (prefix / 'configured').touch()
        (self.home / '.npmrc').write_text(f'prefix={prefix.parent}\n')
        self.local('npx configured')
        (self.cwd / 'report.py').write_text('print(42)\n')
        self.local('pipx run report.py')
        self.checks('pipx run --spec report.py report.py', 'report-py')

    def test_pep508_marker_and_npm_boolean_values_are_not_independent_requests(self):
        self.abstains('pip install "conditional==1.0; python_version < \'3\'"')
        self.checks('uv add "conditional; sys_platform == \'win32\'" active', 'active')
        self.checks('npm install --save-dev false a', 'a')
        self.checks('npm --save-optional true install b', 'b')
        self.checks('npm install -- true', 'true')


if __name__ == '__main__':
    unittest.main()
