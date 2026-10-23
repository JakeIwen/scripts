#!/usr/bin/python3
"""Fail-open PreToolUse guard for catastrophic shell deletion, disks and pushes."""

from dataclasses import dataclass
import json
import os
import posixpath
import re
import sys


PROTECTED_PATHS = frozenset({
    '/', '~', '/Users/jacobr', '/home/pi', '/Users', '/home', '/root',
    '/System', '/Library', '/Applications', '/usr', '/etc', '/var', '/opt',
    '/bin', '/sbin', '/private', '/Volumes', '/boot', '/lib', '/srv',
    '/mnt', '/media', '/dev',
})
ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z_0-9]*=')
VARIABLE = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
OPAQUE = '\x00dynamic\x00'
HANDOFF = ('If Jacob really wants this, ask him to run it himself '
           '(in Claude: `! <command>`).')
BOUNDARIES = frozenset({'&&', '||', ';', ';;', '|', '|&', '&', '\n', '(', ')'})
REDIRECTIONS = frozenset({'<', '>', '>>', '<<', '<<-', '<<<', '<>', '>&', '<&', '>|'})
WRAPPER_ARGUMENTS = {
    'sudo': {'-u', '-g', '-h', '-p', '-C', '-T', '-R', '-D', '--user', '--group',
             '--host', '--prompt', '--chroot', '--chdir', '--close-from'},
    'env': {'-u', '--unset', '-C', '--chdir'},
    'command': set(),
    'nohup': set(),
    'time': {'-f', '--format', '-o', '--output'},
    'timeout': {'-s', '--signal', '-k', '--kill-after'},
    'xargs': {'-n', '-P', '-L', '-I', '-s', '-E', '-a', '--max-args', '--max-procs',
              '--max-lines', '--max-chars', '--arg-file'},
}
SSH_ARGUMENTS = frozenset({
    '-B', '-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L', '-l',
    '-m', '-O', '-o', '-P', '-p', '-Q', '-R', '-S', '-W', '-w',
})
GIT_ARGUMENTS = frozenset({'-C', '-c', '--git-dir', '--work-tree', '--namespace',
                           '--config-env'})
FORCE_FLAGS = frozenset({'--force', '--force-with-lease', '--force-if-includes'})
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
RELATIVE_CONTENTS = frozenset({'.', '..', '*', '.*', '../*', '../.*'})


@dataclass(frozen=True)
class Token:
    text: str
    target: str = ''
    operator: bool = False
    substitutions: tuple = ()


def literal_target(text):
    # Quoted/escaped tildes are filenames, not home-directory expansion.
    return text.replace('~', OPAQUE + '~')


class ShellWords:
    """Read command words without treating quoted prose or heredoc data as code."""

    def __init__(self, source):
        self.source = source
        self.position = 0
        self.substitutions = []

    def tokens(self, end=None):
        result, heredocs = [], []
        delimiter = None
        parentheses = 0
        while self.position < len(self.source):
            char = self.source[self.position]
            if char == end and parentheses == 0:
                self.position += 1
                return result
            if char in ' \t\r':
                self.position += 1
            elif char == '#':
                newline = self.source.find('\n', self.position)
                self.position = len(self.source) if newline < 0 else newline
            elif char in '\n;&|()<>':
                operator = self.read_operator()
                if operator == '(':
                    parentheses += 1
                elif operator == ')':
                    parentheses -= 1
                result.append(Token(operator, operator=True))
                if operator in {'<<', '<<-'}:
                    delimiter = operator
                if operator == '\n':
                    self.skip_heredocs(heredocs)
                    heredocs = []
            else:
                word = self.read_word(end)
                if delimiter:
                    # A heredoc delimiter undergoes quote removal, not execution.
                    word = Token(word.text, word.target)
                    heredocs.append((word.text, delimiter == '<<-'))
                    delimiter = None
                result.append(word)
        if end or heredocs or delimiter:
            raise ValueError('unterminated shell construct')
        return result

    def read_operator(self):
        for operator in ('<<-', '<<<', '&&', '||', ';;', '|&', '>>', '<<',
                         '<>', '>&', '<&', '>|'):
            if self.source.startswith(operator, self.position):
                self.position += len(operator)
                return operator
        self.position += 1
        return self.source[self.position - 1]

    def skip_heredocs(self, heredocs):
        # Even expanding heredocs are deliberately data, not scanned shell scripts.
        for delimiter, strip_tabs in heredocs:
            while self.position < len(self.source):
                finish = self.source.find('\n', self.position)
                finish = len(self.source) if finish < 0 else finish + 1
                line = self.source[self.position:finish].rstrip('\n')
                self.position = finish
                if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                    break
            else:
                raise ValueError('unterminated heredoc')

    def read_word(self, end):
        text, target = [], []
        before = len(self.substitutions)
        while self.position < len(self.source):
            char = self.source[self.position]
            if char in ' \t\r\n;&|()<>' or char == end:
                break
            value, expanded = self.read_fragment()
            text.append(value)
            target.append(expanded)
        substitutions = tuple(self.substitutions[before:])
        del self.substitutions[before:]
        return Token(''.join(text), ''.join(target), substitutions=substitutions)

    def read_fragment(self, double_quoted=False):
        char = self.source[self.position]
        if char in "'\"" and not double_quoted:
            return self.read_quote(char)
        if char == '\\':
            return self.read_escape(double_quoted)
        if char == '$':
            return self.read_dollar()
        if char == '`':
            return self.read_substitution('`', 1)
        self.position += 1
        return char, literal_target(char) if double_quoted else char

    def read_quote(self, quote):
        self.position += 1
        text, target = [], []
        while self.position < len(self.source):
            char = self.source[self.position]
            if char == quote:
                self.position += 1
                return ''.join(text), ''.join(target)
            if quote == "'":
                self.position += 1
                value, expanded = char, literal_target(char)
            else:
                value, expanded = self.read_fragment(double_quoted=True)
            text.append(value)
            target.append(expanded)
        raise ValueError('unterminated quote')

    def read_escape(self, double_quoted):
        self.position += 1
        if self.position == len(self.source):
            raise ValueError('unterminated escape')
        char = self.source[self.position]
        if double_quoted and char not in '$`"\\\n':
            return '\\', '\\'
        self.position += 1
        return ('', '') if char == '\n' else (char, literal_target(char))

    def read_dollar(self):
        start = self.position
        if self.source.startswith('$(', start):
            return self.read_substitution(')', 2)
        if self.source.startswith('${', start):
            return self.read_parameter()
        match = VARIABLE.match(self.source, start + 1)
        if match:
            self.position = match.end()
            return '$' + match.group(0), '~' if match.group(0) == 'HOME' else ''
        self.position += 1
        return '$', '$'

    def read_parameter(self):
        start = self.position
        self.position += 2
        while self.position < len(self.source):
            if self.source[self.position] == '}':
                self.position += 1
                raw = self.source[start:self.position]
                name = raw[2:-1]
                if VARIABLE.fullmatch(name):
                    return raw, '~' if name == 'HOME' else ''
                if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\?[^}]*', name):
                    # ${VAR?} rejects unset values, but permits an empty value.
                    return raw, ''
                # Colon-error/default/indirect expansions are not assumed empty:
                # this narrow guard must not reinterpret their shell semantics.
                return raw, OPAQUE
            self.read_fragment()
        raise ValueError('unterminated parameter expansion')

    def read_substitution(self, end, prefix):
        start = self.position
        self.position += prefix
        self.substitutions.append(self.tokens(end))
        return self.source[start:self.position], OPAQUE


def command_segments(tokens):
    words, substitutions = [], []
    redirect = False
    for token in tokens:
        if token.operator and token.text in BOUNDARIES:
            yield words, substitutions, token.text
            words, substitutions, redirect = [], [], False
        elif token.operator and token.text in REDIRECTIONS:
            if words and words[-1].text.isdigit():
                words.pop()
            redirect = True
        elif not token.operator:
            substitutions.extend(token.substitutions)
            if not redirect:
                words.append(token)
            redirect = False
    yield words, substitutions, None


def parse_options(words, start, arguments):
    options = []
    while start < len(words):
        option = words[start].text
        if option == '--':
            return start + 1, options
        if not option.startswith('-') or option == '-':
            break
        options.append(option)
        start += 2 if option in arguments else 1
    return start, options


def unwrap_command(words):
    while words:
        name = posixpath.basename(words[0].text)
        if ASSIGNMENT.match(words[0].text) or name in {'if', 'then', 'elif', 'else', 'do', '!', '{', '}'}:
            words = words[1:]
            continue
        if name not in WRAPPER_ARGUMENTS:
            break
        start, options = parse_options(words, 1, WRAPPER_ARGUMENTS[name])
        if any(option in {'--help', '--version'} for option in options):
            return []
        if name == 'command' and any(re.fullmatch(r'-[pvV]*[vV][pvV]*', option) for option in options):
            return []
        if name == 'sudo' and any(option in {'-l', '-ll', '--list', '-v', '--validate'} for option in options):
            return []
        if name == 'timeout':
            start += 1
        words = words[start:]
    return words


def protected_target(target):
    if not target:
        return False
    target = target.rstrip('/') or '/'
    if target.endswith('/*'):
        target = target[:-2] or '/'
    # Normalize lexical dot components only; remote paths and symlinks cannot
    # safely be resolved using this machine's filesystem.
    if target.startswith('/'):
        target = '/' + target.lstrip('/')
    return posixpath.normpath(target) in PROTECTED_PATHS


def deletion_target(target, cwd):
    if protected_target(target):
        return True
    if not target or not cwd or target.startswith(('/', '~')):
        return False
    relative = posixpath.normpath(target)
    if protected_target(cwd) and relative in RELATIVE_CONTENTS:
        return True
    return protected_target(posixpath.join(cwd, relative))


def change_directory(words, cwd):
    start, _ = parse_options(words, 1, set())
    target = words[start].target if start < len(words) else '~'
    if not target:
        return '~'
    if target == '-':
        return None
    if target.startswith(('/', '~')):
        return posixpath.normpath(target)
    return posixpath.normpath(posixpath.join(cwd, target)) if cwd else None


def rm_rule(words, cwd):
    recursive, options, targets = False, True, []
    for word in words[1:]:
        if options and word.text == '--':
            options = False
        elif options and word.text.startswith('-'):
            if word.text in {'--help', '--version'}:
                return None
            recursive |= (word.text == '--recursive' or
                          (not word.text.startswith('--') and bool(re.search('[rR]', word.text[1:]))))
        else:
            targets.append(word.target)
    if recursive and any(deletion_target(target, cwd) for target in targets):
        return 'Blocked recursive rm of a protected root, home, system path or its relative contents. ' + HANDOFF
    return None


def git_rule(words):
    start, options = parse_options(words, 1, GIT_ARGUMENTS)
    if any(option in {'--help', '--version'} for option in options):
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
            return 'Blocked forced git push (force option or +refspec). ' + HANDOFF
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
            roots.append(words[index + 1].target)
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
            roots.append(word.target)
            index += 1
    return roots or ['.'], index


def find_deletes(words, start):
    index = start
    while index < len(words):
        predicate = words[index].text
        if predicate == '-delete':
            return True
        if predicate in FIND_EXEC:
            end = index + 1
            while end < len(words) and words[end].text not in {';', '+'}:
                end += 1
            command = unwrap_command(words[index + 1:end])
            if (predicate in {'-exec', '-execdir'} and command and
                    posixpath.basename(command[0].text) == 'rm'):
                return True
            index = end + 1
        elif predicate == '-fprintf':
            index += 3
        elif predicate in FIND_ARGUMENTS or predicate.startswith('-newer'):
            index += 2
        else:
            index += 1
    return False


def find_rule(words, cwd):
    roots, expression = find_roots(words)
    if any(deletion_target(root, cwd) for root in roots) and find_deletes(words, expression):
        return 'Blocked Mac destructive find on a protected path or its relative contents. ' + HANDOFF
    return None


def mac_disk_rule(words):
    name = posixpath.basename(words[0].text)
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


def shell_command(words):
    index = 1
    while index < len(words):
        option = words[index].text
        if option in {'--', '--help', '--version'} or not option.startswith(('-', '+')):
            break
        if option.startswith('-') and not option.startswith('--') and 'c' in option[1:]:
            return words[index + 1].text if index + 1 < len(words) else None
        takes_value = option in {'--rcfile', '--init-file', '-o', '+o', '-O', '+O'}
        takes_value |= not option.startswith('--') and option.endswith(('o', 'O'))
        index += 2 if takes_value else 1
    return None


def nested_command(words):
    name = posixpath.basename(words[0].text)
    if name == 'eval':
        return ' '.join(word.text for word in words[1:])
    if name in {'bash', 'sh', 'zsh'}:
        return shell_command(words)
    if name == 'ssh':
        start, _ = parse_options(words, 1, SSH_ARGUMENTS)
        return ' '.join(word.text for word in words[start + 1:])
    return None


def matched_rule(words, platform, cwds):
    name = posixpath.basename(words[0].text)
    if name == 'git':
        return git_rule(words)
    if name == 'rm' or (platform == 'darwin' and name == 'find'):
        rule = rm_rule if name == 'rm' else find_rule
        for cwd in cwds:
            reason = rule(words, cwd)
            if reason:
                return reason
        return None
    return mac_disk_rule(words) if platform == 'darwin' else None


def scan_tokens(tokens, platform, cwds):
    stack = []
    previous = None
    for words, substitutions, boundary in command_segments(tokens):
        for substitution in substitutions:
            reason, _ = scan_tokens(substitution, platform, cwds)
            if reason:
                return reason, cwds
        words = unwrap_command(words)
        if words:
            name = posixpath.basename(words[0].text)
            reason = matched_rule(words, platform, cwds)
            if reason:
                return reason, cwds
            nested = nested_command(words)
            if nested:
                nested_cwds = (None,) if name == 'ssh' else cwds
                reason, nested_cwds = scan_tokens(ShellWords(nested).tokens(), platform, nested_cwds)
                if reason:
                    return reason, cwds
                if name == 'eval' and boundary not in {'|', '|&', '&'}:
                    cwds = tuple(dict.fromkeys(cwds + nested_cwds)) if previous == '||' else nested_cwds
            # A final pipeline builtin can retain cwd in zsh or bash lastpipe.
            if name == 'cd' and boundary not in {'|', '|&', '&'}:
                destinations = tuple(change_directory(words, cwd) for cwd in cwds)
                # An OR branch may skip this cd, retaining the previous directory.
                cwds = tuple(dict.fromkeys((cwds if previous == '||' else ()) + destinations))
        if boundary == '(':
            stack.append(cwds)
        elif boundary == ')' and stack:
            cwds = stack.pop()
        previous = boundary
    return None, cwds


def command_rule(command, platform):
    return scan_tokens(ShellWords(command).tokens(), platform, (None,))[0]


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
        reason = command_rule(command, platform)
        if reason:
            json.dump({'hookSpecificOutput': {
                'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }}, sys.stdout, separators=(',', ':'))
    except Exception:
        # Malformed shell/JSON and internal errors fail open with no regex
        # fallback: matching prose in an incomplete quote would block safe work.
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
