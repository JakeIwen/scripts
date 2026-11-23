"""Command-form vocabulary shared by the cheap prefilter and the operand parser."""
import re

PYTHON = re.compile(r'python(?:[23](?:\.\d+)?)?t?')
PIP = re.compile(r'pip(?:[23](?:\.\d+)?)?')
# Each mode describes which operands denote packages, not which ones a tool runs.
VERBS = {
    'npm': {**dict.fromkeys('install i in isntall add install-test it'.split(), 'add'),
            'exec': 'npm-exec', 'x': 'npm-exec', 'create': 'create', 'init': 'create'},
    'pnpm': {'add': 'add', 'install': 'add', 'i': 'add', 'dlx': 'exec'},
    'yarn': {'add': 'add', 'global add': 'add', 'dlx': 'exec'},
    'bun': {'add': 'add', 'install': 'add', 'i': 'add', 'x': 'exec'},
    'pip': {'install': 'add'}, 'pipx': {'install': 'add', 'run': 'exec', 'inject': 'inject'},
    'poetry': {'add': 'add'}, 'uv': {'add': 'add', 'pip install': 'add', 'tool install': 'tool-add',
                                   'tool run': 'exec', 'run': 'with-only'},
}
DIRECT = {'npx': 'npm', 'pnpx': 'pnpm', 'bunx': 'bun', 'uvx': 'uv'}


def word_pattern(expressions):
    return re.compile(r'(?<!\w)(?:' + '|'.join(expressions) + r')(?!\w)', re.ASCII)


_TOOLS = word_pattern([*DIRECT, *(k for k in VERBS if k != 'pip'), PIP.pattern, PYTHON.pattern])
_OP_WORDS = word_pattern(sorted({w for forms in VERBS.values() for op in forms for w in op.split()},
                                key=len, reverse=True))
_WITH = re.compile(r'--with\b|-[A-Za-z]*w')
_QUOTES = str.maketrans('', '', '''\\'"''')


def may_install(command):
    # Look only for necessary words, independent of their order or distance:
    # global options, wrappers, nested shells and redirections can intervene.
    # Do not collapse ordinary newlines: they separate executable words.
    text = command.replace('\\\n', '').translate(_QUOTES)
    tools = {m.group() for m in _TOOLS.finditer(text)}
    if not tools:
        return False
    if tools.intersection(DIRECT):
        return True  # npx, uvx and the other package runners need no verb.
    words = {m.group() for m in _OP_WORDS.finditer(text)}
    for tool in tools:
        family = tool if tool in VERBS else 'pip'  # pipN.M / pythonN.M -m pip
        for verb, mode in VERBS[family].items():
            if words.issuperset(verb.split()) and (mode != 'with-only' or _WITH.search(text)):
                return True
    return False
