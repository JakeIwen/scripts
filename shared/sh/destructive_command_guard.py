#!/usr/bin/python3
"""Fail-open PreToolUse guard for catastrophic shell deletion, disks and pushes.

The shell reading lives in sibling modules deployed with it: destructive_guard_shell
(words), destructive_guard_tree (compound structure), destructive_guard_flow
(which folders each command may run in) and destructive_guard_paths (what a
target names). This file holds the rules and what each simple command does.
"""

from collections import namedtuple
from dataclasses import dataclass, field, replace
import json
import os
import re
import sys

from destructive_guard_flow import (
    ANY, FAIL, NOTHING, OK, QUIET, TOP, Flow, TooComplex, any_status)
from destructive_guard_paths import (
    DEFAULT_HOMES, deletion_target, execdir_folders, folder_after_cd, has_glob, home_directory)
from destructive_guard_shell import (
    MAX_DEPTH, Segment, Token, TooDeep, command_segments, parse_script, reads_duplicate)


ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z_0-9]*=')
HANDOFF = ('If Jacob really wants this, ask him to run it himself '
           '(in Claude: `! <command>`).')
RM_REASON = 'Blocked recursive rm of a protected root, home, system path or its relative contents. '
FIND_REASON = 'Blocked Mac destructive find on a protected path or its relative contents. '
TOO_DEEP = ('Blocked a command nested too deeply to check (over {} levels of substitutions '
            'or nested shells). '.format(MAX_DEPTH))
TOO_COMPLEX = 'Blocked a command too complex to check (too many commands to follow). '
# Reserved words the flat fallback reading still sees at the start of a command.
KEYWORDS = frozenset({'if', 'then', 'elif', 'else', 'do', 'while', 'until', '!', '{', '}'})
WRAPPER_ARGUMENTS = {
    'sudo': {'-u', '-g', '-h', '-p', '-C', '-T', '-R', '-D', '-U', '-r', '-t', '--user',
             '--group', '--host', '--prompt', '--chroot', '--chdir', '--close-from',
             '--other-user', '--role', '--type', '--command-timeout'},
    'env': {'-u', '--unset', '-C', '--chdir', '-S', '--split-string'},
    'command': set(),
    'builtin': set(),
    'coproc': set(),
    'exec': {'-a'},
    'nice': {'-n', '--adjustment'},
    'nohup': set(),
    'time': {'-f', '--format', '-o', '--output'},
    'timeout': {'-s', '--signal', '-k', '--kill-after'},
    'xargs': {'-n', '-P', '-L', '-I', '-s', '-E', '-a', '-d', '--max-args', '--max-procs',
              '--max-lines', '--max-chars', '--arg-file', '--delimiter', '--eof'},
}
# Wrappers that run the command in another process, so its cd or exit cannot
# touch this shell (command, builtin and the time keyword run it here).
EXTERNAL_WRAPPERS = frozenset({'sudo', 'env', 'exec', 'nice', 'nohup', 'timeout', 'xargs', 'rtk',
                               'coproc'})
# Options after which the wrapper prints, lists or edits instead of running.
WRAPPER_STOPS = {
    'command': {'-v', '-V'},
    'sudo': {'-l', '--list', '-v', '--validate', '-e', '--edit'},
}
WRAPPER_CHDIRS = {'sudo': {'-D', '--chdir'}, 'env': {'-C', '--chdir'}}
# rtk subcommands that run the command after them (rtk is the token-saving proxy).
RTK_RUNNERS = frozenset({'proxy', 'err', 'summary', 'test'})
SSH_ARGUMENTS = frozenset({
    '-B', '-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L', '-l',
    '-m', '-O', '-o', '-P', '-p', '-Q', '-R', '-S', '-W', '-w',
})
SHELLS = frozenset({'bash', 'sh', 'zsh', 'dash', 'ksh'})
SHELL_VALUE_OPTIONS = frozenset({'--rcfile', '--init-file'})
GIT_ARGUMENTS = frozenset({'-C', '-c', '--git-dir', '--work-tree', '--namespace',
                           '--config-env'})
# --mirror force-updates every remote ref and deletes the ones missing locally.
FORCE_FLAGS = frozenset({'--force', '--force-with-lease', '--force-if-includes', '--mirror'})
PUSH_ARGUMENTS = frozenset({'-o', '--push-option', '--repo', '--receive-pack', '--exec'})
FIND_ARGUMENTS = frozenset({
    '-name', '-iname', '-path', '-ipath', '-wholename', '-iwholename', '-regex',
    '-iregex', '-type', '-xtype', '-perm', '-user', '-group', '-uid', '-gid',
    '-size', '-links', '-inum', '-mtime', '-mmin', '-atime', '-amin', '-ctime',
    '-cmin', '-newer', '-newerXY', '-maxdepth', '-mindepth', '-fstype', '-flags',
    '-lname', '-ilname', '-printf', '-fprintf', '-fprint', '-fprint0', '-fls',
})
FIND_EXEC = frozenset({'-exec', '-execdir', '-ok', '-okdir'})
DISKUTIL_ACTIONS = frozenset({'zerodisk', 'randomdisk', 'secureerase', 'partitiondisk', 'reformat'})
APFS_ACTIONS = frozenset({'deletecontainer', 'deletevolume', 'erasevolume'})
# Commands whose effect is more than "ran somewhere, status unknown".
NOTABLE = frozenset({'rm', 'find', 'git', 'dd', 'diskutil', 'cd', 'pushd', 'popd', 'eval',
                     'ssh', 'source', '.', 'trap'}) | SHELLS
# Commands that could read a heredoc or here-string on the same line as a script.
READERS = frozenset({'source', '.', 'eval', 'ssh'}) | SHELLS
HOME_REFERENCE = re.compile(r'\$(?:HOME\b|\{HOME\})')
# Directory-stack moves (pushd +1, zsh's cd -2) rotate to an earlier folder.
STACK_MOVE = re.compile(r'[+-][0-9]+')
# Anywhere in the command text, these change what a plain cd does for the whole
# command: CDPATH redirects relative cds; physical mode (set -P, set -o physical,
# zsh CHASE_LINKS/CHASE_DOTS) resolves .. through symlinks. A text scan, so a
# command that merely mentions CDPATH (in a message, say) loses W1 for its
# relative cds: an accepted false block.
CDPATH_SETTING = re.compile(r'\bCDPATH\b|\bcdpath\b')
PHYSICAL_SETTING = re.compile(r'(?i)chase_?(?:links|dots)\b|-o\s+physical\b|\bset\b[^\n;|&]*\s-[A-Za-z]*P\b')
# Executed commands (not text) that can replace or restore cd, exit, pushd or
# popd: alias, unalias, zsh's disable, enable -n, unfunction and unset -f.
SWITCHED_NAMES = frozenset({'cd', 'exit', 'pushd', 'popd'})
SWITCH_COMMANDS = frozenset({'alias', 'unalias', 'disable', 'enable', 'unfunction', 'unset'})


@dataclass(frozen=True)
class Context:
    platform: str
    home: str
    depth: int = 0
    positional: tuple = ()
    # Inside find -exec: find walks the whole tree, so any rm there is recursive.
    find_exec: bool = False
    flow: object = None
    # Every body ever defined per function name (source text, re-read at each
    # call with its arguments), shared by every nested context of one command.
    functions: dict = field(default_factory=dict, compare=False)
    # Whole-command settings (see CDPATH_SETTING and PHYSICAL_SETTING above).
    cdpath: bool = False
    physical: bool = False
    # Names among SWITCHED_NAMES that an executed switch command in this script,
    # or an enclosing one, may have replaced: they get no W1 or W3 and move to TOP.
    switched: frozenset = frozenset()

    def nested(self, **changes):
        if self.depth >= MAX_DEPTH:
            raise TooDeep()
        return replace(self, depth=self.depth + 1, **changes)


# words after wrappers; chdir is the folder a wrapper runs it in (env -C,
# sudo -D, sudo -i's login home). A namedtuple: one is built for every command.
Command = namedtuple('Command', 'words chdir external', defaults=(None, False))


def command_name(text):
    # posixpath.basename, minus its overhead: called for every command scanned.
    return text[text.rfind('/') + 1:]


def parse_options(words, start, arguments):
    """Skip leading options; return (next index, [(flag, value token or None)])."""
    options = []
    while start < len(words):
        word = words[start]
        option = word.text
        if option == '--':
            return start + 1, options
        if not option.startswith('-') or option == '-':
            break
        start += 1
        if option.startswith('--'):
            flag, equals, value = option.partition('=')
            if equals:
                options.append((flag, Token(value, word.target.partition('=')[2])))
            elif flag in arguments:
                options.append((flag, words[start] if start < len(words) else None))
                start += 1
            else:
                options.append((flag, None))
            continue
        # Short flags combine (-nu root); a value-taking one ends the cluster.
        for offset in range(1, len(option)):
            flag = '-' + option[offset]
            if flag not in arguments:
                options.append((flag, None))
            elif offset + 1 < len(option):
                options.append((flag, Token(option[offset + 1:], word.target[offset + 1:])))
                break
            else:
                options.append((flag, words[start] if start < len(words) else None))
                start += 1
    return start, options


def unwrap_rtk(words):
    start, options = parse_options(words, 1, set())
    if any(flag in {'-h', '--help', '-V', '--version'} for flag, _ in options):
        return []
    words = words[start:]
    if words and words[0].text in RTK_RUNNERS:
        return words[1:]
    if words and words[0].text == 'run':
        # rtk run -c 'script' runs the script through sh -c.
        start, options = parse_options(words, 1, {'-c', '--command'})
        scripts = [value for flag, value in options if flag in {'-c', '--command'} and value]
        return [Token('sh'), Token('-c'), scripts[-1]] if scripts else words[start:]
    return words


def wrapper_folder(name, options, chdir):
    for flag, value in options:
        if flag in WRAPPER_CHDIRS.get(name, ()) and value is not None:
            chdir = value.target
    flags = {flag for flag, _ in options}
    if name == 'sudo' and flags & {'-i', '--login'}:
        # A login shell starts in the target user's home.
        users = [value.text for flag, value in options if flag in {'-u', '--user'} and value]
        chdir = '~' + (users[-1] if users else 'root')
    return chdir


def unwrap_command(words):
    chdir, external = None, False
    while words:
        text = words[0].text
        name = command_name(text)
        if name in KEYWORDS or ('=' in text and ASSIGNMENT.match(text)):
            words = words[1:]
            continue
        if name == 'rtk':
            words, external = unwrap_rtk(words), True
            continue
        if name not in WRAPPER_ARGUMENTS:
            break
        # The time keyword runs its command here; /usr/bin/time runs it as a child.
        external |= name in EXTERNAL_WRAPPERS or (name == 'time' and text != 'time')
        start, options = parse_options(words, 1, WRAPPER_ARGUMENTS[name])
        flags = {flag for flag, _ in options}
        if flags & ({'--help', '--version'} | WRAPPER_STOPS.get(name, set())):
            return Command([])
        chdir = wrapper_folder(name, options, chdir)
        if name == 'timeout':
            start += 1
        words = words[start:]
        if name == 'env':
            if words and words[0].text == '-':
                words = words[1:]
            split = [value for flag, value in options if flag in {'-S', '--split-string'} and value]
            if split:
                words = [token for token in parse_script(split[-1].text)
                         if not token.operator] + words
    return Command(words, chdir, external)


def cd_destination(words, cwd, context):
    """(folder cd, pushd or popd would move to, whether that folder is certain for W1).

    Unknown, so TOP: popd, pushd with no operand, any +N or -N stack move,
    a target holding an expansion other than ~ or $HOME, a glob, -, an extra
    argument (zsh's cd OLD NEW), a relative target while the command sets
    CDPATH, and .. under -P or physical mode, which resolve symlinks first.
    """
    name = command_name(words[0].text)
    if name == 'popd' or name in context.switched or any(STACK_MOVE.fullmatch(word.text) for word in words[1:]):
        return TOP, False
    start, options = parse_options(words, 1, set())
    if start >= len(words):
        return (TOP, False) if name == 'pushd' else (context.home, True)
    word = words[start]
    target = word.target
    certain = (len(words) == start + 1 and target not in {'', '-'} and
               not re.search(r'[$`]', HOME_REFERENCE.sub('', word.text)) and not has_glob(target))
    if certain and context.cdpath and not target.startswith(('/', '~')):
        certain = False
    if certain and ('..' in target.split('/') and
                    (context.physical or any(flag == '-P' for flag, _ in options))):
        certain = False
    if not certain:
        return TOP, False
    return folder_after_cd(target, cwd, context.home), True


def rm_rule(words, cwd, context):
    recursive, options, targets = context.find_exec, True, []
    for word in words[1:]:
        if options and word.text == '--':
            options = False
        elif options and word.text.startswith('-'):
            if word.text in {'--help', '--version'}:
                return None
            recursive |= (word.text == '--recursive' or
                          (not word.text.startswith('--') and bool(re.search('[rR]', word.text[1:]))))
        else:
            targets.extend(word.targets)
    if recursive and any(deletion_target(target, cwd, context.home) for target in targets):
        return RM_REASON + HANDOFF
    return None


def git_rule(words):
    start, options = parse_options(words, 1, GIT_ARGUMENTS)
    if any(flag in {'--help', '--version'} for flag, _ in options):
        return None
    if start >= len(words) or words[start].text != 'push':
        return None
    options = True
    index = start + 1
    while index < len(words):
        word = words[index].text
        flag = word.split('=', 1)[0]
        short_option = word.startswith('-') and not word.startswith('--')
        # In -o<push-option>, everything after o is data, not short flags.
        short_flags = word[1:].split('o', 1)[0]
        if word == '--':
            options = False
        elif word.startswith('+') or (options and (
                flag in FORCE_FLAGS or (short_option and 'f' in short_flags))):
            return 'Blocked forced git push (force option, --mirror or +refspec). ' + HANDOFF
        if options and (word in PUSH_ARGUMENTS or
                        (short_option and word.find('o') == len(word) - 1)):
            index += 1
        index += 1
    return None


def find_roots(words):
    roots = []
    index = 1
    while index < len(words):
        word = words[index]
        if word.text == '-f' and index + 1 < len(words):
            roots.append(words[index + 1])
            index += 2
            continue
        if word.text == '--':
            index += 1
        elif word.text in {'-H', '-L', '-P', '-E', '-X', '-s', '-x', '-d'}:
            index += 1
        elif word.text == '-D':
            index += 2
        elif word.text.startswith('-O'):
            index += 1
        elif word.text.startswith('-') or word.text in {'(', '!'}:
            break
        else:
            roots.append(word)
            index += 1
    return roots or [Token('.', '.')], index


def find_actions(words, start):
    """Return (whether find deletes itself, its (predicate, command words) clauses)."""
    deletes, clauses = False, []
    index = start
    while index < len(words):
        predicate = words[index].text
        if predicate == '-delete':
            deletes = True
        if predicate in FIND_EXEC:
            end = index + 1
            while end < len(words) and words[end].text not in {';', '+'}:
                end += 1
            clauses.append((predicate, words[index + 1:end]))
            index = end + 1
        elif predicate == '-fprintf':
            index += 3
        elif predicate in FIND_ARGUMENTS or predicate.startswith('-newer'):
            index += 2
        else:
            index += 1
    return deletes, clauses


def find_rule(words, cwd, context):
    roots, expression = find_roots(words)
    deletes, clauses = find_actions(words, expression)
    # The starting folder counts too: from home itself, find . -name '*.pyc'
    # -delete is denied (home is protected) while the same from a repo passes.
    if deletes and any(deletion_target(root.target, cwd, context.home) for root in roots):
        return FIND_REASON + HANDOFF
    # Each clause is a command of its own; {} stands for the roots and every
    # path under them, so substituting a root covers the paths find can pass.
    inner = context.nested(find_exec=True)
    for predicate, clause in clauses:
        for root in roots:
            words = [Token(word.text.replace('{}', root.text), word.target.replace('{}', root.target))
                     for word in clause]
            folders = (execdir_folders(root.target, cwd, context.home)
                       if predicate in {'-execdir', '-okdir'} else (cwd,))
            running = frozenset((folder, ANY) for folder in folders)
            reason, _ = run_simple(Segment(words=words), inner, running, QUIET)
            if reason:
                return reason
    return None


def mac_disk_rule(words):
    name = command_name(words[0].text)
    if name.startswith(('mkfs', 'newfs')):
        return 'Blocked Mac filesystem creation command. ' + HANDOFF
    if name == 'dd':
        for word in words[1:]:
            key, separator, value = word.target.partition('=')
            if key == 'of' and separator and value.startswith('/dev/'):
                return 'Blocked Mac dd output to a device under /dev. ' + HANDOFF
    if name == 'diskutil':
        start, _ = parse_options(words, 1, set())
        if start < len(words):
            action = words[start].text.lower()
            destructive = action.startswith('erase') or action in DISKUTIL_ACTIONS
            if action == 'apfs' and start + 1 < len(words):
                destructive |= words[start + 1].text.lower() in APFS_ACTIONS
            if destructive:
                return 'Blocked destructive Mac diskutil operation. ' + HANDOFF
    return None


def shell_invocation(words):
    """('script', text, positional) for sh -c, ('stdin', None, positional) for a
    shell reading commands from stdin, None for a script file or help."""
    index, command_flag, stdin_flag = 1, False, False
    while index < len(words):
        option = words[index].text
        if option in {'--help', '--version'}:
            return None
        if option in {'--', '-'}:
            stdin_flag |= option == '-'
            index += 1
            break
        if not option.startswith(('-', '+')):
            break
        if option.startswith('--'):
            index += 2 if option in SHELL_VALUE_OPTIONS else 1
            continue
        command_flag |= option.startswith('-') and 'c' in option[1:]
        stdin_flag |= 's' in option[1:]
        index += 2 if option.endswith(('o', 'O')) else 1
    operands = words[index:]
    if command_flag:
        return ('script', operands[0].text, tuple(word.target for word in operands[1:])) if operands else None
    if stdin_flag or not operands:
        return 'stdin', None, (words[0].text,) + tuple(word.target for word in operands)
    if operands[0].text in {'/dev/stdin', '/dev/fd/0'}:
        return 'stdin', None, tuple(word.target for word in operands)  # The script file is stdin.
    return None


def ssh_remote(words):
    """The remote command text of ssh, '' for none, None without a host."""
    start, _ = parse_options(words, 1, SSH_ARGUMENTS)
    return ' '.join(word.text for word in words[start + 1:]) if start < len(words) else None


def stdin_reader(segment, words, positional=(), functions=None):
    """(kind, positional) when a heredoc on this command's line could run as a
    script through it: 'shell' (its own process), 'here' (this shell: source,
    eval; its effects stay) or 'remote' (ssh). Static arguments of a shell
    reading stdin (bash -s -- ARGS) become the script's $1..."""
    name = command_name(words[0].text) if words else ''
    if name in SHELLS:
        invocation = shell_invocation(words)
        if invocation and invocation[0] == 'script' and not script_reads_stdin(invocation[1], functions):
            return None  # bash -c 'echo hi': stdin is data to that script.
        return 'shell', invocation[2] if invocation and invocation[0] == 'stdin' else ()
    if name in {'source', '.'}:
        # source's arguments become $1...; with none, the caller's stay.
        arguments = tuple(word.target for word in words[2:])
        return 'here', ((positional[:1] or ('',)) + arguments) if arguments else positional
    if name == 'eval':
        return 'here', positional
    if name == 'ssh':
        remote = ssh_remote(words) or ''
        if remote.strip() and not script_reads_stdin(remote):
            return None  # ssh host 'cat > notes': stdin is data to the remote command.
        first = next(command_segments(parse_script(remote)), None)
        inner = unwrap_command(first.words).words if first else []
        reader = stdin_reader(first, inner) if inner and command_name(inner[0].text) in SHELLS else None
        return 'remote', reader[1] if reader else ()
    if reads_duplicate(segment.redirects):
        return 'shell', ()  # 0<&N: another descriptor, maybe a heredoc's, becomes stdin.
    return None


def script_reads_stdin(text, functions=None, seen=frozenset()):
    """Whether a literal script (bash -c's, ssh's remote command) could run its
    stdin as code: a shell, source, eval, ssh or 0<&N in it, a command whose
    name is only known when it runs, or a function defined in this command
    whose body does (bash -c f with f exported). A function defined outside
    the command (exported before it ran) is not visible: a known limit."""
    return tokens_read_stdin(parse_script(text), functions or {}, seen)


def tokens_read_stdin(tokens, functions, seen):
    for segment in command_segments(tokens):
        words = unwrap_command(segment.words).words
        name = command_name(words[0].text) if words else ''
        if reads_duplicate(segment.redirects) or name in READERS or (
                words and re.search(r'[$`]', words[0].text)):
            return True
        if name in functions and name not in seen and any(
                not isinstance(body, str) or script_reads_stdin(body, functions, seen | {name})
                for body in functions[name]):
            return True
        if any(tokens_read_stdin(substitution, functions, seen) for substitution in segment.substitutions):
            return True
    return False


def nested_script(words, context):
    """(script, positional, where) for a command that runs a script from its arguments."""
    name = command_name(words[0].text)
    if name == 'eval':
        return ' '.join(word.text for word in words[1:]), context.positional, 'eval'
    if name in SHELLS:
        invocation = shell_invocation(words)
        if invocation and invocation[0] == 'script':
            return invocation[1], invocation[2], 'local'
    if name == 'ssh':
        remote = ssh_remote(words)
        if remote and remote.strip():
            return remote, (), 'remote'
    return None


def matched_rule(words, context, cwds):
    name = command_name(words[0].text)
    if name == 'git':
        return git_rule(words)
    if name == 'rm' or (context.platform == 'darwin' and name == 'find'):
        rule = rm_rule if name == 'rm' else find_rule
        for cwd in cwds:
            reason = rule(words, cwd, context)
            if reason:
                return reason
        return None
    return mac_disk_rule(words) if context.platform == 'darwin' else None


def remote_states(context):
    # A remote login shell starts in its home; ~ expands to this host's home,
    # which sits at the same depth on the Mac and the Pi.
    return frozenset({(context.home, ANY)})


def silent(redirect):
    """Output to /dev/null, or a dup onto stdout or stderr: if it fails, the
    command does not run and the shell stays put, which is a failed cd's branch."""
    _, operator, word = redirect
    return (operator in {'>', '>>', '>|'} and word.text == '/dev/null') or (
        operator == '>&' and word.text in {'1', '2'})


def plain(segment, command, name, silenced=False):
    """A plain simple command, as W1 and W3 require: no wrapper or assignment,
    and no redirection (W1 also takes the silent ones)."""
    redirects = not segment.redirects or (silenced and all(silent(item) for item in segment.redirects))
    return (redirects and not command.external and segment.words[0] is command.words[0]
            and command.words[0].text == name)


def run_scripts(segment, command, context, cwds, scope, after):
    """Check scripts the command runs (eval, sh -c, ssh, heredocs on its line);
    return (reason, paths after). What eval and source run only adds folders."""
    words, flow = command.words, context.flow
    start = frozenset((cwd, ANY) for cwd in cwds)
    here = not command.external
    nested = nested_script(words, context)
    if nested:
        script, positional, where = nested
        reason, result = flow.script(script, context.nested(positional=positional),
                                     remote_states(context) if where == 'remote' else start,
                                     exits=where != 'eval')
        if reason:
            return reason, after
        if where == 'eval' and here:
            after = after | any_status(result)
    reader = stdin_reader(segment, words, context.positional, context.functions) if scope.texts else None
    if reader:
        kind, positional = reader
        for text in scope.texts:
            reason, result = flow.script(text, context.nested(positional=positional),
                                         remote_states(context) if kind == 'remote' else start,
                                         exits=kind == 'shell')
            if reason:
                return reason, after
            if kind == 'here' and here:
                after = after | any_status(result)  # source and eval keep the script's cd.
    return None, after


def register_trap(words, context, running):
    start, _ = parse_options(words, 1, set())
    arguments = words[start:]
    if len(arguments) >= 2 and arguments[0].text not in {'', '-'}:
        context.flow.add_trap(arguments[0].text, context, running)


def call_functions(name, words, context, running, scope):
    """Run every body ever defined for name; the paths after only grow."""
    positional = (context.positional[:1] or ('',)) + tuple(word.target for word in words[1:])
    out = NOTHING
    for body in context.functions[name]:
        reason, result = context.flow.call(body, context.nested(positional=positional), running, scope.texts)
        if reason:
            return reason, NOTHING
        out |= result
    return None, out


def moved_folders(command, context, running, whitelisted, shadowed):
    moved = set()
    for cwd, _ in running:
        # A function of this name may run instead, and move anywhere.
        destination, certain = (TOP, False) if shadowed else cd_destination(command.words, cwd, context)
        if whitelisted and certain:
            moved.update({(destination, OK), (cwd, FAIL)})  # W1
        else:
            moved.update({(destination, ANY), (cwd, ANY)})
    return frozenset(moved)


def run_simple(segment, context, running, scope):
    """One simple command run on these paths: (reason, paths after it)."""
    command = unwrap_command(segment.words)
    words = command.words
    after = any_status(running)
    if not words:
        return None, after
    name = command_name(words[0].text)
    # A function of this name may or may not be the one that runs (a conditional
    # or subshell definition), so its bodies add paths and nothing narrows.
    function = not command.external and name == words[0].text and name in context.functions
    function_out = NOTHING
    if function:
        reason, function_out = call_functions(name, words, context, running, scope)
        if reason:
            return reason, NOTHING
    elif name == 'exit' and scope.exits and 'exit' not in context.switched and plain(segment, command, 'exit') and (
            len(words) == 1 or (len(words) == 2 and words[1].text.isdigit())):
        return None, NOTHING  # W3
    reads = bool(scope.texts) and reads_duplicate(segment.redirects)
    if name not in NOTABLE and not name.startswith(('mkfs', 'newfs')) and not reads:
        return None, after | function_out
    # After a W3 exit no path may remain; check anyway, with the folder unknown,
    # so absolute targets are judged whatever happens.
    cwds = tuple(dict.fromkeys(cwd for cwd, _ in running)) or (None,)
    if command.chdir is not None:
        cwds = tuple(dict.fromkeys(folder_after_cd(command.chdir, cwd, context.home) for cwd in cwds))
    reason = matched_rule(words, context, cwds)
    if not reason:
        reason, after = run_scripts(segment, command, context, cwds, scope, after)
    if reason:
        return reason, NOTHING
    if name == 'trap' and not command.external:
        register_trap(words, context, running)
    if name in {'cd', 'pushd', 'popd'} and not command.external and command.chdir is None:
        whitelisted = name == 'cd' and not function and plain(segment, command, 'cd', silenced=True)
        after = moved_folders(command, context, running, whitelisted, function)
    return None, (any_status(after | function_out) if function else after)


def switch_targets(words):
    """Which of SWITCHED_NAMES one executed switch command may change; all of
    them when an argument is only known at run time or a pattern is used."""
    arguments = words[1:]
    if any(re.search(r'[$`]', word.text) for word in arguments):
        return SWITCHED_NAMES
    letters = ''.join(word.text[1:] for word in arguments if word.text.startswith('-'))
    operands = {word.text for word in arguments if not word.text.startswith('-')}
    name = words[0].text
    if name == 'alias':
        return {operand.split('=', 1)[0] for operand in operands if '=' in operand} & SWITCHED_NAMES
    if (name == 'unalias' and 'a' in letters) or (name in {'disable', 'unfunction'} and 'm' in letters):
        return SWITCHED_NAMES
    if name in {'unalias', 'disable', 'unfunction'} or (name == 'enable' and 'n' in letters) or (
            name == 'unset' and 'f' in letters):
        return operands & SWITCHED_NAMES
    return frozenset()


def with_switches(tokens, context):
    """The context for a script, with the names its executed switch commands
    may change: keyed on command words, so prose and options never count."""
    if not any(not token.operator and token.text in SWITCH_COMMANDS for token in tokens):
        return context
    names = set()
    for segment in command_segments(tokens):
        words = unwrap_command(segment.words).words  # builtin and command reach the same switch.
        if words and words[0].text in SWITCH_COMMANDS:
            names |= switch_targets(words)
    return replace(context, switched=context.switched | names) if names - context.switched else context


def payload_cwd(payload):
    cwd = payload.get('cwd')
    if isinstance(cwd, str) and cwd.startswith('/') and '\x00' not in cwd:
        return os.path.normpath('/' + cwd.lstrip('/'))
    return None


def command_rule(command, platform, home=None, cwd=None):
    flow = Flow(run_simple, with_switches)
    context = Context(
        platform, home or DEFAULT_HOMES.get(platform, '/'), flow=flow,
        # A CDPATH the shell already exports counts as one the command sets.
        cdpath=bool(CDPATH_SETTING.search(command) or os.environ.get('CDPATH')),
        physical=bool(PHYSICAL_SETTING.search(command)))
    try:
        return flow.script(command, context, frozenset({(cwd, ANY)}))[0] or flow.fire_traps()
    except (TooDeep, RecursionError):
        return TOO_DEEP + HANDOFF
    except TooComplex:
        return TOO_COMPLEX + HANDOFF


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get('hook_event_name') != 'PreToolUse':
            return 0
        if payload.get('tool_name') != 'Bash' or not isinstance(payload.get('tool_input'), dict):
            return 0
        command = payload['tool_input'].get('command')
        if not isinstance(command, str) or not command.strip():
            return 0
        # Test-only override exercises Mac/Pi policy without either host's disks.
        platform = os.environ.get('DESTRUCTIVE_GUARD_PLATFORM', sys.platform)
        if platform not in {'darwin', 'linux'}:
            platform = sys.platform
        reason = command_rule(command, platform, home_directory(platform), payload_cwd(payload))
        if reason:
            json.dump({'hookSpecificOutput': {
                'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }}, sys.stdout, separators=(',', ':'))
    except Exception:
        # Malformed JSON and internal errors fail open with no regex fallback:
        # matching prose in an incomplete quote would block safe work. Nesting
        # too deep or too much to check is the exception, denied in command_rule.
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
