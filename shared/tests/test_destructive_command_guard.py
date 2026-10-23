"""Exercise the destructive-command guard only through its hook JSON interface."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'destructive_command_guard.py'
SYSTEM_PATHS = [
    '/Users/jacobr', '/home/pi', '/Users', '/home', '/root', '/System',
    '/Library', '/Applications', '/usr', '/etc', '/var', '/opt', '/bin',
    '/sbin', '/private', '/Volumes', '/boot', '/lib', '/srv', '/mnt', '/media', '/dev',
]
BLOCKED = [
    'rm -rf /', 'rm -rf /*', 'rm -rf ~', 'rm -rf ~/', 'rm -rf ~/*',
    'rm -rf $HOME', 'rm -rf ${HOME}', 'rm -rf "$HOME"', 'rm -rf "${HOME}/"',
    'rm -rf $HOME/*', 'rm -rf ${HOME}/*', 'rm -rf "$HOME"/*',
    'rm -r /', 'rm -R /', 'rm --recursive /', 'rm -fr /', 'rm -Rf /',
    'rm -fR /', 'rm -r -f -- /', 'rm -f --recursive /', '/bin/rm -rf /',
    'rm / -r', 'rm -rf /tmp/safe /', 'rm -rf "/"', "rm -rf '/'",
    'rm -rf //', 'rm -rf /tmp/..', 'rm -rf ~/scratch/..', 'rm -rf /usr/./',
    'rm -rf "$DIR/"', 'rm -rf $DIR/*', 'rm -rf ${DIR}/*',
    'rm -rf ${A}${B}/', 'rm -rf "${A}${B}/"', 'rm -rf "$DIR"/',
    'rm -rf /${DIR}', 'rm -rf "${DIR:-}/" /',
    'sudo rm -rf /', 'sudo -u root -- rm -rf /', 'command rm -rf /',
    'command -p rm -rf /', 'env VAR=x rm -rf /', 'env -u TEMP VAR=x rm -rf /',
    'nohup rm -rf /', 'time rm -rf /', '/usr/bin/time -f "%E" rm -rf /',
    'timeout 5 rm -rf /', 'timeout -s TERM -k 2 5 rm -rf /',
    'xargs rm -rf /', 'xargs -0 -n 1 -P 2 rm -rf /', 'xargs -I {} rm -rf / {}',
    'sudo -p --help rm -rf /', 'sudo -p -l rm -rf /',
    'env -u --help rm -rf /', 'xargs -I --help rm -rf /',
    'git -C --help push -f',
    'env VAR=x sudo -n command nohup timeout 5 rm -rf ~', 'VAR=x rm -rf /',
    'true && rm -rf /', 'false || rm -rf /', 'echo ok; rm -rf /',
    'printf x | rm -rf /', 'printf x | xargs rm -rf /', 'echo ok\nrm -rf /',
    '(rm -rf /)', '(true && (rm -rf /))', '{ rm -rf /; }',
    'if true; then rm -rf /; fi', 'for x in a; do rm -rf /; done',
    'echo $(rm -rf /)', 'echo "$(rm -rf /)"', 'echo `rm -rf /`',
    'echo "`rm -rf /`"', 'echo "$(echo $(rm -rf /))"',
    'cat <(rm -rf /)', 'echo ok > "$(rm -rf /)"',
    'echo "${DIR:-$(rm -rf /)}"',
    "ssh host 'rm -rf ~'", 'ssh host rm -rf /', "ssh -p 22 host 'rm -rf /'",
    "ssh -o BatchMode=yes host 'sudo rm -rf ~/*'", 'ssh host "rm -rf ~"',
    "bash -c 'rm -rf /'", "sh -c 'rm -rf /'", "zsh -lc 'rm -rf /'",
    "sudo bash -c 'rm -rf /'", "bash -o pipefail -c 'rm -rf /'",
    "bash -euo pipefail -c 'rm -rf /'", "bash --rcfile /tmp/rc -c 'rm -rf /'",
    "eval 'rm -rf /'", "eval rm -rf /",
    "ssh host \"bash -c 'rm -rf ~'\"",
    "cat <<'EOF'\nsafe text\nEOF\nrm -rf /",
    "cat <<EOF\nsafe text\nEOF\n$(rm -rf /)",
    'rm -rf / > /tmp/guard-log', '2>/tmp/guard-log rm -rf /',
    'git push -f', 'git push --force', 'git push --force-with-lease',
    'git push --force-with-lease=main', 'git push --force-with-lease=main:abc',
    'git push --force-if-includes', 'git push origin +main',
    'git push origin +HEAD:refs/heads/main', 'git push -- origin +main',
    'git -C /tmp/repo push --force', 'git -C/tmp/repo push --force',
    'git -c color.ui=false -C /tmp/repo push -f origin main',
    'git --git-dir=/tmp/repo/.git push -f', 'git push -uf origin main',
    'git push -ufoflag origin main', 'git push -oflag -f origin main',
    'sudo git push -f', "bash -c 'git push --force-with-lease'",
    "ssh host 'git -C repo push origin +main'", 'echo "$(git push -f)"',
] + ['rm -rf ' + path + suffix for path in SYSTEM_PATHS for suffix in ('', '/', '/*', '/*/')]
ALLOWED = [
    'rm -rf "$TMPDIR/foo"', 'rm -rf "$OUT/build"', 'rm -rf ./node_modules',
    'rm -rf ~/scratch/x', 'rm -rf /tmp/foo', 'rm ~/file', 'rm -f /tmp/foo',
    'rm -rf /usr/local/scratch', 'rm -rf /Users/jacobr/scratch', 'rm -rf /home/pi/scratch',
    'rm -rf /Users/someone', 'rm -rf /tmp', 'rm -rf /tmp/*', 'rm -rf /var/tmp/build',
    'rm -rf "${TMPDIR}/foo"', 'rm -rf "${OUT}/build"', 'rm -rf "$DIR"',
    'rm -rf "${DIR:?}"', 'rm -rf "${DIR:?set DIR first}/"',
    'rm -rf ${A:?}${B:?}/', 'rm -rf "${A:?}/${B:?}/*"',
    'rm -rf "${HOME:?}"', 'rm -rf "${DIR:-/tmp}/build"',
    "rm -rf '$DIR/'", "rm -rf '${HOME}'", 'rm -rf \\$DIR/',
    "rm -rf '~'", 'rm -rf "~"', 'rm -rf \\~', 'rm -rf "\\$HOME"',
    'rm -- -rf /', 'rm -rf -- -r', 'rm -rf ""', 'rm -rf',
    'rm --help -rf /', 'command -v rm -rf /', 'command -p -V rm -rf /',
    'command -pv rm -rf /', 'sudo -l rm -rf /', 'env --help rm -rf /',
    'bash --help -c "rm -rf /"', 'bash -- -c "rm -rf /"',
    'echo rm -rf /', "echo 'rm -rf /'", 'echo "rm -rf /"',
    'git commit -m "block rm -rf ~"', 'git commit -m "git push --force"',
    'grep -n "rm -rf" file', "printf '%s' 'rm -rf /'", '# rm -rf /',
    'echo ok # rm -rf /\necho still-safe', "echo '$(rm -rf /)'", "echo '`rm -rf /`'",
    'echo "\\$(rm -rf /)"', 'echo "\\`rm -rf /\\`"',
    'echo "$(printf \'%s\' \'rm -rf /\')"', 'echo "${DIR:?rm -rf /}"',
    "cat > f <<'EOF'\nrm -rf /\ngit push -f\n$(rm -rf /)\nEOF",
    'cat <<EOF\nrm -rf /\nEOF', 'cat <<-EOF\n\trm -rf /\n\tEOF',
    'cat <<$(rm -rf /)\nsafe\n$(rm -rf /)',
    "cat <<'FIRST' <<'SECOND'\nrm -rf /\nFIRST\ngit push -f\nSECOND",
    "echo 'cat <<EOF\nrm -rf /\nEOF'", "cat <<'EOF'\nrm -rf /\nEOF\necho done",
    "bash -c 'echo rm -rf /'", "ssh host 'echo rm -rf /'", "eval 'echo rm -rf /'",
    'ssh host echo rm -rf /', 'bash script.sh "rm -rf /"', 'python3 -c "rm -rf /"',
    'git push', 'git push origin main', 'git push --no-force',
    'git push --no-force-with-lease', 'git push --no-force-if-includes',
    'git -C repo status', 'git fetch --force', 'git pull -f', 'git branch -f topic',
    'git --help push --force', 'git push -o "-f" origin main',
    'git push -oflag origin main', 'git push -uoflag origin main',
    'git push -uo "--force" origin main',
    'git push --push-option "--force" origin main', 'git push --repo +server main',
    'git push --receive-pack "--force" origin main', 'git push -- origin -f',
    "echo 'git push origin +main'", 'echo "$(git status --short)"',
    # Incomplete shell input is fail-open, not a reason to regex-match quoted prose.
    "echo 'rm -rf /", 'echo "$(rm -rf /)', "cat <<'EOF'\nrm -rf /",
]


CD_BLOCKED = [
    'cd / && rm -rf *', 'cd /; rm -rf .', 'cd ~\nrm -rf ./*',
    'cd && rm -rf .*', 'cd "$VAR" && rm -rf ./', 'cd "${VAR}" && rm -rf ..',
    'cd "${VAR?}" && rm -rf ./*', 'cd "${VAR?set it}" && rm -rf .',
    'cd /Users/jacobr && rm -rf ..', 'cd /usr && rm -rf ../*',
    'cd /home/pi && rm -rf foo/..', 'cd /usr && rm -rf ./scratch/../*',
    'cd /tmp/x; cd /; rm -rf ./*', 'cd /Users/jacobr/scratch && cd .. && rm -rf *',
    'cd /usr || rm -rf .*', 'cd / && (rm -rf .)',
    'cd / || cd /tmp/x && rm -rf ./*', "cd / || eval 'cd /tmp/x'; rm -rf .",
    'cd /tmp/x || cd /; rm -rf ./*', 'cat | cd /; rm -rf ./*',
    '(cd /; rm -rf ./*)', "eval 'cd /' && rm -rf ./*",
    "bash -c 'cd / && rm -rf *'", "ssh host 'cd ~ && rm -rf .*'",
    'cd / && echo "$(rm -rf ./*)"; cd /tmp/x', 'cd / && echo `rm -rf .`',
    'cd /tmp/x && echo "$(cd / && rm -rf *)"',
    'cd / && (cd /tmp/x && rm -rf ./*); rm -rf ./*',
    'rm -rf "${VAR?}/"', 'rm -rf "${VAR?must be set}/*"', 'rm -rf ${A?}${B?}/',
] + ['cd ' + root + ' && rm -rf ' + target
     for root in ('/', '~', '/usr', '/home/pi', '/Users/jacobr')
     for target in ('*', '.', './', './*', '.*', '..')]
CD_ALLOWED = [
    'cd /tmp/x && rm -rf *', 'cd "$TMPDIR/foo" && rm -rf ./*',
    'cd ~/scratch && rm -rf ./*', 'cd /tmp/x && rm -rf .',
    'cd /tmp/x && rm -rf ..', 'cd /usr && rm -rf scratch',
    'cd / && rm -rf /tmp/x', 'cd /tmp/x && cd nested && rm -rf ./*',
    '(cd /); rm -rf ./*', 'cd /tmp/x && (cd /); rm -rf ./*',
    'cd /tmp/x; bash -c "cd /"; rm -rf ./*',
    'cd / | cat; rm -rf ./*', 'cd / & rm -rf ./*',
    'echo "$(cd /)"; rm -rf ./*', 'rm -rf ./*; cd /',
    'cd /tmp/x && rm -rf "$VAR"', 'cd "${VAR:?}" && rm -rf ./*',
    'cd "${VAR:?set VAR}" && rm -rf .*', 'rm -rf "${VAR:?}/"',
    'rm -rf "${VAR:?not empty}/*"', 'rm -rf ${A:?}${B?}/',
    'cd /tmp/x; cd -; rm -rf ./*', 'cd / && cd /tmp/x && rm -rf ./*',
    "cd / && eval 'cd /tmp/x' && rm -rf ./*", "ssh host 'cd /'; rm -rf ./*",
    "cd / && ssh host 'rm -rf ./*'",
]
MAC_BLOCKED = [
    'find / -delete', 'find -- / -delete', 'find ~ -delete', 'find "$DIR/" -delete',
    'find $DIR/* -delete', 'find ${A}${B}/ -delete', 'find "${DIR?}/" -delete',
    'find /tmp/x / -type f -delete', 'find -H /usr -delete', 'find -f / -delete',
    'find / -exec rm {} \\;', 'find ~ -execdir rm -f {} +',
    'find / -exec sudo rm -rf {} \\;', 'sudo find /Users/jacobr -delete',
    'find / -exec echo {} \\; -delete', 'cd / && find . -delete',
    'cd ~ && find ./* -delete', 'cd "$VAR" && find .* -delete',
    'cd "${VAR?}" && find . -delete', 'cd /usr && find -delete',
    'cd / && find . -execdir rm {} +', "ssh host 'find ~ -delete'",
    "bash -c 'cd / && find . -delete'", 'echo "$(find / -delete)"',
    'dd if=/tmp/input of=/dev/disk2', 'dd of=/dev/rdisk3 bs=1m',
    'dd of=/dev/null', 'sudo dd "of=/dev/disk2"', 'dd of=/dev/$DISK',
    '/sbin/mkfs /dev/disk2', 'mkfs.ext4 /dev/disk2', 'mkfs_msdos /dev/disk2',
    'newfs /dev/disk2', 'newfs_apfs /dev/disk2', '/sbin/newfs_msdos /dev/disk2',
    'diskutil eraseDisk APFS scratch /dev/disk2',
    'diskutil eraseVolume APFS scratch /dev/disk2s1',
    'diskutil zeroDisk /dev/disk2', 'diskutil randomDisk 1 /dev/disk2',
    'diskutil secureErase 0 /dev/disk2', 'diskutil partitionDisk /dev/disk2 1 GPT APFS x 100%',
    'diskutil reformat /dev/disk2', 'diskutil apfs deleteContainer disk2',
    'diskutil apfs deleteVolume disk2s1', 'diskutil apfs eraseVolume disk2s1',
    'sudo diskutil eraseDisk APFS x /dev/disk2', "ssh host 'dd of=/dev/disk2'",
    "eval 'diskutil apfs deleteVolume disk2s1'",
] + ['find ' + root + ' -delete' for root in SYSTEM_PATHS]
MAC_ALLOWED = [
    'find ./x -delete', 'find /tmp/x -delete', 'find ~/scratch -delete',
    'find /tmp/x -exec rm -rf {} \\;', 'find /usr -print', 'find / -name "-delete"',
    'find / -path "-delete" -print', 'find / -printf "-delete"',
    'find / -fprintf /tmp/output "-delete"', 'find / -exec echo -delete {} \\;',
    'find / -exec echo rm -rf {} \\;', 'find / -execdir printf "%s" rm {} +',
    'find "${DIR:?}/" -delete', 'cd "${DIR:?}" && find . -delete',
    'cd /tmp/x && find . -delete', 'cd "$TMPDIR/foo" && find ./* -delete',
    '(cd /); find . -delete', 'find . -delete; cd /',
    'cd / && find . -name "-delete" -print', 'cd / && find . -exec echo rm {} \\;',
    'dd if=/dev/disk2 of=/tmp/backup.img', 'dd if=/tmp/input of=/tmp/output',
    'dd if=/tmp/input', 'diskutil list', 'diskutil info /dev/disk2',
    'diskutil apfs list', 'diskutil apfs listVolumeGroups', 'diskutil info eraseDisk',
    'echo "dd of=/dev/disk2"', 'echo "mkfs.ext4 /dev/disk2"',
    'grep -n "diskutil eraseDisk" file', "cat <<'EOF'\ndd of=/dev/disk2\nEOF",
]


class DestructiveCommandGuardTests(unittest.TestCase):
    def run_hook(self, stdin, platform='linux'):
        environment = dict(os.environ)
        if platform is None:
            environment.pop('DESTRUCTIVE_GUARD_PLATFORM', None)
        else:
            environment['DESTRUCTIVE_GUARD_PLATFORM'] = platform
        return subprocess.run([sys.executable, '-I', str(SCRIPT)], input=stdin,
                              capture_output=True, text=True, check=False, env=environment)

    def command_result(self, command, platform='linux'):
        return self.run_hook(json.dumps({
            'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
            'tool_input': {'command': command},
        }), platform)

    def assert_denied(self, command, platform):
        result = self.command_result(command, platform)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertTrue(result.stdout, 'expected deny output')
        decision = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(decision['hookEventName'], 'PreToolUse')
        self.assertEqual(decision['permissionDecision'], 'deny')
        self.assertIn('Blocked ', decision['permissionDecisionReason'])
        self.assertIn('If Jacob really wants this, ask him to run it himself '
                      '(in Claude: `! <command>`).', decision['permissionDecisionReason'])

    def assert_allowed(self, command, platform):
        result = self.command_result(command, platform)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_blocks_dangerous_commands(self):
        for platform in ('linux', 'darwin'):
            for command in BLOCKED + CD_BLOCKED:
                with self.subTest(command=command, platform=platform):
                    self.assert_denied(command, platform)

    def test_passes_safe_commands_without_overriding_permissions(self):
        for platform in ('linux', 'darwin'):
            for command in ALLOWED + CD_ALLOWED + MAC_ALLOWED:
                with self.subTest(command=command, platform=platform):
                    self.assert_allowed(command, platform)

    def test_mac_rules_do_not_block_pi_disk_work(self):
        for command in MAC_BLOCKED:
            with self.subTest(command=command, platform='darwin'):
                self.assert_denied(command, 'darwin')
            with self.subTest(command=command, platform='linux'):
                self.assert_allowed(command, 'linux')

    def test_real_platform_is_used_without_a_valid_test_override(self):
        for platform in (None, 'invalid'):
            result = self.command_result('dd of=/dev/disk2', platform)
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            self.assertEqual(bool(result.stdout), sys.platform == 'darwin')

    def test_malformed_payloads_fail_open(self):
        payloads = ['', 'not json', '{', 'null', '[]', '42', 'true', '"text"']
        payloads += [json.dumps(payload) for payload in [
            {}, {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
                 'tool_input': {'command': 'rm -rf /'}},
            {'hook_event_name': 'PreToolUse', 'tool_name': 'Read',
             'tool_input': {'command': 'rm -rf /'}},
            {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash'},
        ]]
        for tool_input in (None, [], 'text', {}, {'command': None}, {'command': 42},
                           {'command': []}, {'command': ''}, {'command': ' \n'}):
            payloads.append(json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                                        'tool_input': tool_input}))
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.run_hook(payload)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))


if __name__ == '__main__':
    unittest.main()
