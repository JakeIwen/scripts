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

    def test_npm_exec_resolution_table(self):
        # npm 10.9.4 exec.js + libnpmexec 9.0.1: explicit packages bypass
        # hasPkgBin and executable matching. Otherwise the package manifest is
        # checked in either mode; only the .bin walk changes its starting prefix.
        (self.bin / 'node').touch(mode=0o700)
        bins = self.cwd / 'node_modules/.bin'
        bins.mkdir(parents=True)
        for global_mode in (False, True):
            for explicit in (False, True):
                for target in ('manifest', 'project-bin', 'global-bin', 'absent'):
                    name = f'{target}-{int(global_mode)}-{int(explicit)}'
                    if target == 'manifest':
                        (self.cwd / 'package.json').write_text(json.dumps({'name': name, 'bin': 'main.js'}))
                    elif target == 'project-bin':
                        (bins / name).touch()
                    elif target == 'global-bin':
                        (self.bin / name).touch()
                    mode = '-g' if global_mode else '--global=false'
                    package = f'--package={name}' if explicit else ''
                    cmd = f'npm {mode} exec {package} -- {name}'
                    with self.subTest(cmd=cmd):
                        if explicit or target == 'absent' or (global_mode and target == 'project-bin'):
                            self.checks(cmd, name)
                        else:
                            self.local(cmd)

        global_tree = self.bin.parent / 'lib/node_modules/.bin'
        global_tree.mkdir(parents=True)
        (global_tree / 'tree-only').touch()
        self.local('npm -g exec -- tree-only')
        self.checks('npm --global=false exec -- tree-only', 'tree-only')

    def test_false_npm_global_and_project_location_keep_the_project_bin_local(self):
        bins = self.cwd / 'node_modules/.bin'
        bins.mkdir(parents=True)
        (bins / 'installed-bin').touch()
        for flags in ('--global=false', '-g false', '--no-global', '--location=project', '-L user',
                      '--global --no-global', '--no-global true', '--local', '-sg false'):
            with self.subTest(flags=flags):
                self.local(f'npm exec {flags} -- installed-bin')
        self.checks('npm exec --location=project --package=installed-bin -- cmd', 'installed-bin')

    def test_true_global_mode_checks_project_only_names_but_not_global_executables(self):
        node = self.bin / 'node'
        node.touch()
        node.chmod(0o700)
        (self.bin / 'global-cli').touch()
        bins = self.cwd / 'node_modules/.bin'
        bins.mkdir(parents=True)
        for i, flags in enumerate(('-g', '--global true', '--location=global', '--no-global=false',
                                   '--location=global --global=false', '-gs')):
            with self.subTest(flags=flags):
                self.local(f'npm exec {flags} -- global-cli')
                name = f'project-only-{i}'
                (bins / name).touch()
                self.checks(f'npm exec {flags} -- {name}', name)
        self.checks('npm --location global exec --package=global-cli -- cmd', 'global-cli')
        # A prefix set only in the ignored project config is not the global bin.
        not_global = self.root / 'project-prefix/bin'
        not_global.mkdir(parents=True)
        (not_global / 'wrong-prefix').touch()
        (self.cwd / '.npmrc').write_text(f'prefix={not_global.parent}\n')
        self.checks('npm -g exec -- wrong-prefix', 'wrong-prefix')

    def test_short_options_bind_a_boolean_only_after_a_boolean_expansion(self):
        self.checks('npm exec -gs false -- example', 'false')
        self.checks('npm exec -gq true -- example', 'true')
        self.checks('npm exec -gdD false -- example', 'example')
        (self.cwd / 'package.json').write_text(json.dumps({'name': 'false', 'bin': 'main.js'}))
        self.local('npm exec -dd false -- second')
        for flags in ('-gW', '-gz', '-gLtarget'):
            self.abstains(f'npm exec {flags} false -- not-an-operand')

    def test_npx_preprocessor_may_move_the_option_boundary_after_a_cluster(self):
        for command in ('npx -qy bin-name --package package-name command',
                        'npx -qy bin-name --registry https://example.invalid command',
                        'npx -qy ./local --package package-name command', 'npx -pfoo',
                        'npx -ypfoo cmd', 'npx -yp foo', 'npx -pfoo --package=@org/b foo bar'):
            with self.subTest(command=command):
                self.abstains(command)
        self.checks('npm exec -qy --package=package-name -- command', 'package-name')

    def test_npx_prefix_rejects_every_unproved_historical_switch(self):
        # Frozen inventory: expanding the npm table must never expand npx's grammar.
        old_switches = """
            --access --auth-type --before --cache --cafile --call --color --config --config-file --cwd
            --dev --dir --directory --dry-run --exact --fetch-timeout --filter --force --foreground-scripts
            --frozen-lockfile --global --globalconfig --https-proxy --ignore-existing --ignore-scripts
            --ignore-workspace-root-check --include --include-workspace-root --install-links
            --install-strategy --json --legacy-bundling --legacy-peer-deps --local --location --loglevel
            --logs-dir --no --no-audit --no-color --no-fund --no-global --no-install --no-package-lock
            --no-progress --no-save --offline --omit --optional --otp --package --package-lock-only
            --parseable --peer --prefer-offline --prefer-online --prefix --production --proxy --quiet
            --registry --save --save-dev --save-exact --save-optional --save-peer --save-prefix --save-prod
            --script-shell --shell --silent --strict-peer-deps --tag --userconfig --verbose --workspace
            --workspace-root-dir --workspaces --yes -B -C -D -E -L -O -P -S -W -c -d -dd -ddd -f -g -p -q
            -s -v -w -y
        """.split()
        for flag in old_switches:
            with self.subTest(flag=flag):
                if flag in ('-y', '--yes'):
                    name = 'yes-short' if flag == '-y' else 'yes-long'
                    self.checks(f'npx {flag} {name} --registry https://example.invalid cmd', name)
                elif flag in ('-p', '--package'):
                    name = 'spec-short' if flag == '-p' else 'spec-long'
                    self.checks(f'npx {flag} {name} bin-name --package cmd', name)
                else:
                    self.abstains(f'npx {flag} bin-name --registry https://example.invalid cmd')
        self.checks('npx -y --package=attached -- unrelated', 'attached')
        self.checks('npx --yes false --registry arg', 'false')
        for command in ('npx -y -p explicit --no-audit bin-name', 'npx -p=foo cmd',
                        'npx --yes=false cmd', 'npx --package --no-audit cmd', 'npx -p', 'npx --package=',
                        'npx -p explicit * --no-audit cmd'):
            self.abstains(command)

    def test_changed_tree_is_not_proof_of_a_local_runner(self):
        bins = self.cwd / 'node_modules/.bin'
        bins.mkdir(parents=True)
        (bins / 'semver').touch()
        self.local('git status && npx semver')
        self.abstains('mv node_modules node_modules.old && npx semver')
        self.abstains('printf data >node_modules/.bin/semver && npx semver')

    def test_local_bin_proof_never_authorizes_a_public_lookup(self):
        (self.cwd / '.npmrc').write_text('registry=https://private.invalid\n')
        bins = self.cwd / 'node_modules/.bin'
        bins.mkdir(parents=True)
        (bins / 'private-tool').touch()
        self.local('npm exec --global=false -- private-tool')
        self.abstains('npm exec --global=false -- other-private-name')
        self.abstains('npm exec --package private-tool -- private-tool')

    def test_environment_and_config_global_modes_do_not_create_a_project_exemption(self):
        bins = self.cwd / 'node_modules/.bin'
        bins.mkdir(parents=True)
        (bins / 'project-only').touch()
        self.checks('NPM_CONFIG_GLOBAL=true npm exec -- project-only', 'project-only')
        self.local('NPM_CONFIG_GLOBAL=true npm exec --no-global -- project-only')
        (self.home / '.npmrc').write_text('location=global\n')
        self.abstains('npm exec -- project-only')  # A possible npmrc global mode needs proof.
        self.local('npm exec --location=project -- project-only')

    def test_uv_add_pip_is_not_the_uv_pip_install_operation(self):
        result = self.hook('uv add pip requests')
        self.assertEqual('deny', result.get('permissionDecision'))
        self.assertCountEqual(['/pypi/pip/json', '/pypi/requests/json'], self.calls)

    def test_uv_run_with_pip_and_pipx_install_inject_use_their_actual_operations(self):
        self.checks('uv run --with pip python', 'pip')
        self.checks('pipx install inject', 'inject')
        # A real pipx inject, even with global options before its verb, must
        # still prove its named environment, not check the name on faith.
        self.abstains('pipx --verbose inject some-env inject')

    def test_pep508_marker_and_npm_boolean_values_are_not_independent_requests(self):
        self.abstains('pip install "conditional==1.0; python_version < \'3\'"')
        self.checks('uv add "conditional; sys_platform == \'win32\'" active', 'active')
        self.checks('npm install --save-dev false a', 'a')
        self.checks('npm --save-optional true install b', 'b')
        self.checks('npm install -- true', 'true')


if __name__ == '__main__':
    unittest.main()
