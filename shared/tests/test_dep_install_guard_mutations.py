"""Offline hook tests for source changes between Bash command segments."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dep_install_guard import evaluate


class MutationTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name).resolve()
        self.cwd = self.home / 'project'
        self.cwd.mkdir()
        self.env = {'HOME': str(self.home), 'PATH': str(self.home / 'empty-bin')}
        self.serial = 0
        self.urls = []
        # Do not consult the machine's global npmrc in an offline route fixture.
        fixture_etc = patch('dep_guard_sources._ETC', self.home / 'etc')
        fixture_etc.start()
        self.addCleanup(fixture_etc.stop)

    def check(self, command, *, public=False, env=None):
        self.serial += 1
        name = 'example-' + str(self.serial)
        cmd = command.replace('PACKAGE', name)
        self.urls.clear()

        def transport(method, url, body, timeout, limit):
            self.urls.append(url)
            return 404, b'{}'

        result = evaluate(cmd, self.cwd, env={**self.env, **(env or {})}, home=self.home, transport=transport)
        if public:
            self.assertEqual(result.get('permissionDecision'), 'deny', command)
            self.assertEqual(len(self.urls), 1, command)
            self.assertIn('/' + name + '/', self.urls[0], command)
        else:
            self.assertEqual(self.urls, [], command)
            self.assertNotIn('permissionDecision', result, command)
            self.assertIn('not checked', result.get('additionalContext', ''), command)

    def test_prior_write_to_each_pnpm_config_and_generic_relative_basename(self):
        for prefix in ('printf setting > ~/.config/pnpm/rc', 'cp local ~/.config/pnpm/config.yaml',
                       'cd ~/.config/pnpm && tee -a rc', 'printf setting > "$XDG_CONFIG_HOME/pnpm/config.yaml"',
                       'tee ~/Library/Preferences/pnpm/rc', 'touch ~/Library/Preferences/pnpm/config.yaml',
                       'echo setting >> pnpm-workspace.yaml'):
            with self.subTest(prefix=prefix):
                self.check(prefix + ' && pnpm add PACKAGE')

    def test_earlier_writes_invalidate_the_indirect_npmrc_actually_consulted(self):
        indirect = self.cwd / 'policy' / 'mirror'
        indirect.parent.mkdir()
        intermediate = self.home / 'defaults'
        (self.home / '.npmrc').write_text(f'globalconfig={intermediate}\n')
        intermediate.write_text(f'userconfig={indirect}\n')
        # Creation of a previously absent config matters as much as replacing one.
        self.check('echo setting >policy/mirror && npm i PACKAGE')
        indirect.write_text('registry=https://registry.npmjs.org/\n')
        (self.cwd / 'alias').symlink_to(indirect)
        for prefix in (f'printf setting >> {indirect}', 'tee -a policy/mirror', 'cp template alias',
                       'mv policy/mirror backup', 'cp mirror policy/', "sed -i 's/a/b/' policy/mirror",
                       'git diff --output=policy/mirror', 'dd if=template of=alias',
                       'cd policy && tee mirror', "bash -c 'tee alias'", 'tee "$DEST/mirror"',
                       'ln -s policy/mirror new-name && tee new-name'):
            with self.subTest(prefix=prefix):
                self.check(prefix + ' && npm install PACKAGE')
        self.check('echo setting > alias && npm exec -- PACKAGE')
        for prefix in ('tee mirror', 'cp policy/mirror backup', 'git diff -- policy/mirror',
                       'cat < policy/mirror', 'printf setting >policy/other'):
            with self.subTest(prefix=prefix):
                self.check(prefix + ' && npm install PACKAGE', public=True)
        self.check('npm install PACKAGE && tee alias', public=True)
        # An unknown directory plus an ordinary basename is harmless only when
        # no consulted config selected an indirect source with an arbitrary name.
        (self.home / '.npmrc').unlink()
        self.check('tee "$DEST/mirror" && npm i PACKAGE', public=True)

    def test_git_tree_changes_or_unknown_verbs_obscure_future_route(self):
        for invocation in ('switch other', 'checkout -- file', 'restore .', 'reset HEAD~1', 'pull', 'merge other',
                           'rebase origin/main', 'cherry-pick other', 'revert HEAD', 'stash pop', 'stash apply',
                           'worktree add elsewhere', 'clone local elsewhere', 'am change.patch', 'apply change.patch',
                           'custom-alias', '-C elsewhere custom-alias', '-c alias.inspect=status inspect',
                           '--work-tree=elsewhere -c key=value checkout other', '--future-global status',
                           'remote -v set-url origin elsewhere', 'branch --list -D target',
                           'branch -f target HEAD', 'branch --new-mode --list item', 'branch --format --list item'):
            with self.subTest(invocation=invocation):
                self.check('rtk proxy git ' + invocation + ' && npm install PACKAGE')

    def test_git_reads_still_check_an_install_even_when_the_pathspec_is_config(self):
        for invocation in ('status -- package.json', 'diff -- package.json', 'log -- .npmrc',
                           'show HEAD:package.json', 'grep private -- pip.conf', 'ls-files -- uv.toml',
                           'rev-parse --show-toplevel', 'branch', 'branch --list', 'branch --list package.json',
                           'branch -a --list "feature/*"', 'branch --list --format="%(refname:short)"',
                           'remote -v', 'blame -- .npmrc', '-C elsewhere --no-pager diff -- config.yaml',
                           '--git-dir=repo -c color.ui=false --work-tree tree log -- .env',
                           '-Celsewhere -ccolor.ui=false grep content -- pnpm-workspace.yaml'):
            with self.subTest(invocation=invocation):
                self.check('git ' + invocation + ' && npm install PACKAGE', public=True)
        self.check('git diff -- package.json >.npmrc && npm install PACKAGE')
        self.check('git log --output=.npmrc && npm install PACKAGE')

    def test_source_commands_in_outer_or_child_shell_obscure_the_future_route(self):
        for prefix in ('source ./setup', '. /tmp/local-env', 'builtin source local-env',
                       "bash -c '. ./setup'", "zsh -c ':'"):
            with self.subTest(prefix=prefix):
                self.check(prefix + ' && npm install PACKAGE')

    def test_child_shell_startup_uses_effective_environment(self):
        self.check("zsh -c 'npm install PACKAGE'")
        for shell in ('bash', 'sh'):
            body = shell + " -c 'npm install PACKAGE'"
            self.check(body, public=True)
            for key in ('BASH_ENV', 'ENV'):
                with self.subTest(shell=shell, key=key):
                    self.check(body, env={key: 'startup'})
                    self.check(key + '=startup ' + body)
                    self.check('env ' + key + '=startup ' + body)
                    self.check('export ' + key + '=startup && ' + body)
        self.check("npm install PACKAGE", env={'BASH_ENV': 'startup'}, public=True)


if __name__ == '__main__':
    unittest.main()
