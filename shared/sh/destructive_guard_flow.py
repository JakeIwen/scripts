"""Control flow for destructive_command_guard.py: which folders a command may run in.

Exports Flow, which walks a destructive_guard_tree parse over a set of shell
paths, each a (working folder, last status) pair; Scope; WHITELIST; and the
state names and helpers the guard shares (OK, FAIL, ANY, TOP, NOTHING,
any_status). Deployed beside the guard: shared/sh/ on the Mac, the flat
/home/pi/scripts/ on the Pi.

Default reading: every command in the text may run, in every folder that could
be current there, and every folder a cd could reach stays possible afterwards.
The evaluator narrows that only in the shapes of WHITELIST below.
"""

from collections import namedtuple

from destructive_guard_shell import Segment, command_segments, parse_script, redirect_texts
from destructive_guard_tree import AndOr, Compound, ParseError, Pipeline, nodes_in, parse_tree


# Narrowing whitelist. Each entry behaves identically in bash, sh and zsh
# (probed 2026-11-13 with harmless cd/pwd/exit scripts); a reviewer can check
# each by itself. Nothing else removes a folder or ends a path.
#
# | Entry | Shape | What the guard concludes |
# | W1 | `cd DIR` as a plain simple command: first word `cd`, no wrapper or    | succeeded: the shell is in DIR (status OK); failed: still in the old    |
# |    | assignment; redirections only >/dev/null, 2>/dev/null, 2>&1 and the   | folder (status FAIL). So `cd DIR && X` runs X in DIR, `cd DIR || X`     |
# |    | like (a failed one leaves the old folder, as a failed cd does; not    | in the old folder, `if cd DIR; then A; else B; fi` A in DIR, B in old.  |
# |    | &>, which dash runs as a background job); DIR a single literal (or ~, |                                                                         |
# |    | $HOME) with no glob, not -, +N or -N; DIR absolute, or relative while |                                                                         |
# |    | nothing in the command or environment sets CDPATH; no .. in DIR under |                                                                         |
# |    | cd -P or physical mode (set -P, set -o physical, zsh CHASE_LINKS or   |                                                                         |
# |    | CHASE_DOTS); no function, alias, unalias -a, enable -n or disable in  |                                                                         |
# |    | the command that could replace cd. Every other cd, pushd and popd     |                                                                         |
# |    | whose folder is not certain moves the shell to TOP.                   |                                                                         |
# | W2 | `!` before a pipeline                                                 | swaps OK and FAIL, so `if ! cd DIR; then ...` and `! cd DIR && X` work. |
# | W3 | `exit` or `exit N` (one numeric argument) as a plain simple command   | the path ends there (cd x || exit 1; rm -rf ./* runs rm only in x).     |
# |    | with no redirection at all (bash and zsh carry on after a failed one) |                                                                         |
# |    | and no function or alias that could replace it, reached only through |                                                                         |
# |    | sequences, && and || lists, if conditions and branches, and { }       |                                                                         |
# |    | groups without redirections                                           |                                                                         |
# | W4 | `( ... )`                                                             | the folder after it is the folder before it.                            |
# | W5 | a pipeline stage other than the last                                  | does not change the folder after the pipeline.                          |
# | W6 | an and-or list ended by `&` (a background job)                        | does not change the folder after it.                                    |
#
# Known limit, which the guard cannot see: a path component that is a symlink
# into a protected folder. W1's DIR and every rm or find target are resolved
# lexically, so /tmp/link/.. is taken as /tmp.
#
# Known limit: an alias or builtin switch for cd or exit made inside `eval` or
# sourced text (eval "alias cd=':'", `. /dev/stdin` with a heredoc) does not
# reach the caller's later commands, which keep W1 and W3. It takes a
# deliberate override of cd or exit to hit; the earlier flat guard missed it too.
WHITELIST =('W1 plain literal cd', 'W2 negation', 'W3 plain exit', 'W4 subshell',
             'W5 earlier pipeline stage', 'W6 background job')

OK, FAIL, ANY = 'ok', 'fail', 'any'
# A folder no longer tracked: judged as possibly any folder, protected ones
# included, so widening never hides a deletion.
TOP = '\x00anywhere\x00'
STATE_CAP = 64
LOOP_ROUNDS = 6
STEP_LIMIT = 50000
NOTHING = frozenset()
WIDE = frozenset({(TOP, ANY)})
# A command no path reaches (after a W3 exit, or a W1 branch that cannot run)
# is still checked from an unknown folder, so absolute protected targets are judged.
UNREACHED = frozenset({(None, ANY)})
# exits: whether a W3 exit here ends the path; texts: heredoc and here-string
# texts a shell reader here could take as a script; source: the text the tokens
# came from, for re-reading function bodies.
Scope = namedtuple('Scope', 'exits texts source')
QUIET = Scope(False, (), '')


class TooComplex(Exception):
    """More simple-command evaluations than STEP_LIMIT: the guard denies."""


def any_status(states):
    if len(states) == 1:
        (cwd, status), = states
        return states if status == ANY else frozenset({(cwd, ANY)})
    if all(status == ANY for _, status in states):
        return states
    return frozenset((cwd, ANY) for cwd, _ in states)


def negated(states):
    swap = {OK: FAIL, FAIL: OK, ANY: ANY}
    return frozenset((cwd, swap[status]) for cwd, status in states)


def widen(states):
    # Relative cds after ; double the paths each time; past the cap one path
    # standing for every folder replaces them, so cost stays bounded.
    return WIDE if len(states) > STATE_CAP else states


def split(states, op):
    """(paths that run the command after op, paths that skip it). Only W1
    statuses (OK, FAIL) separate them; ANY paths go both ways."""
    if op not in {'&&', '||'}:
        return states, NOTHING
    needed, passed = (OK, FAIL) if op == '&&' else (FAIL, OK)
    running = frozenset(state for state in states if state[1] != passed)
    skipped = frozenset((cwd, passed) for cwd, status in states if status != needed)
    return running, skipped


def with_texts(scope, texts):
    return scope._replace(texts=tuple(dict.fromkeys(scope.texts + tuple(texts)))) if texts else scope


class Flow:
    def __init__(self, simple, prepare=None):
        # simple(segment, ctx, paths, scope) -> (reason, paths after);
        # prepare(tokens, ctx) -> the context a script runs with.
        self.simple, self.prepare, self.steps, self.traps = simple, prepare, 0, []

    def script(self, text, ctx, states, exits=True, texts=()):
        tokens = parse_script(text, ctx.depth, ctx.positional)
        return self.tokens(tokens, ctx, states, Scope(exits, tuple(texts), text))

    def tokens(self, tokens, ctx, states, scope):
        if self.prepare is not None:
            ctx = self.prepare(tokens, ctx)
        try:
            tree = parse_tree(tokens)
        except ParseError:
            return self.flat(tokens, ctx, states, scope)
        return self.sequence(tree, ctx, states, scope)

    def sequence(self, items, ctx, states, scope):
        for node, terminator in items:
            if not states:
                reason, _ = self.statement(node, ctx, UNREACHED, scope._replace(exits=False))
            elif terminator == '&':
                reason, _ = self.statement(node, ctx, states, scope._replace(exits=False))
                states = any_status(states)  # W6
            else:
                reason, states = self.statement(node, ctx, states, scope)
            if reason:
                return reason, NOTHING
        return None, states

    def statement(self, node, ctx, states, scope):
        scope = with_texts(scope, [text for part in nodes_in(node, deep=False)
                                   for text in redirect_texts(part.redirects)])
        if isinstance(node, AndOr):
            return self.andor(node, ctx, states, scope)
        if isinstance(node, Pipeline):
            return self.pipeline(node, ctx, states, scope)
        reason, out = self.command(node, ctx, states, scope)
        return reason, widen(out)

    def andor(self, node, ctx, states, scope):
        reason, states = self.pipeline(node.pipelines[0], ctx, states, scope)
        for op, pipeline in zip(node.ops, node.pipelines[1:]):
            if reason:
                break
            running, skipped = split(states, op)
            if running:
                reason, out = self.pipeline(pipeline, ctx, running, scope)
            else:
                (reason, _), out = self.pipeline(pipeline, ctx, UNREACHED, scope._replace(exits=False)), NOTHING
            states = widen(out | skipped)
        return reason, states

    def pipeline(self, node, ctx, states, scope):
        commands = node.commands
        if len(commands) == 1:
            reason, out = self.command(commands[0], ctx, states, scope)
        else:
            inner = scope._replace(exits=False)
            for command in commands:
                reason, out = self.command(command, ctx, states, inner)
                if reason:
                    return reason, NOTHING
            # W5: earlier stages run in subshells. The last may run in this
            # shell (zsh, bash lastpipe), so its effect and none are both kept.
            out = any_status(out | states)
        if reason:
            return reason, NOTHING
        return None, widen(negated(out) if node.negated else out)  # W2

    def command(self, node, ctx, states, scope):
        is_segment = isinstance(node, Segment)
        if not is_segment or node.substitutions or node.processes:
            reason = self.side_scripts(node, ctx, states, scope)
            if reason:
                return reason, NOTHING
        if is_segment:
            self.count(states)
            reason, out = self.simple(node, ctx, states, scope)
            # A trap can fire after this command too (at the end of the script,
            # a sourced script or a function), so it sees this command's folders.
            self.note_traps(out)
            return reason, out
        texts = [text for part in nodes_in(node) for text in redirect_texts(part.redirects)]
        scope = with_texts(scope, texts)
        if node.redirects:
            scope = scope._replace(exits=False)  # A failed redirection skips the whole compound.
        reason, out = getattr(self, 'compound_' + node.kind)(node, ctx, states, scope)
        if reason:
            return reason, NOTHING
        return None, (any_status(out | states) if node.redirects else out)

    def count(self, states):
        self.steps += 1
        if self.steps > STEP_LIMIT:
            raise TooComplex()
        self.note_traps(states)

    def note_traps(self, states):
        for trap in self.traps:
            trap[2].update(cwd for cwd, _ in states)

    def side_scripts(self, node, ctx, states, scope):
        """Check what a command runs besides itself: $(...) and <(...) lists."""
        quiet = QUIET._replace(source=scope.source)
        substitutions = list(node.substitutions)
        if isinstance(node, Compound):
            substitutions += [sub for word in node.words for sub in word.substitutions]
        for substitution in substitutions:
            reason, _ = self.tokens(substitution, ctx.nested(), any_status(states), quiet)
            if reason:
                return reason
        for process in getattr(node, 'processes', ()):
            reason, _ = self.sequence(process, ctx.nested(), any_status(states), quiet)
            if reason:
                return reason
        return None

    def compound_group(self, node, ctx, states, scope):
        return self.sequence(node.body, ctx, states, scope)

    def compound_subshell(self, node, ctx, states, scope):
        reason, _ = self.sequence(node.body, ctx, states, scope._replace(exits=False))
        return reason, any_status(states)  # W4

    def compound_if(self, node, ctx, states, scope):
        results, pending = NOTHING, states
        for cond, body in node.clauses:
            reason, after = self.sequence(cond, ctx, pending, scope)
            if reason:
                return reason, NOTHING
            running, pending = split(after, '&&')
            reason, out = self.sequence(body, ctx, running, scope)
            if reason:
                return reason, NOTHING
            results |= out
        if node.otherwise is not None:
            reason, out = self.sequence(node.otherwise, ctx, pending, scope)
            if reason:
                return reason, NOTHING
            results |= out
        else:
            results |= any_status(pending)
        return None, widen(results)

    def compound_while(self, node, ctx, states, scope):
        return self.loop(node, ctx, states, scope)

    compound_until = compound_for = compound_while

    def loop(self, node, ctx, states, scope):
        """The body may run any number of times, whatever its condition says."""
        scope = scope._replace(exits=False)
        head = states
        for round_number in range(LOOP_ROUNDS + 1):
            if round_number == LOOP_ROUNDS:
                head = WIDE  # Still growing: assume any folder and check once from there.
            after = head
            if node.cond is not None:
                reason, after = self.sequence(node.cond, ctx, head, scope)
                if reason:
                    return reason, NOTHING
            reason, out = self.sequence(node.body, ctx, any_status(after | head), scope)
            if reason:
                return reason, NOTHING
            grown = widen(any_status(head | after | out))
            if grown == head or head == WIDE:
                break
            head = grown
        return None, head

    def compound_case(self, node, ctx, states, scope):
        results = any_status(states)  # No arm matched.
        for arm in node.arms:
            reason, out = self.sequence(arm, ctx, states, scope._replace(exits=False))
            if reason:
                return reason, NOTHING
            results |= out
        return None, widen(any_status(results))

    def compound_function(self, node, ctx, states, scope):
        if node.span and scope.source:
            body = scope.source[node.span[0]:node.span[1]]
        else:
            body = node.body  # No source to re-read: keep the tree (its $1 stays as parsed).
        ctx.functions.setdefault(node.name, []).append(body)
        # Defining runs nothing, but check the body now too, from every folder
        # possible here: it may be called in ways the guard cannot see.
        reason, _ = self.call(body, ctx.nested(), states, scope.texts)
        return reason, states

    def call(self, body, ctx, states, texts=()):
        """Run one function body; a function never narrows its caller's paths."""
        if isinstance(body, str):
            reason, out = self.script(body, ctx, states, exits=False, texts=texts)
        else:
            reason, out = self.command(body, ctx, states, Scope(False, tuple(texts), ''))
        return reason, any_status(out | states)

    def add_trap(self, text, ctx, states):
        self.traps.append([text, ctx, {cwd for cwd, _ in states}])

    def fire_traps(self):
        """Check each trap's script from every folder possible after it was set."""
        index = 0
        while index < len(self.traps):
            text, ctx, folders = self.traps[index]
            index += 1
            reason, _ = self.script(text, ctx.nested(), frozenset((cwd, ANY) for cwd in folders),
                                    exits=False)
            if reason:
                return reason
        return None

    def flat(self, tokens, ctx, states, scope):
        """Every simple command in order, for shapes the tree parser does not
        model: a folder, once possible, stays possible, and no exit ends a path."""
        segments = [segment for segment in command_segments(tokens) if segment.words or segment.redirects]
        scope = with_texts(scope._replace(exits=False),
                           [text for segment in segments for text in redirect_texts(segment.redirects)])
        for segment in segments:
            reason = self.side_scripts(segment, ctx, states, scope)
            if not reason:
                self.count(states)
                reason, out = self.simple(segment, ctx, states, scope)
                self.note_traps(out)
            if reason:
                return reason, NOTHING
            states = widen(states | any_status(out))
        return None, any_status(states)
