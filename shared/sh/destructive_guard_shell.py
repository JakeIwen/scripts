"""Shell reader for destructive_command_guard.py.

Exports ShellWords (the words, operators, command substitutions and heredocs
of a shell script), parse_script, Segment (one simple command with its
redirections), command_segments (the flat reading), add_word, redirect_texts,
reads_duplicate and the markers the guard's rules compare targets against. Deployed beside the
guard: shared/sh/ on the Mac, the flat /home/pi/scripts/ on the Pi.
"""

from dataclasses import dataclass, field
import re


VARIABLE = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
BODY_SPECIAL = re.compile(r'[\\$`]')
# A run of unquoted characters with no special meaning: read in one step, since
# the hook runs before every Bash call and long commands are mostly such runs.
PLAIN = re.compile(r'[^ \t\r\n;&|()<>\'"\\$`]+')
WORD_END = frozenset(' \t\r\n;&|()<>')
OPAQUE = '\x00dynamic\x00'
# Quoted or escaped ~ and glob characters name one literal file, never an
# expansion, so their targets must not match home or "directory contents".
LITERAL = '\x00literal\x00'
LITERAL_SENSITIVE = frozenset('~*?[')
POSITIONAL_ALL = '\x00positional\x00'
POSITIONAL_QUOTED = '\x00quoted-positional\x00'
MARKED_GLOB = re.compile(re.escape(LITERAL) + r'([*?\[])')
UNMARKED_GLOB = re.compile(r'(?<!' + re.escape(LITERAL) + r')([*?\[])')
PARAMETER_FORM = re.compile(r'([A-Za-z_][A-Za-z_0-9]*)(:?)([-=?+])(.*)', re.S)
# Real commands nest a handful of levels. Past this the guard denies instead of
# recursing into Python's stack limit, whose error used to fail open.
MAX_DEPTH = 32
BOUNDARIES = frozenset({'&&', '||', ';', ';;', '|', '|&', '&', '\n', '(', ')'})
PIPES = frozenset({'|', '|&'})
# &> and &>> redirect stdout and stderr in bash and zsh; dash (the Pi's sh) reads
# them as a background job, so the guard never trusts a command carrying one.
REDIRECTIONS = frozenset({'<', '>', '>>', '<<', '<<-', '<<<', '<>', '>&', '<&', '>|', '&>', '&>>'})
OPERATORS = frozenset({'<<-', '<<<', '&>>', '&&', '||', ';;', '|&', '>>', '<<', '<>', '>&', '<&', '>|',
                       '&>'})


class TooDeep(Exception):
    """Nesting past MAX_DEPTH: the guard denies rather than guess."""


class Heredoc:
    """A heredoc body, filled in once the reader passes the line that opened it."""

    def __init__(self, quoted):
        self.quoted = quoted
        self.body = ''
        self.substitutions = ()

    def script(self):
        """The text a shell reading this heredoc as stdin would run."""
        if self.quoted:
            return self.body
        # An unquoted body loses the backslash before $, ` and \ when expanded.
        return re.sub(r'\\([$`\\])|\\\n', lambda match: match.group(1) or '', self.body)


@dataclass(frozen=True)
class Token:
    text: str
    target: str = ''
    operator: bool = False
    substitutions: tuple = ()
    heredoc: object = None
    alternatives: tuple = ()
    fd: object = None  # The descriptor a redirection operator applies to.
    start: int = -1  # Source offsets, so a function body can be re-read at each call.
    end: int = -1

    @property
    def targets(self):
        """Every path this word can name: "$@" names one per positional argument."""
        return self.alternatives or (self.target,)


@dataclass
class Segment:
    """One simple command: its words, the substitutions it runs, its
    redirections as (descriptor, operator, word), the operator after it."""
    words: list = field(default_factory=list)
    substitutions: list = field(default_factory=list)
    redirects: list = field(default_factory=list)
    boundary: object = None
    # Process substitutions <(...) and >(...), parsed command trees.
    processes: list = field(default_factory=list)


def redirect_texts(redirects):
    """The text of every heredoc and here-string, at any descriptor.

    Which one a shell reads is not decided here: zsh concatenates several
    inputs, and a descriptor can be duplicated onto stdin (0<&3).
    """
    texts = []
    for _, operator, word in redirects:
        if operator in {'<<', '<<-'} and word.heredoc is not None:
            texts.append(word.heredoc.script())
        elif operator == '<<<':
            texts.append(word.text)
    return texts


def reads_duplicate(redirects):
    """Whether stdin is duplicated from another descriptor (0<&3)."""
    return any(fd == 0 and operator == '<&' for fd, operator, _ in redirects)


def literal_target(char):
    return LITERAL + char if char in LITERAL_SENSITIVE else char


def expansion_target(value, quoted):
    """A positional value as it lands where $1 or $@ is expanded.

    Quoting is per expansion: '/x/*' passed quoted is literal in transit, but an
    unquoted $1 then globs it, while "$1" keeps every character literal.
    """
    if quoted:
        return UNMARKED_GLOB.sub(LITERAL + r'\1', value)
    return MARKED_GLOB.sub(r'\1', value)


class ShellWords:
    """Read command words without treating quoted prose or heredoc data as code."""

    def __init__(self, source, depth=0, positional=()):
        self.source = source
        self.position = 0
        self.depth = depth
        # $0, $1, ... of the script being read; unset ones expand empty.
        self.positional = tuple(positional)
        self.substitutions = []
        self.parsed, self.complete = [], 0

    def tokens(self, end=None):
        result, heredocs = [], []
        if end is None:
            self.parsed = result
        delimiter = None
        parentheses, word_end = 0, -1
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
                glued, start = self.position == word_end, self.position
                operator = self.read_operator()
                if operator == '(':
                    parentheses += 1
                elif operator == ')':
                    parentheses -= 1
                fd = None
                if operator in REDIRECTIONS:
                    # 3<<EOF feeds descriptor 3; "3 <<EOF" passes 3 as an argument.
                    if glued and result and not result[-1].operator and result[-1].text.isdigit():
                        fd = int(result.pop().text)
                    else:
                        fd = 0 if operator.startswith('<') else 1
                result.append(Token(operator, operator=True, fd=fd, start=start, end=self.position))
                if operator in {'<<', '<<-'}:
                    delimiter = operator
                if operator == '\n':
                    self.read_heredocs(heredocs)
                    heredocs = []
                if end is None and operator in BOUNDARIES:
                    self.complete = len(result)
            else:
                start = self.position
                word = self.read_word(end)
                word_end = self.position
                if delimiter:
                    # A heredoc delimiter undergoes quote removal, not execution;
                    # any quoting in it makes the body literal text.
                    raw = self.source[start:self.position]
                    heredoc = Heredoc(quoted=any(mark in raw for mark in '\'"\\'))
                    word = Token(word.text, word.target, heredoc=heredoc, start=start, end=word_end)
                    heredocs.append((word.text, delimiter == '<<-', heredoc))
                    delimiter = None
                result.append(word)
        if end or delimiter:
            raise ValueError('unterminated shell construct')
        return result

    def read_operator(self):
        for width in (3, 2):
            operator = self.source[self.position:self.position + width]
            if operator in OPERATORS:
                self.position += width
                return operator
        self.position += 1
        return self.source[self.position - 1]

    def read_heredocs(self, heredocs):
        # Bodies stay data here. The guard runs one as a script only when a
        # shell reads it as stdin, and scans the substitutions an unquoted body
        # expands; documentation heredocs that mention rm -rf / keep passing.
        for delimiter, strip_tabs, heredoc in heredocs:
            lines = []
            # A shell runs an unterminated heredoc up to end of input, with a warning.
            while self.position < len(self.source):
                finish = self.source.find('\n', self.position)
                finish = len(self.source) if finish < 0 else finish + 1
                line = self.source[self.position:finish].rstrip('\n')
                self.position = finish
                if strip_tabs:
                    line = line.lstrip('\t')
                if line == delimiter:
                    break
                lines.append(line + '\n')
            heredoc.body = ''.join(lines)
            if not heredoc.quoted:
                heredoc.substitutions = self.body_substitutions(heredoc.body)

    def body_substitutions(self, body):
        reader = ShellWords(body, self.depth, self.positional)
        try:
            while True:
                special = BODY_SPECIAL.search(body, reader.position)
                if not special:
                    break
                reader.position = special.start()
                if special.group() == '\\':
                    reader.position += 2
                elif special.group() == '$':
                    reader.read_dollar()
                else:
                    reader.read_substitution('`', 1)
        except ValueError:
            pass  # The shell reports a broken expansion when it runs; later commands still run.
        return tuple(reader.substitutions)

    def read_word(self, end):
        start = self.position
        plain = PLAIN.match(self.source, self.position)
        if plain and (plain.end() == len(self.source) or self.source[plain.end()] in WORD_END):
            self.position = plain.end()
            return Token(plain.group(), plain.group(), start=start, end=self.position)
        text, target = [], []
        before = len(self.substitutions)
        while self.position < len(self.source):
            char = self.source[self.position]
            if char in ' \t\r\n;&|()<>' or char == end:
                break
            plain = PLAIN.match(self.source, self.position)
            if plain:
                self.position = plain.end()
                text.append(plain.group())
                target.append(plain.group())
                continue
            value, expanded = self.read_fragment()
            text.append(value)
            target.append(expanded)
        substitutions = tuple(self.substitutions[before:])
        del self.substitutions[before:]
        target = ''.join(target)
        alternatives = ()
        if POSITIONAL_ALL in target or POSITIONAL_QUOTED in target:
            values = self.positional[1:] or ('',)
            alternatives = tuple(target.replace(POSITIONAL_ALL, expansion_target(value, False))
                                 .replace(POSITIONAL_QUOTED, expansion_target(value, True))
                                 for value in values)
            target = alternatives[0]
        return Token(''.join(text), target, substitutions=substitutions, alternatives=alternatives,
                     start=start, end=self.position)

    def read_fragment(self, double_quoted=False):
        char = self.source[self.position]
        if char in "'\"" and not double_quoted:
            return self.read_quote(char)
        if char == '\\':
            return self.read_escape(double_quoted)
        if char == '$':
            return self.read_dollar(double_quoted)
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

    def read_dollar(self, quoted=False):
        start = self.position
        if self.source.startswith('$(', start):
            return self.read_substitution(')', 2)
        if self.source.startswith('${', start):
            return self.read_parameter(quoted)
        following = self.source[start + 1:start + 2]
        if following and following in '0123456789@*':
            self.position = start + 2
            return self.source[start:self.position], self.positional_target(following, quoted)
        match = VARIABLE.match(self.source, start + 1)
        if match:
            self.position = match.end()
            return '$' + match.group(0), '~' if match.group(0) == 'HOME' else ''
        self.position += 1
        return '$', '$'

    def positional_target(self, name, quoted):
        if name in ('@', '*'):
            return POSITIONAL_QUOTED if quoted else POSITIONAL_ALL
        index = int(name)
        return expansion_target(self.positional[index], quoted) if index < len(self.positional) else ''

    def read_parameter(self, quoted=False):
        start = self.position
        self.position += 2
        self.enter()
        while self.position < len(self.source):
            if self.source[self.position] == '}':
                self.position += 1
                self.depth -= 1
                raw = self.source[start:self.position]
                return raw, self.parameter_target(raw[2:-1], quoted)
            self.read_fragment()
        raise ValueError('unterminated parameter expansion')

    def parameter_target(self, name, quoted):
        if VARIABLE.fullmatch(name):
            return '~' if name == 'HOME' else ''
        if re.fullmatch(r'[0-9]+|[@*]', name):
            return self.positional_target(name, quoted)
        form = PARAMETER_FORM.fullmatch(name)
        if not form:
            return OPAQUE
        variable, colon, operator, word = form.groups()
        if variable == 'HOME' and operator != '+':
            return '~'  # HOME is set and non-empty, so -, =, ? and their : forms all yield it.
        if operator in '-=' and not word:
            return ''  # An empty default leaves an unset or empty variable empty.
        if operator == '?' and not colon:
            return ''  # ${VAR?} rejects unset values, but permits an empty value.
        # A real default, ${VAR:?} and ${VAR:+...} are not assumed empty:
        # this narrow guard must not reinterpret their shell semantics.
        return OPAQUE

    def read_substitution(self, end, prefix):
        start = self.position
        self.position += prefix
        self.enter()
        self.substitutions.append(self.tokens(end))
        self.depth -= 1
        return self.source[start:self.position], OPAQUE

    def enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise TooDeep()


def parse_script(source, depth=0, positional=()):
    """Tokens of the leading statements that parse completely.

    A shell runs each complete line before it reaches a later syntax error, so
    a malformed tail must not hide the commands in front of it. The statement
    holding the error is dropped whole: its quoted prose is never scanned.
    """
    reader = ShellWords(source, depth, positional)
    try:
        return reader.tokens()
    except ValueError:
        return reader.parsed[:reader.complete]


def add_word(segment, token, redirect):
    """Add a word to a simple command: an argument, or the target of redirect."""
    segment.substitutions.extend(token.substitutions)
    if token.heredoc is not None:
        segment.substitutions.extend(token.heredoc.substitutions)
    if redirect is None:
        segment.words.append(token)
    else:
        segment.redirects.append((redirect.fd, redirect.text, token))


def command_segments(tokens):
    """Simple commands in order, ignoring compound structure (the fallback reading)."""
    segment = Segment()
    redirect = None
    for token in tokens:
        if token.operator and token.text in BOUNDARIES:
            segment.boundary = token.text
            yield segment
            segment, redirect = Segment(), None
        elif token.operator and token.text in REDIRECTIONS:
            redirect = token
        elif not token.operator:
            add_word(segment, token, redirect)
            redirect = None
    yield segment
