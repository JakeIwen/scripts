"""Only literal package operands reach the parser/lookup boundary."""

from pathlib import Path
import tempfile
import unittest

from dep_guard_commands import parse_commands
from dep_guard_forms import DIRECT, VERBS
from dep_install_guard import candidate


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def parse(self, text):
        requests = list(parse_commands(text, self.root))
        if requests:
            self.assertTrue(candidate(text, {}), 'prefilter discarded a parsed invocation: ' + text)
        return requests

    def assertNames(self, command, expected):
        result = self.parse(command)
        self.assertEqual(expected.split(), [r.name for r in result], command)
        return result

    def test_prefilter_uses_every_form_understood_by_the_parser(self):
        for exe, forms in VERBS.items():
            for verb, mode in forms.items():
                suffix = '--with example program' if mode == 'with-only' else 'env example' if mode == 'inject' else 'example'
                cmd = f'/opt/bin/{exe} {verb} {suffix}'
                with self.subTest(cmd=cmd):
                    self.assertTrue(self.parse(cmd))
        for exe in DIRECT:
            self.assertTrue(self.parse(exe + ' example'))
        for cmd in ('np"m" i example', 'np\\\nm in example', 'printf ok\nnpm install example',
                    '/bin/bash -c "python3.9 -mpip install example"', 'rtk proxy uv run -wexample python'):
            self.assertTrue(self.parse(cmd), cmd)

    def test_prefilter_ignores_tool_mentions_without_an_install_operation(self):
        for cmd in ('git status', 'npm --version', 'git grep npm', 'git grep pkg-npm',
                    'echo pipx', 'uv run python', 'yarn --help', 'npm run build'):
            self.assertFalse(candidate(cmd, {}), cmd)

    def test_node_installs(self):
        for tool, verbs in [('npm', 'install i in add isntall it install-test'), ('pnpm', 'add install i'),
                            ('yarn', 'add'), ('bun', 'add install i')]:
            for verb in verbs.split():
                with self.subTest(tool=tool, verb=verb):
                    cmd = f'{tool} --registry https://registry.npmjs.org {verb} -D abc @org/bar@2.1.0'
                    a, b = self.assertNames(cmd, 'abc @org/bar')
                    self.assertEqual((a.ecosystem, a.tool, a.cwd, a.pin, b.pin), ('npm', tool, self.root, None, '2.1.0'))
        self.assertNames('yarn global add abc xyz', 'abc xyz')

    def test_node_runners_have_different_option_ownership(self):
        for cmd, names in [
            ('npx -p foo tool', 'foo'), ('npx --package=foo --package=@org/b foo bar', 'foo @org/b'), ('npx app -p hidden', 'app'),
            ('npm exec -p app', 'app'), ('npm exec app --package foo', 'foo'),
            ('npm exec --package foo -- app --package hidden', 'foo'), ('npm x -- app -p other', 'app'),
            ('npx -- app --package hidden', 'app'), ('npx --package=foo -c "hello world"', 'foo'),
            ('npm exec --call=hello', ''), ('pnpm --package=one dlx --package two tool --package hidden', 'one two'),
            ('corepack pnpm dlx foo hidden', 'foo'), ('pnpx tool --package hidden', 'tool'),
            ('corepack yarn dlx -p one --package=two do-stuff -p data', 'one two'), ('yarn dlx tool x', 'tool'),
            ('bun x tool x', 'tool'), ('bunx --package one tool x', 'one'), ('bunx -pone tool', 'one'),
        ]:
            with self.subTest(cmd=cmd):
                self.assertNames(cmd, names)
        item, = self.parse('npx app --registry https://args.invalid')
        self.assertEqual(item.argv, ('npx', 'app'))
        create, = self.parse('npm create app --registry https://private.invalid')
        self.assertIn('https://private.invalid', create.argv)

    def test_npm_initializer_translation_and_aliases(self):
        for cmd, name, pin in [('npm init app@2.0.0', 'create-app', '2.0.0'),
                               ('npm create @org', '@org/create', None),
                               ('npm init @org@1.2.0', '@org/create', '1.2.0'),
                               ('npm create @org/app@next', '@org/create-app', None),
                               ('npm i alias@npm:@org/actual@1.0.0', '@org/actual', '1.0.0')]:
            with self.subTest(cmd=cmd):
                item, = self.parse(cmd)
                self.assertEqual((item.name, item.pin), (name, pin))

    def test_non_request_operands_and_specs(self):
        self.assertNames('npm i git+ssh://host/pkg.git ./local ../other /tmp/tar.tar.gz '
                         'owner/repo file:thing pkg@file:../here pkg@workspace:* '
                         'https://a.invalid/pkg.tgz a.tgz $PACKAGE good', 'good')
        self.assertNames('pip install ../local ./local file.whl https://example.invalid/source '
                         '"secret-name @ https://sites.invalid/p" secret2 @ https://sites.invalid/p '
                         'git+https://sites.invalid/test -e ./work --constraint constraint.txt $PKG actual', 'actual')
        self.assertNames("printf '%s\\n' 'npm i false'; cat <<'EOF'\nnpm i hidden\nEOF\npip install abc >cache", 'abc')
        self.assertNames('echo $(npx test) && >redirect pip install -r requirements.txt ok 2>err', 'test ok')
        self.assertNames('ssh vanpi "npm install remote"; command -v npm install pretend; bash ./setup.sh', '')
        self.assertNames('npm ci; npm install -g; pnpm install; yarn; bun i; npm init -y; '
                         'pip install -r requirements.txt; uv sync; poetry install; pipx list', '')

    def test_pip_family_and_requirements(self):
        for tool in ('pip', 'pip3', 'pip3.12', '/srv/venv/bin/pip', 'python -m pip', '/p/bin/python3.9 -Im pip',
                     '/p/bin/python3.11 -I -B -m pip', 'uv pip'):
            with self.subTest(tool=tool):
                a, b, c = self.assertNames(tool + ' --quiet install "A_b[C,d]==1.2rc1" "z-x~=1.5" '
                                           '"Foo.bar>=2; python_version < \'3.9\'"', 'a-b z-x foo-bar')
                self.assertEqual((a.pin, b.pin, c.pin), ('1.2rc1', None, None))
                self.assertEqual(a.executable, tool.split()[0])
        self.assertNames('python -m something pip install fake', '')
        self.assertNames('pip install -ihttps://pypi.org/simple -rfile.txt --proxy http://proxy.invalid pkg', 'pkg')
        self.assertNames('poetry add --group dev cleo@^2.1 another@latest', 'cleo another')

    def test_python_runners(self):
        for cmd, names in [('uv add --group dev one two', 'one two'), ('uv tool install --with abc one', 'abc one'),
                           ("uvx --with 'one[ex,ey,ez],two>=1,<2' --from actual tool app-arg", 'one two actual'),
                           ('uv tool run --with=more actual --with hidden', 'more actual'),
                           ('uv run --with=more app command --with hidden', 'more'),
                           ('uv run --with x -- python -m pip install hidden', 'x'),
                           ('pipx install --python python3 tool', 'tool'), ('pipx run tool fake', 'tool'),
                           ('pipx run --spec other tool fake --spec hidden', 'other'),
                           ('uvx actual@1.2.3', 'actual'), ('uv tool run actual@latest', 'actual'),
                           ("uv run --with 'name @ https://repo.invalid/tar,secret' app", ''),
                           ('pipx inject --include-deps env-name one two', 'one two')]:
            with self.subTest(cmd=cmd):
                self.assertNames(cmd, names)

    def test_pins_never_turn_ranges_into_exact_versions(self):
        for command in ['npm i a@^1.0.0', 'npm i a@1', 'npm i a@1.2.x', 'npm i a@next',
                        'pip install "a==1.2.*"', 'pip install "a>=1.2,<1.3"']:
            with self.subTest(command=command):
                item, = self.parse(command)
                self.assertIsNone(item.pin)

    def test_wrappers_and_inherited_environment_do_not_move_overrides(self):
        cmd = ("A=b rtk proxy env B=c command nice -n 5 nohup time -p timeout -s TERM 5 "
               "bash -lc 'DEP_GUARD_ALLOW=good exec npm i good bad'")
        good, bad = self.parse(cmd)
        self.assertEqual(good.env, {'A': 'b', 'B': 'c', 'DEP_GUARD_ALLOW': 'good'})
        self.assertTrue(good.override)
        self.assertFalse(bad.override)
        c = "DEP_GUARD_ALLOW=good bash -ic 'pip install good'; env DEP_GUARD_ALLOW=Foo_Bar pip3 install foo.bar; pip install foo-bar"
        self.assertEqual([x.override for x in self.parse(c)], [False, True, False])
        c = 'DEP_GUARD_ALLOW=alias npm i alias@npm:actual; DEP_GUARD_ALLOW=actual npm i alias@npm:actual'
        self.assertEqual([x.override for x in self.parse(c)], [False, True])
        c = 'export PIP_INDEX_URL=https://private.invalid DEP_GUARD_ALLOW=foo; pip install foo'
        obj, = self.parse(c)
        self.assertEqual(obj.env.get('PIP_INDEX_URL'), 'https://private.invalid')
        self.assertFalse(obj.override)

    def test_relocation_is_never_treated_as_payload_cwd(self):
        for prefix in ['sudo', 'sudo env', 'sudo -u user', 'sudo -H', 'sudo -iu bob', 'env -i', 'env -C private',
                       'HOME=/other', 'XDG_CONFIG_HOME=/other']:
            with self.subTest(prefix=prefix):
                obj, = self.parse(prefix+' npm install abc')
                self.assertIsNone(obj.cwd)
        for command in ['npm -Cexample i p', 'npm -w one install p', 'npm i --prefix=d p',
                        'uv --directory d add p', 'uv add --project=d p', 'pip --isolated install p']:
            with self.subTest(command=command):
                obj, = self.parse(command)
                self.assertIsNone(obj.cwd)

    def test_cd_and_local_runner_qualification(self):
        directory = self.root / 'project' / 'child'
        directory.mkdir(parents=True)
        bins = self.root / 'project/node_modules/.bin'
        bins.mkdir(parents=True)
        (bins / 'foo').touch()
        (bins / 'folder').mkdir()
        command = 'cd project/child && '
        for tail, local in [('npm exec -- foo', True), ('npx foo', True), ('npx folder', False),
                            ('npx --package=foo foo', False), ('npx foo@latest', False),
                            ('npm add foo', False), ('npm exec --global foo', False),
                            ('pnpm dlx foo', False), ('npm exec -w other foo', False)]:
            with self.subTest(tail=tail):
                obj, = self.parse(command+tail)
                self.assertEqual(obj.local, local)
                if '-w ' not in tail:
                    self.assertEqual(obj.cwd, directory)
        for command in ['cd "$PRIV" && npx foo', '(cd project/child); npm i foo', 'true && cd project; npm i foo']:
            with self.subTest(command=command):
                obj, = self.parse(command)
                self.assertIsNone(obj.cwd)
                self.assertFalse(obj.local)


if __name__ == '__main__':
    unittest.main()
