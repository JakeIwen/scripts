"""What an interpreter runs, read from its arguments with confidence or not at all.

`ps` joins argv with spaces, so after argv[0] (which the process table reads separately) the
argument boundaries are only a guess. The parse therefore accepts a short, known shape and
nothing else: operand-free flags from a small per-interpreter table, then either `-m <module>`
(Python) or a first operand that plainly is a script file. Any other option, an inline program
(`-c`, `-e`), an option with a value, or an operand that is not a script name yields no detail,
so an argument value can never become a label. Unmatched is also the safe outcome for grouping:
the process is then an ordinary app process and the signal policy still applies.
"""
from dataclasses import dataclass
from enum import Enum
import os
import re


class Family(str, Enum):
    PYTHON = 'python'
    NODE = 'node'
    SHELL = 'shell'
    RUBY = 'ruby'
    PERL = 'perl'


SHELLS = frozenset(('zsh', 'bash', 'sh', 'dash', 'fish', 'ksh', 'tcsh', 'csh'))
PYTHON_NAME = re.compile(r'python(?:w)?(?:\d+(?:\.\d+)*)?', re.I)
FAMILY_NAMES = {'node': Family.NODE, 'nodejs': Family.NODE, 'ruby': Family.RUBY,
                'perl': Family.PERL, **dict.fromkeys(SHELLS, Family.SHELL)}


@dataclass(frozen=True)
class OperandFreeFlags:
    """Flags that take no value, so the token after them is still the interpreter's operand."""
    # Single letters, accepted alone (`-B`) or clustered (`-BI`).
    letters: frozenset
    # Whole long options (`--no-warnings`).
    words: frozenset = frozenset()
    # Python's `-m`: allowed as the last letter of a cluster (`-Bm`), then the module name.
    module: bool = False


FAMILY_FLAGS = {
    Family.PYTHON: OperandFreeFlags(frozenset('PBIuEsSOq'), module=True),
    Family.NODE: OperandFreeFlags(frozenset(), frozenset((
        '--no-warnings', '--enable-source-maps', '--no-deprecation', '--trace-warnings',
        '--expose-gc', '--experimental-vm-modules'))),
    # `-c` is absent on purpose: its operand is inline program text.
    Family.SHELL: OperandFreeFlags(frozenset('lieuxfp'), frozenset(('--login', '--noprofile',
                                                                     '--norc'))),
    Family.RUBY: OperandFreeFlags(frozenset('w')),
    Family.PERL: OperandFreeFlags(frozenset('wTt')),
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
    family: Family | None
    script: str | None = None
    module: str | None = None


def interpreter_family(exe) -> Family | None:
    if PYTHON_NAME.fullmatch(exe):
        return Family.PYTHON
    return FAMILY_NAMES.get(exe)


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
    family = interpreter_family(exe)
    if family is None:
        return Invocation(None)
    flags = FAMILY_FLAGS[family]
    tokens = arguments_text.split()
    for index, token in enumerate(tokens):
        if token in flags.words:
            continue
        if SHORT_CLUSTER.fullmatch(token):
            letters = token[1:]
            if flags.module and letters[-1] == 'm' and set(letters[:-1]) <= flags.letters:
                return Invocation(family, module=module_after(tokens, index))
            if set(letters) <= flags.letters:
                continue
            return Invocation(family)
        if token.startswith(('-', '+')):
            return Invocation(family)
        return Invocation(family, script=token if script_label(token) else None)
    return Invocation(family)


def invocation_detail(call: Invocation) -> str | None:
    """The part of a display name after the executable, or None."""
    if call.module is not None:
        return f'-m {call.module}'
    if call.script is not None:
        return script_label(call.script)
    return None
