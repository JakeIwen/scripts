"""Reviewable, narrowly scoped daemon restart permission; never installs itself."""
import getpass
import re
import shlex

DESTINATION = '/etc/sudoers.d/mac-panel-daemons'
# sudoers treats the executable and argument regex separately.
ARGUMENT_PATTERN = r'^kickstart -k system/com\.jacobr\.[a-z0-9-]+$'
COMMAND_PATTERN = '/bin/launchctl ' + ARGUMENT_PATTERN


def rule(user=None):
    user = getpass.getuser() if user is None else user
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*', user):
        raise ValueError('Invalid sudoers user')
    return f'{user} ALL=(root) NOPASSWD: {COMMAND_PATTERN}'


def installer(user=None):
    # Only the argument string is a sudoers regex; a regex spanning the executable
    # and its arguments would not match sudo's separate command/path fields.
    return '''#!/bin/zsh
set -eux -o pipefail
sudo -v
( set +x; while kill -0 $$; do sudo -n true; sleep 50; done 2>/dev/null ) &
trap "kill $! 2>/dev/null" EXIT
keepalive=$!
umask 077
temporary=$(/usr/bin/mktemp)
trap '/bin/rm -f -- "$temporary"; kill "$keepalive" 2>/dev/null || true' EXIT
printf '%s\\n' RULE > "$temporary"
sudo /usr/sbin/visudo -cf "$temporary"
sudo /usr/bin/install -m 440 -o root -g wheel "$temporary" DESTINATION
'''.replace('RULE', shlex.quote(rule(user))).replace('DESTINATION', shlex.quote(DESTINATION))
