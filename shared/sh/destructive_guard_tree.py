"""Parse tree for destructive_command_guard.py: the compound structure of a script.

Exports parse_tree (tokens from destructive_guard_shell to a sequence), ParseError,
nodes_in (a walk over a statement) and the node classes AndOr, Pipeline and
Compound; simple commands stay
destructive_guard_shell.Segment records. A sequence is a list of
(AndOr, terminator) pairs. Deployed beside the guard: shared/sh/ on the Mac,
the flat /home/pi/scripts/ on the Pi.
"""

from destructive_guard_shell import MAX_DEPTH, REDIRECTIONS, Segment, TooDeep, add_word


COMPOUND_WORDS = frozenset({'{', 'if', 'while', 'until', 'for', 'select', 'case', 'function'})
STATEMENT_ENDS = frozenset({';', '\n', '&'})
PIPELINE_PREFIXES = frozenset({'!', 'time'})


class ParseError(Exception):
    """A shape this parser does not model; the guard falls back to its flat reading."""


class AndOr:
    __slots__ = ('pipelines', 'ops')

    def __init__(self, pipelines, ops):
        self.pipelines, self.ops = pipelines, ops


class Pipeline:
    __slots__ = ('negated', 'commands')

    def __init__(self, negated, commands):
        self.negated, self.commands = negated, commands


class Compound:
    """group, subshell, if, while, until, for, case or function.

    clauses: if's (condition, body) pairs; cond and body: a loop's; arms: case
    bodies; words: tokens whose substitutions run (for and case words); span:
    a function body's source offsets.
    """
    __slots__ = ('kind', 'body', 'clauses', 'otherwise', 'cond', 'arms', 'words', 'name',
                 'redirects', 'substitutions', 'span')

    def __init__(self, kind, body=None, clauses=(), otherwise=None, cond=None, arms=(), words=(),
                 name=None, span=None):
        self.kind, self.body, self.clauses, self.otherwise = kind, body, clauses, otherwise
        self.cond, self.arms, self.words, self.name, self.span = cond, arms, words, name, span
        self.redirects, self.substitutions = [], []


class TreeParser:
    def __init__(self, tokens):
        self.tokens, self.index, self.depth = tokens, 0, 0

    def peek(self, offset=0):
        index = self.index + offset
        return self.tokens[index] if index < len(self.tokens) else None

    @staticmethod
    def word(token, *texts):
        return token is not None and not token.operator and token.text in texts

    @staticmethod
    def op(token, *texts):
        return token is not None and token.operator and token.text in texts

    def expect_word(self, text):
        if not self.word(self.peek(), text):
            raise ParseError(text)
        self.index += 1

    def expect_op(self, text):
        if not self.op(self.peek(), text):
            raise ParseError(text)
        self.index += 1

    def skip_newlines(self):
        while self.op(self.peek(), '\n'):
            self.index += 1

    def sequence(self, ends=()):
        """Statements up to one of ends; each item's node is an AndOr, a Pipeline,
        or for the common single command just that Segment or Compound."""
        items, tokens = [], self.tokens
        while True:
            token = tokens[self.index] if self.index < len(tokens) else None
            if token is None or ending(token, ends):
                return items
            if token.operator and token.text in STATEMENT_ENDS:
                self.index += 1
                continue
            node = self.andor()
            token = tokens[self.index] if self.index < len(tokens) else None
            terminator = None
            if token is not None and token.operator and token.text in STATEMENT_ENDS:
                terminator = token.text
                self.index += 1
            elif not (token is None or ending(token, ends)):
                raise ParseError('statement runs on')
            items.append((node, terminator))

    def andor(self):
        first = self.pipeline()
        token = self.tokens[self.index] if self.index < len(self.tokens) else None
        if token is None or not token.operator or token.text not in ('&&', '||'):
            return first
        pipelines, ops = [first], []
        while self.op(self.peek(), '&&', '||'):
            ops.append(self.peek().text)
            self.index += 1
            self.skip_newlines()
            pipelines.append(self.pipeline())
        return AndOr([as_pipeline(node) for node in pipelines], ops)

    def pipeline(self):
        token = self.peek()
        if token is not None and not token.operator and token.text not in PIPELINE_PREFIXES:
            command = self.command()
            token = self.tokens[self.index] if self.index < len(self.tokens) else None
            if token is None or not token.operator or token.text not in ('|', '|&'):
                return command
            return self.rest_of_pipeline(False, command)
        negated = False
        while True:
            token, following = self.peek(), self.peek(1)
            if self.word(token, '!'):
                negated, self.index = not negated, self.index + 1
            elif self.word(token, 'time') and (self.op(following, '(') or
                                               self.word(following, *COMPOUND_WORDS)):
                self.index += 1  # The time keyword runs the compound in the current shell.
            else:
                break
        return self.rest_of_pipeline(negated, self.command())

    def rest_of_pipeline(self, negated, first):
        commands = [first]
        while self.op(self.peek(), '|', '|&'):
            self.index += 1
            self.skip_newlines()
            commands.append(self.command())
        return Pipeline(negated, commands)

    def command(self):
        token = self.peek()
        if token is None:
            raise ParseError('missing command')
        if token.operator and token.text != '(' or not token.operator and \
                token.text not in COMPOUND_WORDS and not self.op(self.peek(1), '('):
            return self.simple()
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise TooDeep()
        if self.op(token, '('):
            self.index += 1
            node = Compound('subshell', body=self.sequence())
            self.expect_op(')')
        elif self.word(token, '{'):
            self.index += 1
            node = Compound('group', body=self.sequence(('}',)))
            self.expect_word('}')
        elif self.word(token, 'if'):
            node = self.if_clause()
        elif self.word(token, 'while', 'until'):
            self.index += 1
            cond = self.sequence(('do',))
            self.expect_word('do')
            node = Compound(token.text, cond=cond, body=self.sequence(('done',)))
            self.expect_word('done')
        elif self.word(token, 'for', 'select'):
            node = self.for_clause()
        elif self.word(token, 'case'):
            node = self.case_clause()
        elif self.word(token, 'function'):
            self.index += 1
            node = self.function()
        elif not token.operator and self.op(self.peek(1), '(') and self.op(self.peek(2), ')'):
            node = self.function()
        else:
            self.depth -= 1
            return self.simple()
        self.redirections(node)
        self.depth -= 1
        return node

    def if_clause(self):
        self.index += 1
        clauses, otherwise = [], None
        while True:
            cond = self.sequence(('then',))
            self.expect_word('then')
            clauses.append((cond, self.sequence(('elif', 'else', 'fi'))))
            if self.word(self.peek(), 'elif'):
                self.index += 1
                continue
            if self.word(self.peek(), 'else'):
                self.index += 1
                otherwise = self.sequence(('fi',))
            self.expect_word('fi')
            return Compound('if', clauses=clauses, otherwise=otherwise)

    def for_clause(self):
        self.index += 1
        words = []
        if self.op(self.peek(), '('):
            nesting = 0  # for (( init; test; step ))
            while True:
                token = self.peek()
                if token is None:
                    raise ParseError('unterminated for')
                self.index += 1
                nesting += 1 if self.op(token, '(') else -1 if self.op(token, ')') else 0
                if not token.operator:
                    words.append(token)
                if nesting == 0:
                    break
        else:
            name = self.peek()
            if name is None or name.operator:
                raise ParseError('for name')
            self.index += 1
            self.skip_newlines()
            if self.word(self.peek(), 'in'):
                self.index += 1
                while self.peek() is not None and not self.op(self.peek(), ';', '\n'):
                    if self.peek().operator:
                        raise ParseError('for words')
                    words.append(self.peek())
                    self.index += 1
        while self.op(self.peek(), ';', '\n'):
            self.index += 1
        self.expect_word('do')
        node = Compound('for', body=self.sequence(('done',)), words=words)
        self.expect_word('done')
        return node

    def case_clause(self):
        self.index += 1
        subject = self.peek()
        if subject is None or subject.operator:
            raise ParseError('case word')
        self.index += 1
        self.skip_newlines()
        self.expect_word('in')
        words, arms = [subject], []
        while True:
            self.skip_newlines()
            if self.word(self.peek(), 'esac'):
                self.index += 1
                return Compound('case', arms=arms, words=words)
            if self.op(self.peek(), '('):
                self.index += 1
            while not self.op(self.peek(), ')'):
                token = self.peek()
                if token is None or (token.operator and token.text != '|'):
                    raise ParseError('case pattern')
                if not token.operator:
                    words.append(token)
                self.index += 1
            self.index += 1
            arms.append(self.sequence(('esac',)))
            if self.op(self.peek(), ';;') and not self.op(self.peek(1), '&'):
                self.index += 1
            elif not self.word(self.peek(), 'esac'):
                raise ParseError('case fallthrough')  # ;& and ;;& run the next arm too.

    def function(self):
        name = self.peek()
        if name is None or name.operator:
            raise ParseError('function name')
        self.index += 1
        if self.op(self.peek(), '('):
            self.index += 1
            self.expect_op(')')
        self.skip_newlines()
        first = self.peek()
        body = self.command()
        last = self.tokens[self.index - 1]
        return Compound('function', body=body, name=name.text,
                        span=(first.start, last.end) if first.start >= 0 and last.end >= 0 else None)

    def redirections(self, node):
        while self.op(self.peek(), *REDIRECTIONS):
            redirect = self.peek()
            self.index += 1
            target = self.peek()
            if target is None or target.operator:
                raise ParseError('redirection target')
            self.index += 1
            node.substitutions.extend(target.substitutions)
            node.redirects.append((redirect.fd, redirect.text, target))

    def simple(self):
        segment, redirect, tokens = Segment(), None, self.tokens
        while self.index < len(tokens):
            token = tokens[self.index]
            if not token.operator:
                self.index += 1
                add_word(segment, token, redirect)
                redirect = None
            elif redirect is None and token.text in REDIRECTIONS:
                self.index += 1
                redirect = token
                if self.op(self.peek(), '('):
                    # <(...) and >(...) run their list in a subshell; the word is a path.
                    self.index += 1
                    segment.processes.append(self.sequence())
                    self.expect_op(')')
                    redirect = None
            elif redirect is None and token.text == '(' and segment.words[-1:] and \
                    segment.words[-1].text.endswith('='):
                self.array_assignment(segment)
            else:
                break
        if redirect is not None or not (segment.words or segment.redirects or segment.processes):
            raise ParseError('empty command')
        return segment

    def array_assignment(self, segment):
        self.index += 1  # name=( words ... )
        while not self.op(self.peek(), ')'):
            token = self.peek()
            if token is None or (token.operator and token.text != '\n'):
                raise ParseError('array')
            segment.substitutions.extend(token.substitutions)
            self.index += 1
        self.index += 1


def ending(token, ends):
    if token.operator:
        return token.text in (')', ';;')
    return token.text in ends


def as_pipeline(node):
    return node if isinstance(node, Pipeline) else Pipeline(False, [node])


def nodes_in(node, deep=True):
    """Every Segment and Compound in a statement node, inside compounds too when deep."""
    if isinstance(node, AndOr):
        for pipeline in node.pipelines:
            yield from nodes_in(pipeline, deep)
    elif isinstance(node, Pipeline):
        for command in node.commands:
            yield from nodes_in(command, deep)
    elif node is not None:
        yield node
        if deep and isinstance(node, Compound):
            sequences = [node.body] if node.kind != 'function' else [[(node.body, None)]]
            sequences += [part for clause in node.clauses for part in clause]
            sequences += [node.otherwise, node.cond] + list(node.arms)
            for sequence in sequences:
                for statement, _ in sequence or ():
                    yield from nodes_in(statement, deep)


def parse_tree(tokens):
    parser = TreeParser(tokens)
    items = parser.sequence()
    if parser.index != len(tokens):
        raise ParseError('unbalanced')
    return items
