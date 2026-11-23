"""Public hook boundary: private routes neither disclose an operand nor use a cached verdict."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import dep_guard_sources
from dep_install_guard import evaluate


class SourcePrivacyTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / 'home'
        self.project = self.root / 'project'
        self.cwd = self.project / 'nested'
        self.bin = self.root / 'toolchain/bin'
        for folder in (self.home, self.cwd, self.bin):
            folder.mkdir(parents=True)
        self.env = {'HOME': str(self.home), 'PATH': str(self.bin)}
        self.etc = self.root / 'etc'
        self.library = self.root / 'Library'
        self.system = self.root / 'usr/bin'
        for alias, target in [('pip', 'pip3'), ('python3', 'python3')]:
            launcher = self.seed(self.system / target, f'#!{self.system}/python3\n' if target == 'pip3' else 'not executed')
            launcher.chmod(0o700)
            (self.bin / alias).symlink_to(launcher)
        for key, value in (('_ETC', self.etc), ('_MAC_LIBRARY', self.library), ('_PIP_SHARE_DIRS', ()),
                           ('_SYSTEM_BIN', self.system), ('_DARWIN', False)):
            p = patch.object(dep_guard_sources, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

    def seed(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def hook(self, cmd, env, respond):
        self.calls.clear()

        def transport(method, url, body, timeout, limit):
            self.calls.append(url)
            if not respond:
                self.fail('private route reached transport')
            return 404, b'{}'

        return evaluate(cmd, self.cwd, env=dict(self.env, **env), home=self.home, transport=transport)

    def skipped(self, cmd, **env):
        with patch('dep_guard_run.Cache', side_effect=AssertionError('private route opened public cache')) as cache:
            result = self.hook(cmd, env, False)
            cache.assert_not_called()
        self.assertFalse(self.calls, 'a private name reached transport')
        self.assertIsInstance(result, dict)
        self.assertNotIn('permissionDecision', result)
        self.assertIn('not checked: private/offline', result.get('additionalContext', ''))
        self.assertNotIn('confidential.invalid', str(result))
        self.assertNotIn('secret-token', str(result))

    def checked(self, cmd, **env):
        result = self.hook(cmd, env, True)
        self.assertEqual(1, len(self.calls), 'public existence alone precedes a 404 deny')
        self.assertEqual(result.get('permissionDecision'), 'deny')

    def test_cli_private_offline_extra_and_indirect_inputs(self):
        for cmd in (
            'npm --registry https://confidential.invalid add example',
            'npm install example --offline', 'pnpm add example --registry=https://confidential.invalid',
            'pip install example -ihttps://confidential.invalid', 'pip install --no-index example',
            'pip install -f./wheelhouse example', 'pip install example --extra-index-url=https://pypi.org/simple',
            'pip install -rrequirements.txt example', 'python3 -m pip install example -c pins.txt',
            'pip install example --requirement=local.txt', 'uv pip install -r local.txt example',
            'uv --offline add example', 'uv add --default-index https://confidential.invalid example',
            'uv tool run --index=corp=https://confidential.invalid --from example app',
            'poetry add --source internal example', 'pipx install --pip-args="-i private" example',
        ):
            with self.subTest(cmd=cmd):
                self.skipped(cmd)

    def test_base_and_inline_environments(self):
        for cmd, key, value in (
            ('npm install example', 'nPm_CoNfIg_ReGiStRy', 'https://secret-token@confidential.invalid'),
            ('npm install example', 'npm_config_offline', 'true'),
            ('bun add example', 'NPM_CONFIG_USERCONFIG', '/another/npmrc'),
            ('yarn add example', 'YARN_NPM_REGISTRY_SERVER', 'https://confidential.invalid'),
            ('pip install example', 'PIP_INDEX_URL', 'https://pypi.org.confidential.invalid/simple'),
            ('pip install example', 'PIP_INDEX_URL', 'https://pypı.org/simple'),
            ('pip install example', 'PIP_EXTRA_INDEX_URL', 'https://pypi.org/simple'),
            ('pipx install example', 'PIP_NO_INDEX', '1'),
            ('pip install example', 'PIP_CONFIG_FILE', 'custom.conf'),
            ('uvx example', 'UV_INDEX', 'private=https://confidential.invalid'),
            ('uv tool install example', 'UV_DEFAULT_INDEX', 'https://confidential.invalid'),
            ('uv add example', 'UV_INDEX_URL', 'https://confidential.invalid'),
            ('uv pip install example', 'UV_EXTRA_INDEX_URL', 'https://pypi.org/simple'),
            ('uv add example', 'UV_OFFLINE', '1'),
            ('pip install example', 'XDG_DATA_HOME', str(self.root)),
            ('uv pip install example', 'XDG_DATA_DIRS', str(self.root)),
        ):
            with self.subTest(key=key):
                self.skipped(cmd, **{key: value})
                self.skipped(f'{key}={value} {cmd}')

    def test_hidden_sources_in_project_user_and_system_files(self):
        bad = 'https://secret-token@confidential.invalid/simple'
        pip = f'[install]\nindex-url = {bad}\n'
        xdg = self.root / 'xdg'
        xdg2 = self.root / 'xdg-second'
        for file, text, cmd, env in (
            (self.home / '.npmrc', f'registry={bad}', 'npm install example', {}),
            (self.project / '.yarnrc.yml', 'npmScopes: {}', 'corepack yarn add example', {}),
            (self.home / '.yarnrc', f'registry "{bad}"', 'yarn global add example', {}),
            (self.cwd / '.env.local', f'BUN_CONFIG_REGISTRY={bad}', 'bun add example', {}),
            (self.project / '.venv/pip.conf', pip, 'uv pip install example', {}),
            (self.cwd / 'bunfig.toml', f'[install]\nregistry="{bad}"', 'bun add example', {}),
            (self.home / '.bunfig.toml', f'[install.scopes]\ncorp="{bad}"', 'bunx example', {}),
            (self.project / 'pnpm-workspace.yaml', f'registries:\n  "@corp": {bad}', 'pnpm add example', {}),
            (self.project / 'uv.toml', f'[[index]]\nurl="{bad}"', 'uv add example', {}),
            (self.project / 'pyproject.toml', f'[[tool."uv".index]]\nurl="{bad}"', 'uvx example', {}),
            (self.home / '.config/uv/uv.toml', f'index-url="{bad}"', 'uv pip install example', {}),
            (self.project / 'pyproject.toml', f'[[tool.poetry.source]]\nurl="{bad}"', 'poetry add example', {}),
            (self.home / '.config/pip/pip.conf', pip, 'pip install example', {}),
            (self.home / '.pip/pip.conf', f'[global]\nfind-links={bad}', 'pip install example', {}),
            (self.home / 'Library/Application Support/pip/pip.conf', pip, 'pip install example', {}),
            (self.library / 'Application Support/pip/pip.conf', pip, 'pip install example', {}),
            (self.etc / 'pip.conf', pip, 'pip install example', {}),
            (self.etc / 'xdg/pip/pip.conf', pip, 'pip install example', {'XDG_CONFIG_DIRS': str(xdg)}),
            (xdg / 'pip/pip.conf', pip, 'pip install example', {'XDG_CONFIG_HOME': str(xdg)}),
            (xdg2 / 'pip/pip.conf', pip, 'pip install example', {'XDG_CONFIG_DIRS': f'{xdg}:{xdg2}'}),
        ):
            with self.subTest(file=file, cmd=cmd):
                self.seed(file, text)
                try:
                    self.skipped(cmd, **env)
                finally:
                    file.unlink()

    def test_global_npm_config_follows_selected_npm(self):
        self.seed(self.bin / 'npm', '').chmod(0o700)
        self.seed(self.bin.parent / 'etc/npmrc', 'registry=https://confidential.invalid')
        self.skipped('npm add example')
        self.skipped('npx example')

    def test_npm_build_auth_and_engine_settings_do_not_disable_lookup(self):
        self.seed(self.home / '.npmrc', 'python=/fixture/python\nengine-strict=false\n'
                  '//registry.npmjs.org/:_authToken=fake-token\n')
        self.checked('npm install public-example')
        self.checked('npm install public-other', npm_config_python='/fixture/another-python')
        self.skipped('npm install private-example', npm_config_python='/fixture/python',
                     NPM_CONFIG_REGISTRY='https://confidential.invalid')

    def test_prior_output_invalidates_pip_config_without_comparing_symlink_targets(self):
        settings = self.seed(self.cwd / 'company-pip-settings', '[global]\nindex-url=https://pypi.org/simple\n')
        conf = self.home / '.config/pip/pip.conf'
        conf.parent.mkdir(parents=True)
        conf.symlink_to(settings)
        self.checked('printf ok 2>/dev/null && pip install public-example')
        for prefix in ('printf setting > company-pip-settings', 'cat <<<setting >company-pip-settings',
                       'tee -a company-pip-settings', 'printf ok >other-file', 'printf ok >2'):
            with self.subTest(prefix=prefix):
                self.skipped(prefix + ' && pip install confidential')

    def test_distribution_npmrc_with_an_absolute_globalconfig(self):
        config = self.root / 'distro/npmrc'
        self.seed(self.etc / 'npmrc', f'globalconfig={config}')
        self.seed(config, 'ignore-scripts=true')
        self.checked('npm i public-example')
        self.seed(config, 'registry=https://confidential.invalid')
        self.skipped('npm i public-example')

    def test_scoped_registry_is_per_operand_and_matches_alias_target(self):
        self.seed(self.project / '.npmrc', '@inside_scope:registry=https://confidential.invalid\nignore-scripts=true')
        self.skipped('npm i @inside_scope/private')
        self.skipped('npm install alias@npm:@inside_scope/private')
        result = self.hook('npm i @inside_scope/private outside', {}, True)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('outside', self.calls[0])
        self.assertNotIn('private', self.calls[0])
        self.assertIn('not checked', result['permissionDecisionReason'])

    def test_public_routes_are_checked_and_a_private_route_does_not_reuse_the_result(self):
        self.seed(self.home / '.npmrc', 'ignore-scripts=true\nregistry=https://registry.npmjs.org/')
        self.checked('npm install documented')
        self.skipped('npm install documented', NPM_CONFIG_REGISTRY='https://confidential.invalid')
        self.checked('python3 -m pip install -i https://pypi.org/simple documented')
        self.skipped('python3 -m pip install documented', PIP_NO_INDEX='1')

    def test_default_public_routes_allow_established_packages(self):
        urls = []

        def found(method, url, body, timeout, limit):
            urls.append(url)
            if 'dist-tags' in url:
                return 200, b'{"latest":"1.0.0"}'
            if method == 'HEAD':
                return 200, b''
            if '/downloads/' in url:
                return 200, b'{"downloads":100000}'
            if '/recent' in url:
                return 200, b'{"data":{"last_week":100000}}'
            if '/v1/querybatch' in url:
                return 200, b'{"results":[{}]}'
            self.fail('unexpected public request')

        for cmd, host in [('npm install public-example', 'registry.npmjs.org'),
                          ('pip install public-example', 'pypi.org')]:
            with self.subTest(cmd=cmd):
                urls.clear()
                self.assertIsNone(evaluate(cmd, self.cwd, env=self.env, home=self.home, transport=found))
                self.assertTrue(urls[0].startswith(f'https://{host}/'))
                self.assertEqual(len(urls), 3)

    def test_unreadable_or_nonregular_configs_do_not_become_public_defaults(self):
        rc = self.project / '.npmrc'
        for make in (lambda: rc.mkdir(), lambda: os.mkfifo(rc), lambda: rc.write_bytes(b'\xff'),
                     lambda: rc.write_bytes(b' ' * (256 * 1024 + 1)), lambda: rc.symlink_to('missing')):
            make()
            try:
                self.skipped('npm install example')
            finally:
                if rc.is_dir():
                    rc.rmdir()
                else:
                    rc.unlink()

    def test_selected_apple_launcher_uses_clt_or_xcode_framework_not_usr(self):
        selection = self.root / 'xcode_select_link'
        developers = [self.library / 'Developer/CommandLineTools', self.root / 'Xcode.app/Contents/Developer']
        with patch.object(dep_guard_sources, '_DARWIN', True), patch.object(dep_guard_sources, '_XCODE_SELECTION', selection):
            for version, developer in enumerate(developers, 9):
                with self.subTest(developer=developer):
                    prefix = developer / f'Library/Frameworks/Python3.framework/Versions/3.{version}'
                    python = self.seed(prefix / 'bin/python3', 'never execute')
                    python.chmod(0o700)
                    (developer / 'usr/bin').mkdir(parents=True)
                    (developer / 'usr/bin/python3').symlink_to(python)
                    selection.symlink_to(developer)
                    package = f'example-{version}'
                    self.checked(f'{self.system}/python3 -m pip install {package}')
                    self.seed(prefix / 'pip.conf', '[global]\nindex-url=https://confidential.invalid')
                    self.skipped(f'{self.system}/python3 -m pip install {package}')
                    self.skipped(f'{self.system}/pip3 install {package}')
                    selection.unlink()
            self.skipped('python3 -m pip install example-9')

    def test_custom_shim_and_missing_launcher_do_not_use_public_cache(self):
        self.checked('pip install example')
        shim = self.seed(self.root / 'shims/python', '#!/bin/sh\nexit 7\n')
        shim.chmod(0o700)
        self.skipped(f'{shim} -m pip install example')
        self.skipped('python -m pip install example', PATH=str(self.root / 'missing'))
        self.skipped('pip install example', PYTHONHOME=str(self.root))
        self.seed(self.system / 'pip3', f'#!{shim}\n')
        self.skipped('pip install example')

    def test_leading_dashes_on_pip_options_are_not_a_distinct_key(self):
        self.checked('pip install example')
        for key in ('--index-url', '--extra_index_url', '--find-links'):
            with self.subTest(key=key):
                self.seed(self.home / '.config/pip/pip.conf', f'[global]\n{key}=https://confidential.invalid')
                self.skipped('pip install example')

    def test_pipx_inject_uses_the_named_venv_and_not_its_own_executable_prefix(self):
        base = self.root / 'custom-pipx'
        venv = base / 'venvs/my-tool'
        self.seed(venv / 'pyvenv.cfg', 'include-system-site-packages = false')
        (venv / 'bin').mkdir()
        (venv / 'bin/python').symlink_to(self.system / 'python3')
        cmd = 'pipx inject my-tool example'
        self.checked(cmd, PIPX_HOME=str(base))
        self.seed(venv / 'pip.conf', '[global]\n--index-url=https://confidential.invalid')
        self.skipped(cmd, PIPX_HOME=str(base))
        self.skipped(cmd, PIPX_HOME=str(self.root / 'undiscoverable'))
        self.skipped(cmd + ' --global', PIPX_HOME=str(base))

    def test_uv_pip_only_checks_an_identified_target_environment(self):
        cmd = 'uv pip install example'
        self.skipped(cmd)
        venv = self.project / '.venv'
        self.seed(venv / 'pyvenv.cfg', 'include-system-site-packages = false')
        (venv / 'bin').mkdir()
        (venv / 'bin/python').symlink_to(self.system / 'python3')
        self.checked(cmd)
        self.seed(venv / 'pip.conf', '[global]\n--no-index=true')
        self.skipped(cmd)

    def test_selected_python_prefix_even_outside_virtualenv(self):
        prefix = self.root / 'selected'
        python = self.seed(prefix / 'bin/python', '')
        python.chmod(0o700)
        (self.bin / 'python').symlink_to(python)
        self.seed(prefix / 'pip.conf', '[global]\nindex-url=https://confidential.invalid')
        self.skipped('python -m pip install example')
        self.skipped(f'{python} -m pip install example')
        self.skipped('uv pip install example')
        self.skipped('pip install example', VIRTUAL_ENV=str(prefix))
        self.skipped(f'{prefix}/bin/pip install example')

    def test_unknown_or_relocated_directory_is_not_the_payload_directory(self):
        other = self.root / 'other'
        self.seed(other / '.npmrc', 'registry=https://confidential.invalid')
        for cmd in (f'cd {other} && npm i example', 'cd "$UNKNOWN" && npm i example',
                    f'npm --prefix {other} i example', f'npm -C{other} i example',
                    'npm --workspace private i example', 'npm i -wprivate example',
                    f'npm --userconfig {other}/.npmrc i example', f'npm i example --globalconfig={other}/.npmrc',
                    f'uv --directory={other} add example', f'uv add --project {other} example',
                    f'uv --config-file {other}/uv.toml add example', 'pip --isolated install example',
                    'sudo -u other npm i example', 'sudo -H pip install example', 'sudo -i npm i example',
                    'sudo -s npm i example', 'env -i npm i example', f'env -C {other} npm i example',
                    f'HOME={other} npm i example', f'XDG_CONFIG_HOME={other} pip install example'):
            with self.subTest(cmd=cmd):
                self.skipped(cmd)


if __name__ == '__main__':
    unittest.main()
