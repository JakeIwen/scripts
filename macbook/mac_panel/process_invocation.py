"""What an interpreter runs, read from its arguments with confidence or not at all.

`ps` joins argv with spaces, so after argv[0] (which the process table reads separately) the
argument boundaries are only a guess. The parse therefore accepts a short, known shape and
nothing else: operand-free flags from a small table of that interpreter's own, then either `-m <module>`
(Python) or a first operand that plainly is a script file. Any other option, an inline program
(`-c`, `-e`), an option with a value, or an operand that is not a script name yields no detail,
so an argument value can never become a label. This parse is for labels only: roles that
decide what may be signalled come from `process_roles`, which reads the same arguments the
other way round.
"""
from dataclasses import dataclass
import os
import re


@dataclass(frozen=True)
class OperandFreeFlags:
    """Flags that take no value, so the token after them is still the interpreter's operand."""
    # Single letters, accepted alone (`-B`) or clustered (`-BI`).
    letters: frozenset
    # Whole long options (`--no-warnings`).
    words: frozenset = frozenset()
    # Python's `-m`: allowed as the last letter of a cluster (`-Bm`), then the module name.
    module: bool = False


PYTHON = 'python'
PYTHON_NAME = re.compile(r'python(?:w)?(?:\d+(?:\.\d+)*)?', re.I)
INTERPRETER_ALIASES = {'nodejs': 'node'}
# One table per interpreter: the same letter can be operand-free in one shell and take a
# path in another (fish `-p` writes a profile to a file). An interpreter without a table
# shows the bare program. `-c` and `-e` are absent everywhere: their operand is a program.
INTERPRETER_FLAGS = {
    PYTHON: OperandFreeFlags(frozenset('PBIuEsSOq'), module=True),
    'node': OperandFreeFlags(frozenset(), frozenset((
        '--no-warnings', '--enable-source-maps', '--no-deprecation', '--trace-warnings',
        '--expose-gc', '--experimental-vm-modules'))),
    'zsh': OperandFreeFlags(frozenset('lifuvx'), frozenset(('--login',))),
    'bash': OperandFreeFlags(frozenset('lifuvx'), frozenset(('--login', '--noprofile', '--norc'))),
    'sh': OperandFreeFlags(frozenset('ifuvx')),
    'dash': OperandFreeFlags(frozenset('lifuvx')),
    'ruby': OperandFreeFlags(frozenset('w')),
    'perl': OperandFreeFlags(frozenset('wTt')),
}
SCRIPT_SUFFIXES = frozenset(('.py', '.js', '.mjs', '.cjs', '.ts', '.sh', '.zsh', '.bash',
                             '.rb', '.pl'))
SAFE_NAME = re.compile(r'[\w.@+-]{1,80}')
# A script operand: path components of safe characters only.
SAFE_PATH = re.compile(r'[~/]?(?:[\w.@+-]+/)*[\w.@+-]+')
SHORT_CLUSTER = re.compile(r'-[A-Za-z]{1,8}')
MODULE_NAME = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){0,8}')


@dataclass(frozen=True)
class Invocation:
    """What an interpreter runs: a script operand or a module (internal only)."""
    interpreter: str | None
    script: str | None = None
    module: str | None = None


def interpreter_name(exe) -> str | None:
    """The key of the interpreter's flag table, or None for a program without one."""
    if PYTHON_NAME.fullmatch(exe):
        return PYTHON
    name = INTERPRETER_ALIASES.get(exe, exe)
    return name if name in INTERPRETER_FLAGS else None


def script_label(token) -> str | None:
    """A script operand's base name: safe characters and a script suffix, or nothing.

    The suffix is required even for paths: a path argument containing a space reaches
    here cut at the space, and its first fragment must not be shown as a name.
    """
    if not SAFE_PATH.fullmatch(token):
        return None
    base = os.path.basename(token)
    if os.path.splitext(base)[1] not in SCRIPT_SUFFIXES or not SAFE_NAME.fullmatch(base):
        return None
    return base


def module_after(tokens, index) -> str | None:
    module = tokens[index + 1] if index + 1 < len(tokens) else None
    return module if module and MODULE_NAME.fullmatch(module) else None


def invocation(exe, arguments_text) -> Invocation:
    interpreter = interpreter_name(exe)
    if interpreter is None:
        return Invocation(None)
    flags = INTERPRETER_FLAGS[interpreter]
    tokens = arguments_text.split()
    for index, token in enumerate(tokens):
        if token in flags.words:
            continue
        if SHORT_CLUSTER.fullmatch(token):
            letters = token[1:]
            if flags.module and letters[-1] == 'm' and set(letters[:-1]) <= flags.letters:
                return Invocation(interpreter, module=module_after(tokens, index))
            if set(letters) <= flags.letters:
                continue
            return Invocation(interpreter)
        if token.startswith(('-', '+')):
            return Invocation(interpreter)
        return Invocation(interpreter, script=token if script_label(token) else None)
    return Invocation(interpreter)


def invocation_detail(call: Invocation) -> str | None:
    """The part of a display name after the executable, or None."""
    if call.module is not None:
        return f'-m {call.module}'
    if call.script is not None:
        return script_label(call.script)
    return None
