"""Exercise the destructive-command guard only through its hook JSON interface."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time
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
    'rm -rf "${DIR:-/tmp}/build"',
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
    'cd / && (rm -rf .)',
    'cd / || cd /tmp/x && rm -rf ./*', "cd / || eval 'cd /tmp/x'; rm -rf .",
    'cd /tmp/x || cd /; rm -rf ./*', 'cat | cd /; rm -rf ./*',
    '(cd /; rm -rf ./*)', "eval 'cd /' && rm -rf ./*",
    "bash -c 'cd / && rm -rf *'", "ssh host 'cd ~ && rm -rf .*'",
    'cd / && echo "$(rm -rf ./*)"; cd /tmp/x', 'cd / && echo `rm -rf .`',
    'cd /tmp/x && echo "$(cd / && rm -rf *)"',
    'cd / && (cd /tmp/x && rm -rf ./*); rm -rf ./*',
    'rm -rf "${VAR?}/"', 'rm -rf "${VAR?must be set}"/*', 'rm -rf ${A?}${B?}/',
    # cd - returns to an earlier folder, which could be any of them.
    'cd /tmp/x; cd -; rm -rf ./*',
    # A cd whose target is only known when it runs leaves the folder unknown
    # (TOP), and eval is not on the narrowing whitelist.
    'cd "$TMPDIR/foo" && rm -rf ./*', 'cd "${VAR:?}" && rm -rf ./*',
    'cd "${VAR:?set VAR}" && rm -rf .*', "cd / && eval 'cd /tmp/x' && rm -rf ./*",
] + ['cd ' + root + ' && rm -rf ' + target
     for root in ('/', '~', '/usr', '/home/pi', '/Users/jacobr')
     for target in ('*', '.', './', './*', '.*', '..')]
CD_ALLOWED = [
    'cd /tmp/x && rm -rf *', 'cd "$HOME/scratch" && rm -rf ./*',
    'cd ~/scratch && rm -rf ./*', 'cd /tmp/x && rm -rf .',
    'cd /tmp/x && rm -rf ..', 'cd /usr && rm -rf scratch',
    'cd / && rm -rf /tmp/x', 'cd /tmp/x && cd nested && rm -rf ./*',
    '(cd /); rm -rf ./*', 'cd /tmp/x && (cd /); rm -rf ./*',
    'cd /tmp/x; bash -c "cd /"; rm -rf ./*',
    'cd / | cat; rm -rf ./*', 'cd / & rm -rf ./*',
    'echo "$(cd /)"; rm -rf ./*', 'rm -rf ./*; cd /',
    'cd /tmp/x && rm -rf "$VAR"', 'rm -rf "${VAR:?}/"',
    'rm -rf "${VAR:?not empty}/*"', 'rm -rf ${A:?}${B?}/',
    'cd / && cd /tmp/x && rm -rf ./*', "ssh host 'cd /'; rm -rf ./*",
    # A local cd never moves the remote shell (which starts in its home).
    "cd / && ssh host 'cd /tmp/x && rm -rf ./*'",
    # The rm runs only when cd failed, in a directory the guard cannot know.
    'cd /usr || rm -rf .*', 'cd /tmp && cd /usr || rm -rf *',
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
    # A cd target only known when it runs leaves the folder unknown (TOP).
    'cd "${DIR:?}" && find . -delete', 'cd "$TMPDIR/foo" && find ./* -delete',
] + ['find ' + root + ' -delete' for root in SYSTEM_PATHS]
MAC_ALLOWED = [
    'find ./x -delete', 'find /tmp/x -delete', 'find ~/scratch -delete',
    'find /tmp/x -exec rm -rf {} \\;', 'find /usr -print', 'find / -name "-delete"',
    'find / -path "-delete" -print', 'find / -printf "-delete"',
    'find / -fprintf /tmp/output "-delete"', 'find / -exec echo -delete {} \\;',
    'find / -exec echo rm -rf {} \\;', 'find / -execdir printf "%s" rm {} +',
    'find "${DIR:?}/" -delete', 'cd /tmp/x && find . -delete',
    '(cd /); find . -delete', 'find . -delete; cd /',
    'cd / && find . -name "-delete" -print', 'cd / && find . -exec echo rm {} \\;',
    'dd if=/dev/disk2 of=/tmp/backup.img', 'dd if=/tmp/input of=/tmp/output',
    'dd if=/tmp/input', 'diskutil list', 'diskutil info /dev/disk2',
    'diskutil apfs list', 'diskutil apfs listVolumeGroups', 'diskutil info eraseDisk',
    'echo "dd of=/dev/disk2"', 'echo "mkfs.ext4 /dev/disk2"',
    'grep -n "diskutil eraseDisk" file', "cat <<'EOF'\ndd of=/dev/disk2\nEOF",
]

# Regression cases from the 2026-11-12 audit (findings in the config repo's
# .agent/session-runtime-audit/bash-gate.md): (command, platforms, payload cwd, denied).
BOTH, MAC = ('linux', 'darwin'), ('darwin',)
EXTRA_MALFORMED_TAIL = [
    ("bash -c 'git push -f\nprintf \"unterminated'", BOTH, None, True),
    ('git push -f\nprintf "unterminated', BOTH, None, True),
    ('rm -rf /Users/jacobr\nprintf "unterminated', BOTH, None, True),
    ('echo ok\ncd / && rm -rf ./*\necho "unterminated', BOTH, None, True),
    ('git push --force; echo $(true', BOTH, None, True),
    # The statement holding the error is dropped whole, quoted prose included.
    ("echo 'rm -rf /", BOTH, None, False),
    ('printf "unterminated\ngit push -f', BOTH, None, False),
    ('git status\ngit commit -m "stop git push -f', BOTH, None, False),
]
EXTRA_MIRROR_PUSH = [
    ('git push --mirror origin', BOTH, None, True),
    ('git push --mirror', BOTH, None, True),
    ('git -C repo push --mirror backup', BOTH, None, True),
    ('git push origin main', BOTH, None, False),
    ('git push origin feature/-force', BOTH, None, False),
    ('git clone --mirror https://example.invalid/repo.git', BOTH, None, False),
]
EXTRA_WRAPPERS = [(command, BOTH, None, True) for command in [
    'rtk git push --force-with-lease origin main', 'rtk proxy git push -f origin main',
    'rtk -v git push -f', "rtk run -c 'git push -f'", 'rtk err rm -rf /Users/jacobr',
    'rtk git push --mirror origin',
    'exec rm -rf /Users/jacobr', 'exec -a name rm -rf /', 'sudo -nu root rm -rf /Users/jacobr',
    'sudo -u root -E rm -rf ~', 'sudo --user=root rm -rf /', 'sudo -iu root git push -f',
    'nice -n 10 rm -rf /Users/jacobr', 'nice -10 git push -f', 'nohup git push -f &',
    'time -p rm -rf /', 'builtin command rm -rf /', "env -S 'rm -rf /Users/jacobr'",
    'env - rm -rf /', 'xargs -0 -n1 rm -rf /Users/jacobr', 'xargs -I{} rm -rf / {}',
    'until git push -f; do break; done', 'while rm -rf /Users/jacobr; do break; done',
    'while true; do git push -f; done', 'if git push -f; then :; fi',
    'if true; then :; elif git push -f; then :; fi', 'for x in a b; do git push -f; done',
    '! git push -f', '{ git push -f; }',
]] + [(command, BOTH, None, False) for command in [
    'rtk git push origin main', 'rtk git status', 'rtk grep -n "rm -rf /" file', 'rtk gain',
    'sudo -l rm -rf /', 'exec 3>&1', 'nice -n 10 make test',
    'while read -r line; do echo "$line"; done < /tmp/list', 'until false; do break; done',
    'env -C /tmp/build rm -rf ./*', 'time make', 'xargs -0 rm -f < /tmp/list',
]]
EXTRA_HEREDOCS = [(command, BOTH, None, True) for command in [
    "bash <<'EOF'\nrm -rf /Users/jacobr\nEOF\n", "cat <<'EOF' | bash\nrm -rf /Users/jacobr\nEOF\n",
    'cat <<EOF\n$(git push --force-with-lease)\nEOF\n', 'cat <<EOF\n$(rm -rf /Users/jacobr)\nEOF\n',
    'cat > notes <<EOF\nfoo `git push -f` bar\nEOF', 'sh -s <<EOF\ngit push -f\nEOF',
    "zsh <<'EOF'\ncd / && rm -rf ./*\nEOF", "sudo bash <<'EOF'\nrm -rf ~\nEOF",
    "bash -s -- arg <<'EOF'\ngit push -f\nEOF", "cat <<'EOF' | sed s/a/b/ | bash\ngit push -f\nEOF",
    "bash <<< 'rm -rf /'", "bash <<'EOF'\nrm -rf /", 'bash <<EOF\n\\$(git push -f)\nEOF',
    "bash -c 'cat <<EOF\n$(rm -rf /)\nEOF'", "ssh host <<'EOF'\nrm -rf ~\nEOF",
    "ssh host bash -s <<'EOF'\ngit push -f\nEOF", "source /dev/stdin <<'EOF'\ngit push -f\nEOF",
    "cat <<'EOF' | ssh host\nrm -rf ./*\nEOF", 'cat <<EOF\nPrice: $(5\nEOF\ngit push -f',
    # A shell running an unknown script could evaluate its stdin.
    "bash script.sh <<'EOF'\nrm -rf /\nEOF",
]] + [(command, BOTH, None, False) for command in [
    # A literal -c script or remote command that reads no code from stdin.
    "ssh host 'cat > notes' <<'EOF'\nrm -rf ~\nEOF", "bash -c 'echo hi' <<'EOF'\nrm -rf /\nEOF",
    "cat > /tmp/notes <<'EOF'\nrm -rf /Users/jacobr\ngit push --force\nEOF\n",
    'cat <<EOF\nNever run rm -rf / or git push --force.\nEOF',
    "git commit -F - <<'EOF'\nBlock git push --force and rm -rf ~\nEOF",
    "python3 - <<'EOF'\n# rm -rf /\nprint('git push --force')\nEOF",
    "cat <<'EOF'\n$(rm -rf /)\nEOF", 'cat <<EOF\n\\$(rm -rf /)\nEOF',
    "cat <<'EOF' | python3\nrm -rf /\nEOF", "tee notes.md <<'EOF' > /dev/null\n`git push -f`\nEOF",
    'cat <<EOF\nPrice: $(5\nEOF\necho done',
]]
EXTRA_CWD = [
    ('cd /Users/jacobr && false && cd /tmp/build; rm -rf *', BOTH, None, True),
    ('cd /Users/jacobr; cd /path/that/does/not/exist; rm -rf *', BOTH, None, True),
    ('cd /Users/jacobr; if false; then cd /tmp/build; fi; rm -rf *', BOTH, None, True),
    ('env -C /Users/jacobr rm -rf ./*', BOTH, None, True),
    ('env --chdir=/Users/jacobr rm -rf -- *', BOTH, None, True),
    ('sudo -D /Users/jacobr rm -rf -- *', BOTH, None, True),
    ('rm -rf ./*', BOTH, '/Users/jacobr', True),
    ('rm -rf ..', BOTH, '/Users/jacobr/config', True),
    ('rm -rf ../*', BOTH, '/home/pi/scripts', True),
    ('cd /tmp/x; rm -rf ./*', BOTH, '/Users/jacobr', True),
    ('cd /usr || rm -rf .*', BOTH, '/Users/jacobr', True),
    ('! cd /tmp/x && rm -rf ./*', BOTH, '/Users/jacobr', True),
    ('find . -delete', MAC, '/Users/jacobr', True),
    ('cd /tmp/x && rm -rf ./*', BOTH, '/Users/jacobr', False),
    ('cd /tmp/x &&\nrm -rf ./*', BOTH, '/Users/jacobr', False),
    ('if cd /tmp/x; then rm -rf ./*; fi', BOTH, '/Users/jacobr', False),
    ('rm -rf ./build', BOTH, '/Users/jacobr', False),
    ('rm -rf ./*', BOTH, '/Users/jacobr/config', False),
    ('(cd /Users/jacobr); rm -rf ./*', BOTH, '/tmp/x', False),
    ('rm -rf ./*', BOTH, 'relative/dir', False),
    ('env -C /tmp/build rm -rf ./*', BOTH, '/Users/jacobr', False),
]
EXTRA_PATHS = [(command, BOTH, None, True) for command in [
    'rm -rf /Users/jacobr/.*', 'rm -rf /Users/jacobr/.[!.]*', 'rm -rf /home/pi/.??*',
    'rm -rf ~/.*', 'rm -rf "$HOME/../"', 'rm -rf ~/../../usr', 'rm -rf ~/..',
    'rm -rf $HOME/../..', 'rm -rf /Users/jacobr/scratch/../.*',
]] + [('find /Users/jacobr/.* -delete', MAC, None, True)] + [
    (command, BOTH, None, False) for command in [
        'rm -rf ~/*.log', 'rm -rf ~/.cache/*', 'rm -rf ~/scratch/.*',
        'rm -rf /Users/jacobr/.git-template', 'rm -rf ~/scratch/../build',
    ]]
EXTRA_FIND = [(command, MAC, None, True) for command in [
    "find /Users/jacobr -exec sh -c 'rm -rf \"$1\"' _ {} \\;",
    'find /tmp/build -exec rm -rf /Users/jacobr {} +',
    "find /Users/jacobr -exec bash -c 'rm -f \"$@\"' _ {} +",
    "find ~ -exec sh -c 'rm -rf {}' \\;", 'find /tmp/build -exec git push -f \\;',
    'find /Users/jacobr -ok rm {} \\;', "find /tmp/x -exec sh -c 'cd / && rm -rf ./*' \\;",
    'find /Users/jacobr -name "*.pyc" -execdir rm {} +',
    'find /tmp/build -exec env -C /Users/jacobr rm -rf ./* \\;',
]] + [(command, MAC, None, False) for command in [
    "find /tmp/build -exec sh -c 'rm -rf \"$1\"' _ {} \\;", "find . -name '*.pyc' -exec rm {} +",
    'find /Users/jacobr -name x -print', 'find /Users/jacobr -exec grep -l "rm -rf" {} +',
    "find /Users/jacobr -exec sh -c 'echo rm -rf \"$1\"' _ {} \\;",
]] + [
    # Positional parameters of a -c script are bound statically, on both hosts.
    ("bash -c 'rm -rf -- \"$1\"' _ /Users/jacobr", BOTH, None, True),
    ("sh -c 'rm -rf \"$@\"' _ /tmp/x /Users/jacobr", BOTH, None, True),
    ("bash -c 'rm -rf -- \"$1\"' _ /tmp/build", BOTH, None, False),
]
EXTRA_QUOTED_GLOBS = [(command, BOTH, None, False) for command in [
    'rm -rf "/Users/jacobr/*"', 'rm -rf /Users/jacobr/\\*', "rm -rf '/Users/jacobr/.*'",
    'rm -rf "${VAR?must be set}/*"',
]] + [(command, BOTH, None, True) for command in [
    'rm -rf "/Users/jacobr"/*', 'rm -rf "$HOME"/.*', 'rm -rf "${VAR?must be set}"/*',
]]
EXTRA_SSH = [
    ("ssh host 'rm -rf ./*'", BOTH, None, True), ("ssh -p 22 host 'rm -rf ../*'", BOTH, None, True),
    ("ssh host 'rm -rf build'", BOTH, None, False), ("ssh host 'cd /tmp/x && rm -rf ./*'", BOTH, None, False),
]
# Second review (2026-11-13): where the guard cannot be sure what a shell does,
# it keeps the possibility that the dangerous command runs in the dangerous place.
HOME_CWD, REPO_CWD, BUILD_CWD = '/Users/jacobr', '/Users/jacobr/dev/app', '/tmp/build'
REVIEW_EXIT = [
    # set -e is not modelled: zsh ignores it inside Claude Code's `eval '...' &&` wrapper.
    ('(set -e); cd /path/does/not/exist; rm -rf *', BOTH, HOME_CWD, True),
    ('set -e; { cd /path/does/not/exist; } || true; rm -rf *', BOTH, HOME_CWD, True),
    ('set -e; cd /tmp/x; rm -rf ./*', BOTH, HOME_CWD, True),
    ('(set +e); cd /tmp/build && rm -rf ./*', BOTH, HOME_CWD, False),
    # zsh carries on after exit with two arguments or a non-numeric one.
    ('exit 1 extra; rm -rf *', BOTH, HOME_CWD, True),
    ('cd /tmp/build || exit "$code"; rm -rf ./*', BOTH, HOME_CWD, True),
    ('exit 1; rm -rf /', BOTH, None, True),
    ('f() { exit; }; rm -rf ./*', BOTH, HOME_CWD, True),
    ('while true; do cd /Users/jacobr; break; exit; done; rm -rf ./*', BOTH, BUILD_CWD, True),
    ('cd /tmp/build || exit 1; rm -rf ./*', BOTH, HOME_CWD, False),
    ('cd /tmp/build || { echo missing; exit 1; }; rm -rf ./*', BOTH, HOME_CWD, False),
]
REVIEW_STDIN = [
    ("bash -s -- /Users/jacobr <<'EOF'\nrm -rf \"$1\"\nEOF", BOTH, BUILD_CWD, True),
    ("cat <<'EOF' | bash -s -- /Users/jacobr\nrm -rf \"$1\"\nEOF", BOTH, BUILD_CWD, True),
    ("env -C /Users/jacobr bash <<'EOF'\nrm -rf ./*\nEOF", BOTH, BUILD_CWD, True),
    ("source /dev/stdin <<'EOF'\ncd /Users/jacobr\nEOF\nrm -rf ./*", BOTH, BUILD_CWD, True),
    ("env -C /tmp/build bash <<'EOF'\nrm -rf ./*\nEOF", BOTH, HOME_CWD, False),
    ('sudo -iu root rm -rf ./*', BOTH, BUILD_CWD, True),
    ('sudo -i rm -rf ./*', BOTH, BUILD_CWD, True),
    ('sudo -u root rm -rf ./build', BOTH, BUILD_CWD, False),
]
REVIEW_EXECDIR = [
    ("find /Users/jacobr -mindepth 1 -maxdepth 1 -type d -execdir sh -c 'rm -rf ./*' \\;",
     MAC, BUILD_CWD, True),
    ("find /Users -maxdepth 1 -okdir sh -c 'rm -rf ./*' \\;", MAC, BUILD_CWD, True),
    ("find /tmp/build -execdir sh -c 'rm -rf ./*' \\;", MAC, HOME_CWD, False),
    ("find /tmp/build -name '*.o' -execdir rm {} +", MAC, HOME_CWD, False),
]
REVIEW_NEGATION = [
    ('if ! cd /tmp/build; then exit 1; fi; rm -rf ./*', BOTH, HOME_CWD, False),
    ('if ! cd x; then exit 1; fi; rm -rf ./*', BOTH, HOME_CWD, False),
    ('if ! cd /tmp/build; then echo missing; fi; rm -rf ./*', BOTH, HOME_CWD, True),
    ('if cd /tmp/build; then :; else exit 1; fi; rm -rf ./*', BOTH, HOME_CWD, False),
    ('! cd /tmp/build || rm -rf ./*', BOTH, HOME_CWD, False),
]
REVIEW_POSITIONAL_GLOBS = [
    ("sh -c 'rm -rf $1' _ '/Users/jacobr/*'", BOTH, None, True),
    ("sh -c 'rm -rf $@' _ '/Users/jacobr/*'", BOTH, None, True),
    ("sh -c 'rm -rf \"$1\"' _ '/Users/jacobr/*'", BOTH, None, False),
    ("sh -c 'rm -rf \"$@\"' _ '/Users/jacobr/*'", BOTH, None, False),
]
# Any heredoc or here-string on a line a shell could read is a script, at any
# descriptor: zsh concatenates several inputs, and 0<&N duplicates one onto stdin.
REVIEW_DESCRIPTORS = [(command, BOTH, None, True) for command in [
    "bash <<'SCRIPT' 3<<'DATA'\necho hi\nSCRIPT\nrm -rf /Users/jacobr\nDATA",
    "bash <<'A' <<'B'\nrm -rf /Users/jacobr\nA\necho hi\nB",
    "bash <<'A' <<'B'\necho hi\nA\nrm -rf /Users/jacobr\nB",
    "cat > /tmp/out <<'EOF' | bash\nrm -rf /Users/jacobr\nEOF",
    "cat 3 <<'EOF' | bash\nrm -rf ~\nEOF", "bash 3<<'EOF'\nrm -rf /\nEOF",
    "bash 3<<'EOF' 0<&3\nrm -rf /Users/jacobr\nEOF", "cat 3<<'EOF' 0<&3\nrm -rf /Users/jacobr\nEOF",
]] + [(command, BOTH, None, False) for command in [
    "cat 3<<'EOF'\nrm -rf /Users/jacobr\nEOF", "cat > notes.md <<'EOF'\nrm -rf /Users/jacobr\nEOF",
]]
# Third review (2026-11-13): narrowing only by the whitelist in destructive_guard_flow.py.
REVIEW_FUNCTIONS = [
    ('f() { rm -rf "$1"; }; f /Users/jacobr', BOTH, None, True),
    ('f() { rm -rf "$@"; }; f /tmp/x /Users/jacobr', BOTH, None, True),
    ('f() { :; }; (f() { exit; }); f; rm -rf ./*', BOTH, HOME_CWD, True),
    ('f() { :; }; if false; then f() { exit; }; fi; f; rm -rf ./*', BOTH, HOME_CWD, True),
    ('if false; then cd() { :; }; fi; cd /Users/jacobr; rm -rf ./*', BOTH, BUILD_CWD, True),
    ('f() { rm -rf "$1"; }; f /tmp/build', BOTH, None, False),
]
REVIEW_REDIRECTED = [(command, BOTH, HOME_CWD, True) for command in [
    'exit 0 </path/does/not/exist; rm -rf ./*', 'true </path/does/not/exist || rm -rf ./*',
    '{ exit; } </path/does/not/exist; rm -rf ./*', 'f() { return 1 extra; rm -rf ./*; }; f',
    # bash and zsh carry on after exit's failed redirection; dash runs &> as a background job.
    'exit 0 999999999>/dev/null; rm -rf ./*', 'cd /tmp/build &>/dev/null || exit 1; rm -rf ./*',
]]
# Fourth review (2026-11-13): W1 only where the folder is certain, W3 only for the real exit.
REVIEW_CD_CERTAINTY = [(command, BOTH, BUILD_CWD, True) for command in [
    'CDPATH=/; cd usr && rm -rf ./*', 'export CDPATH=/\ncd usr && rm -rf ./*',
    'cd -P /tmp/link/.. && rm -rf ./*', 'setopt CHASE_LINKS; cd /tmp/link/.. && rm -rf ./*',
    'set -P; cd /tmp/link/.. && rm -rf ./*', 'set -o physical; cd /tmp/link/.. && rm -rf ./*',
]] + [(command, BOTH, HOME_CWD, False) for command in [
    'CDPATH=/; cd /tmp/build && rm -rf ./*', 'cd -P /tmp/build && rm -rf ./*',
]]
REVIEW_STACK = [(command, BOTH, HOME_CWD, True) for command in [
    'pushd /tmp/build; cd /tmp/build || exit 1; pushd +1; rm -rf ./*',
    'pushd /tmp/build && popd && rm -rf ./*', 'cd -2 && rm -rf ./*', 'cd +1 && rm -rf ./*',
    'pushd /tmp/build; pushd && rm -rf ./*',
]]
REVIEW_ALIASES = [(command, BOTH, HOME_CWD, True) for command in [
    "alias exit=':'\nexit\nrm -rf ./*", "alias cd=':'\ncd /tmp/build && rm -rf ./*",
    "alias 'cd=echo'; cd /tmp/build || exit 1; rm -rf ./*",
    'unalias -a; cd /tmp/build && rm -rf ./*', 'enable -n cd; cd /tmp/build && rm -rf ./*',
    'disable cd; cd /tmp/build && rm -rf ./*', 'cd() { :; }; cd /tmp/build && rm -rf ./*',
]] + [("alias ll='ls -l'; cd /tmp/build && rm -rf ./*", BOTH, HOME_CWD, False)]
# Fifth review: switches are executed commands, never words in prose or options.
REVIEW_SWITCH_TEXT = [(command, BOTH, cwd, False) for cwd in (HOME_CWD, REPO_CWD) for command in [
    './configure --disable-shared && cd /tmp/build && rm -rf ./*',
    "printf '%s\\n' 'disable cache'; cd /tmp/build && rm -rf ./*",
    "git commit -m 'disable flaky check' && cd /tmp/build && rm -rf ./*",
    "echo 'alias cd=:'; cd /tmp/build && rm -rf ./*",
]]
REVIEW_SWITCH_COMMANDS = [(command, BOTH, HOME_CWD, True) for command in [
    'disable cd; cd /tmp/build && rm -rf ./*', 'alias cd=:; cd /tmp/build && rm -rf ./*',
    'enable -n exit; cd /tmp/build || exit 1; rm -rf ./*',
    'alias "$name"=:; cd /tmp/build && rm -rf ./*', 'disable $x; cd /tmp/build && rm -rf ./*',
    'builtin alias cd=:; cd /tmp/build && rm -rf ./*', 'command unalias -a; cd /tmp/build && rm -rf ./*',
    'unset -f cd; cd /tmp/build && rm -rf ./*',
]] + [
    ("f() { bash; }; export -f f; bash -c f <<'EOF'\nrm -rf /\nEOF", BOTH, None, True),
    ('alias ll=ls; disable echo; cd /tmp/build && rm -rf ./*', BOTH, HOME_CWD, False),
    # Accepted false block: CDPATH is found by a text scan.
    ("echo 'CDPATH is unset'; cd build && rm -rf ./*", BOTH, HOME_CWD, True),
]
REVIEW_TRAP_END = [(command, BOTH, BUILD_CWD, True) for command in [
    "trap 'rm -rf ./*' EXIT; cd /Users/jacobr",
    "f() { trap 'rm -rf ./*' EXIT; cd /Users/jacobr; }; f",
    "source /dev/stdin <<'EOF'\ntrap 'rm -rf ./*' EXIT\ncd /Users/jacobr\nEOF",
]]
REVIEW_LITERAL_READERS = [(command, BOTH, None, True) for command in [
    "bash -c 'source /dev/stdin' <<'EOF'\nrm -rf /\nEOF",
    "bash -c 'eval \"$(cat)\"' <<'EOF'\nrm -rf /\nEOF",
    "bash -c '$cmd' <<'EOF'\nrm -rf /\nEOF", "ssh host 'bash -s' <<'EOF'\nrm -rf ~\nEOF",
    "ssh host <<'EOF'\nrm -rf ~\nEOF", "bash -c 'cat | sh' <<'EOF'\nrm -rf /\nEOF",
]] + [(command, BOTH, None, False) for command in [
    "bash -c 'cat > notes.md' <<'EOF'\nrm -rf /\nEOF", "ssh host 'tee notes' <<'EOF'\nrm -rf ~\nEOF",
]]
REVIEW_GROUP_READERS = [
    ("{ bash -s -- /Users/jacobr; } <<'EOF'\nrm -rf \"$1\"\nEOF", BOTH, None, True),
    ("{ source /dev/stdin; } <<'EOF'\ncd /Users/jacobr\nEOF\nrm -rf ./*", BOTH, REPO_CWD, True),
    ("{ env -C /tmp/build bash; } <<'EOF'\nrm -rf ./*\nEOF", BOTH, HOME_CWD, False),
    ("{ cat <<'EOF'; bash; }\nrm -rf /Users/jacobr\nEOF", BOTH, None, True),
]
REVIEW_UNKNOWN_FOLDERS = [
    ('cd "$(pwd)" && rm -rf ./*', BOTH, HOME_CWD, True),
    ('cd -; rm -rf */*', BOTH, REPO_CWD, True), ('cd -; rm -rf us?', BOTH, REPO_CWD, True),
    ('cd -; rm -rf d*', BOTH, REPO_CWD, True), ('cd -; rm -rf ./build', BOTH, REPO_CWD, False),
    ('rm -rf /U*', BOTH, None, True), ('rm -rf /*/jacobr', BOTH, None, True),
    ('rm -rf /tmp/foo*', BOTH, None, False), ('rm -rf ~/*.log', BOTH, None, False),
]
REVIEW_TRAPS = [
    ("trap 'rm -rf /Users/jacobr' EXIT; true", BOTH, None, True),
    ("trap 'rm -rf ./*' EXIT; cd /tmp/build", BOTH, HOME_CWD, True),
    ("trap -- 'git push -f' INT TERM", BOTH, None, True),
    ("tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT", BOTH, HOME_CWD, False),
    ('trap - EXIT', BOTH, HOME_CWD, False),
]
# Accepted false blocks (also in the scripts AGENTS.md entry): set -e is not
# modelled, and cd - and popd may return to any earlier folder.
REVIEW_KNOWN_FALSE_BLOCKS = [
    ('set -e; cd /tmp/x; rm -rf ./*', BOTH, HOME_CWD, True),
    ('cd /tmp/x; cd -; rm -rf ./*', BOTH, BUILD_CWD, True),
    ('pushd /tmp/x && popd; rm -rf ./*', BOTH, BUILD_CWD, True),
]
REVIEW3_IDIOMS = [(command, BOTH, cwd, False) for cwd in (HOME_CWD, REPO_CWD) for command in [
    'rm -rf node_modules', 'rm -rf ./build', 'cd build && rm -rf ./*',
    'cd build || exit 1; rm -rf ./*', 'if ! cd x; then exit 1; fi; rm -rf ./*',
    'for d in a b; do (cd "$d" && make clean); done',
    "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT", 'git worktree remove .claude/worktrees/x',
    'rm -rf "$TMPDIR"/x', "cat > notes.md <<'EOF'\nNever rm -rf / or git push --force.\nEOF",
    'git commit -m "guard against rm -rf ~ and git push --force"',
    'grep -rn "rm -rf /" docs', 'echo "git push -f is blocked"', 'git push origin main',
    'cd dir 2>/dev/null || exit 1; rm -rf ./*', 'cd /tmp/build >/dev/null 2>&1 || exit 1; rm -rf ./*',
]] + [("find . -name '*.pyc' -delete", MAC, REPO_CWD, False)]
REVIEW_PARAMETERS = [(command, BOTH, None, True) for command in [
    'rm -rf "${HOME:?}"', 'rm -rf "${HOME?}"', 'rm -rf ${HOME:-/tmp}', 'rm -rf "${ROOT:-}/"',
    'rm -rf "${ROOT-}/"', 'rm -rf "${ROOT:=}/"', 'rm -rf "${HOME:?HOME unset}/"',
]] + [(command, BOTH, None, False) for command in [
    'rm -rf "${DIR:-/tmp}/build"', 'rm -rf "${DIR:?}"', 'rm -rf "${HOME:+/tmp/x}"',
    'rm -rf "${DIR:?set DIR}/build"',
]]
REVIEW_CONTROL_FLOW = [
    ("f() { bash; }; f <<'EOF'\nrm -rf /\nEOF", BOTH, None, True),
    ("bash /dev/stdin <<'EOF'\nrm -rf /\nEOF", BOTH, None, True),
    ("coproc bash -c 'rm -rf /'", BOTH, None, True),
    ('f() { cd /Users/jacobr; return; }; f; rm -rf ./*', BOTH, BUILD_CWD, True),
    ('while cd /Users/jacobr; do break; done; rm -rf ./*', BOTH, BUILD_CWD, True),
    ('cd() { builtin cd /Users/jacobr; }; cd /tmp/x && rm -rf ./*', BOTH, BUILD_CWD, True),
    ('true | cd /Users/jacobr; rm -rf ./*', BOTH, BUILD_CWD, True),
    ('false && cd /tmp/x; rm -rf ./*', BOTH, HOME_CWD, True),
    ('sudo cd /tmp/x && rm -rf ./*', BOTH, HOME_CWD, True),
    ('[ -d /tmp/x ] && cd /tmp/x || exit 1; rm -rf ./*', BOTH, HOME_CWD, False),
    ('(cd /tmp/x; rm -rf ./*)', BOTH, HOME_CWD, True),
    ('for i in 1 2 3; do cd ..; done; rm -rf ./*', BOTH, REPO_CWD, True),
    ("{ bash; } <<'EOF'\nrm -rf /\nEOF", BOTH, None, True),
    # Shapes the tree parser does not model fall back to the grow-only flat reading.
    ('case x in a) cd /Users/jacobr ;& b) rm -rf ./* ;; esac', BOTH, BUILD_CWD, True),
    ('[[ ( -f x ) ]] && rm -rf ./build', BOTH, REPO_CWD, False),
    ('[[ ( -f x ) ]] && rm -rf /usr', BOTH, None, True),
]
REVIEW_FIND_START = [
    # Accepted find policy: from home itself the starting folder is protected.
    ("find . -name '*.pyc' -delete", MAC, HOME_CWD, True),
    ("find . -name '*.pyc' -delete", MAC, REPO_CWD, False),
]
REVIEW_IDIOMS = [(command, BOTH, cwd, False) for command, cwd in [
    ('cd build || exit 1; rm -rf ./*', REPO_CWD), ('cd build || exit 1; rm -rf ./*', HOME_CWD),
    ('set -e\ncd sub\nrm -rf dist', REPO_CWD), ('if ! cd x; then exit 1; fi; rm -rf ./*', HOME_CWD),
    ('rm -rf node_modules', REPO_CWD), ('rm -rf ./build', REPO_CWD),
    ("cat > notes.md <<'EOF'\nNever rm -rf / or git push --force.\nEOF", REPO_CWD),
    ('cd /tmp/x && make && cd .. && rm -rf x', HOME_CWD),
    ('for d in a b c; do (cd "$d" && make clean); done', REPO_CWD),
    ('while read -r f; do rm -f "$f"; done < list.txt', REPO_CWD),
    ('case "$1" in build) rm -rf ./build ;; *) echo no ;; esac', REPO_CWD),
]]
CD_CHAIN = '; '.join('cd d' + str(index) for index in range(22)) + '; git push -f'
DEEP_COMMANDS = [
    'echo ' + '$(' * 5000 + 'true' + ')' * 5000 + '; git push -f',
    'echo ' + '$(' * 1000 + 'true' + ')' * 1000,
    'echo "' + '${a:-' * 200 + 'x' + '}' * 200 + '"', 'eval ' * 40 + 'true',
]
SHALLOW_COMMANDS = [
    'echo "$(echo "$(echo "$(date)")")"', 'echo ' + '$(' * 20 + 'true' + ')' * 20,
    'eval eval eval true',
]


def claude_payload(command, cwd=None):
    payload = {'session_id': 'test', 'transcript_path': '/tmp/transcript.jsonl',
               'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
               'tool_input': {'command': command, 'description': 'test'}}
    if cwd is not None:
        payload['cwd'] = cwd
    return payload


def codex_payload(command, cwd=None):
    # Codex 0.167 sends its own session fields and normalises its shell tool to Bash.
    payload = {'session_id': 'test', 'turn_id': '1', 'transcript_path': None,
               'model': 'gpt-test', 'permission_mode': 'default',
               'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
               'tool_use_id': 'call_1', 'tool_input': {'command': command}}
    if cwd is not None:
        payload['cwd'] = cwd
    return payload


class DestructiveCommandGuardTests(unittest.TestCase):
    def run_hook(self, stdin, platform='linux', home=None):
        environment = dict(os.environ)
        # Each host's own home, unless a test sets one: the guard protects $HOME.
        environment['HOME'] = home or ('/Users/jacobr' if platform == 'darwin' else '/home/pi')
        if platform is None:
            environment.pop('DESTRUCTIVE_GUARD_PLATFORM', None)
        else:
            environment['DESTRUCTIVE_GUARD_PLATFORM'] = platform
        # Not -I: the hook runs as `python3 script.py`, which imports its sibling module.
        return subprocess.run([sys.executable, '-E', '-s', '-B', str(SCRIPT)], input=stdin,
                              capture_output=True, text=True, check=False, env=environment)

    def command_result(self, command, platform='linux', cwd=None, shape=claude_payload, home=None):
        return self.run_hook(json.dumps(shape(command, cwd)), platform, home)

    def check_denied(self, result):
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertTrue(result.stdout, 'expected deny output')
        decision = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(decision['hookEventName'], 'PreToolUse')
        self.assertEqual(decision['permissionDecision'], 'deny')
        self.assertIn('Blocked ', decision['permissionDecisionReason'])
        self.assertIn('If Jacob really wants this, ask him to run it himself '
                      '(in Claude: `! <command>`).', decision['permissionDecisionReason'])
        return decision['permissionDecisionReason']

    def check_allowed(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def check_cases(self, cases, shapes=(claude_payload, codex_payload), home=None):
        """Run (command, platforms, cwd, denied) cases through the hook, eight processes at a time."""
        jobs = [(command, platform, cwd, shape, denied) for command, platforms, cwd, denied in cases
                for platform in platforms for shape in shapes]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda job: self.command_result(
                job[0], job[1], cwd=job[2], shape=job[3], home=home), jobs))
        for (command, platform, cwd, shape, denied), result in zip(jobs, results):
            with self.subTest(command=command, platform=platform, cwd=cwd, shape=shape.__name__):
                (self.check_denied if denied else self.check_allowed)(result)

    def test_blocks_dangerous_commands(self):
        self.check_cases([(command, BOTH, None, True) for command in BLOCKED + CD_BLOCKED],
                         shapes=(claude_payload,))

    def test_passes_safe_commands_without_overriding_permissions(self):
        self.check_cases([(command, BOTH, None, False)
                          for command in ALLOWED + CD_ALLOWED + MAC_ALLOWED], shapes=(claude_payload,))

    def test_mac_rules_do_not_block_pi_disk_work(self):
        self.check_cases([(command, MAC, None, True) for command in MAC_BLOCKED] +
                         [(command, ('linux',), None, False) for command in MAC_BLOCKED],
                         shapes=(claude_payload,))

    def test_malformed_tail_still_checks_the_complete_statements_before_it(self):
        self.check_cases(EXTRA_MALFORMED_TAIL)

    def test_mirror_push_counts_as_forced(self):
        self.check_cases(EXTRA_MIRROR_PUSH)

    def test_wrappers_and_loop_conditions_are_inspected(self):
        self.check_cases(EXTRA_WRAPPERS)

    def test_heredocs_run_by_a_shell_are_scripts_and_documentation_stays_data(self):
        self.check_cases(EXTRA_HEREDOCS)

    def test_cwd_keeps_every_directory_a_command_may_run_in(self):
        self.check_cases(EXTRA_CWD)

    def test_home_and_dot_globs_match_their_equivalent_forms(self):
        self.check_cases(EXTRA_PATHS)

    def test_any_home_and_its_parents_stay_protected(self):
        self.check_cases([('rm -rf ~', BOTH, None, True), ('rm -rf ~/..', BOTH, None, True),
                          ('rm -rf "$HOME"/*', BOTH, None, True), ('rm -rf /Users/jacobr', BOTH, None, True),
                          ('rm -rf ~/scratch', BOTH, None, False)],
                         shapes=(claude_payload,), home='/private/tmp/guard-test-home')

    def test_find_exec_clauses_are_checked_as_commands(self):
        self.check_cases(EXTRA_FIND)

    def test_quoted_wildcards_name_one_literal_file(self):
        self.check_cases(EXTRA_QUOTED_GLOBS)

    def test_ssh_remote_commands_start_in_the_remote_home(self):
        self.check_cases(EXTRA_SSH)

    def test_exit_and_set_e_never_hide_a_reachable_deletion(self):
        self.check_cases(REVIEW_EXIT)

    def test_stdin_scripts_keep_their_arguments_folder_and_effects(self):
        self.check_cases(REVIEW_STDIN)

    def test_execdir_clauses_run_in_the_folder_of_each_match(self):
        self.check_cases(REVIEW_EXECDIR)

    def test_negation_swaps_success_and_failure(self):
        self.check_cases(REVIEW_NEGATION)

    def test_unquoted_positional_expansion_globs_its_value(self):
        self.check_cases(REVIEW_POSITIONAL_GLOBS)

    def test_heredocs_feed_their_own_descriptor_and_the_last_one_wins(self):
        self.check_cases(REVIEW_DESCRIPTORS)

    def test_parameter_expansions_with_certain_results_are_resolved(self):
        self.check_cases(REVIEW_PARAMETERS)

    def test_functions_loops_and_pipelines_keep_every_reachable_folder(self):
        self.check_cases(REVIEW_CONTROL_FLOW)

    def test_find_judges_its_starting_folder(self):
        self.check_cases(REVIEW_FIND_START)

    def test_common_idioms_still_pass(self):
        self.check_cases(REVIEW_IDIOMS)

    def test_functions_never_narrow_and_take_their_call_arguments(self):
        self.check_cases(REVIEW_FUNCTIONS)

    def test_a_redirection_takes_cd_exit_and_compounds_off_the_whitelist(self):
        self.check_cases(REVIEW_REDIRECTED)

    def test_heredocs_on_a_group_reach_the_readers_inside_it(self):
        self.check_cases(REVIEW_GROUP_READERS)

    def test_unknown_folders_are_judged_as_any_folder_globs_included(self):
        self.check_cases(REVIEW_UNKNOWN_FOLDERS)

    def test_trap_scripts_are_checked_from_every_later_folder(self):
        self.check_cases(REVIEW_TRAPS)

    def test_known_false_blocks_stay_denied(self):
        self.check_cases(REVIEW_KNOWN_FALSE_BLOCKS)

    def test_whitelist_idioms_pass_from_home_and_a_repo_root(self):
        self.check_cases(REVIEW3_IDIOMS)

    def test_cd_is_whitelisted_only_where_its_folder_is_certain(self):
        self.check_cases(REVIEW_CD_CERTAINTY)

    def test_directory_stack_moves_go_to_an_unknown_folder(self):
        self.check_cases(REVIEW_STACK)

    def test_aliases_and_builtin_switches_take_cd_and_exit_off_the_whitelist(self):
        self.check_cases(REVIEW_ALIASES)

    def test_switch_words_in_prose_or_options_change_nothing(self):
        self.check_cases(REVIEW_SWITCH_TEXT)

    def test_executed_switch_commands_remove_narrowing_for_the_names_they_touch(self):
        self.check_cases(REVIEW_SWITCH_COMMANDS)

    def test_traps_see_the_folder_the_last_command_leaves(self):
        self.check_cases(REVIEW_TRAP_END)

    def test_literal_scripts_that_read_no_code_from_stdin_keep_heredocs_as_data(self):
        self.check_cases(REVIEW_LITERAL_READERS)

    def test_the_fallback_reading_has_the_same_cap(self):
        for prefix in ('', '[[ ( -f x ) ]]; '):
            with self.subTest(prefix=prefix):
                reason = self.check_denied(self.command_result(prefix + ':; ' * 50002, 'darwin'))
                self.assertIn('too complex', reason)

    def test_folder_state_stays_bounded_and_push_is_denied(self):
        for count in (22, 400):
            command = '; '.join('cd d' + str(index) for index in range(count))
            for tail, denied in (('; git push -f', True), ('; rm -rf ./*', True),
                                 ('; rm -rf ./build', False)):
                with self.subTest(count=count, tail=tail):
                    started = time.monotonic()
                    result = self.command_result(command + tail, 'darwin', cwd=REPO_CWD)
                    self.assertLess(time.monotonic() - started, 3.0)
                    (self.check_denied if denied else self.check_allowed)(result)
        self.check_cases([(CD_CHAIN, BOTH, HOME_CWD, True)])

    def test_deep_nesting_is_denied_instead_of_failing_open(self):
        for command in DEEP_COMMANDS:
            for platform in BOTH:
                with self.subTest(command=command[:40], platform=platform):
                    reason = self.check_denied(self.command_result(command, platform))
                    self.assertIn('nested too deeply', reason)
        self.check_cases([(command, BOTH, None, False) for command in SHALLOW_COMMANDS])

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
