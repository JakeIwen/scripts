"""Package-manager option arities. Unknown options never turn their value into a lookup."""

from dataclasses import dataclass, field


def words(text):
    return frozenset(text.split())


# Options may occur before the verb. Do not use one table for all tools: -p
# takes a package in npx, means --parseable in npm, and --python in uv.
_NODE_VALUES = words('''--registry --prefix --workspace --filter --config --userconfig --globalconfig
    --cache --tag --before --loglevel --logs-dir --script-shell --shell --package --call -c
    --cwd --dir --directory --config-file --location --install-strategy --omit --include --access
    --save-prefix --proxy --https-proxy --cafile --otp --fetch-timeout --auth-type --workspace-root-dir''')
_NODE_FLAGS = words('''-g -D -P -O -E -S -B -W -y -s -f -q -v -d -dd -ddd --global --save
    --save-dev --save-prod --save-optional --save-exact --save-peer --no-save --dev --production
    --optional --exact --peer --verbose --silent --quiet --yes --no --offline --prefer-offline
    --prefer-online --dry-run --package-lock-only --no-package-lock --force --ignore-scripts
    --no-audit --no-fund --legacy-peer-deps --strict-peer-deps --workspaces --include-workspace-root
    --no-progress --no-color --color --json --parseable --foreground-scripts --frozen-lockfile
    --ignore-workspace-root-check --no-install --ignore-existing --install-links --legacy-bundling''')
_PY_VALUES = words('''--index --index-url --extra-index-url --default-index --find-links -i -f
    --trusted-host --proxy --cert --client-cert --config-file --config --python --cache-dir --log
    --timeout --retries --keyring-provider --index-strategy --color --directory --project
    -r --requirement --requirements -c --constraint --constraints -e --editable''')
_PY_FLAGS = words('''-q -v --quiet --verbose --no-index --offline --isolated --user --no-deps
    --no-cache-dir --no-cache --dry-run --pre --no-input --no-color --no-build-isolation
    --no-python-downloads --no-progress --system --break-system-packages --only-managed --no-config''')
_PIP_VALUES = words('''--target -t --prefix --root --src --platform --python-version --implementation
    --abi --only-binary --no-binary --config-settings -C --upgrade-strategy --progress-bar
    --report --root-user-action --exists-action --use-feature --use-deprecated --group
    --build-constraint --global-option --install-option''')
_PIP_FLAGS = words('''-U --upgrade --force-reinstall -I --ignore-installed --require-hashes
    --disable-pip-version-check --compile --no-compile --prefer-binary --require-virtualenv
    --use-pep517 --no-warn-script-location --no-warn-conflicts --ignore-requires-python''')
_UV_VALUES = words('''-p --from -w --with --with-editable --with-requirements --with-executables-from
    --group --optional --extra --package --resolution --prerelease --link-mode --refresh-package
    --upgrade-package -P --reinstall-package --exclude-newer --exclude-newer-package
    --default-group --env-file --allow-insecure-host --python-preference --config-setting
    --python-platform --python-version --dependency-metadata --fork-strategy''')
_UV_FLAGS = words('''-U --upgrade --dev --no-sync --frozen --locked --no-project
    --no-dev --no-editable --active --global --refresh --reinstall --exact --inexact --preview
    --native-tls --no-managed-python --managed-python --prerelease-allow''')
_VALUES = {
    'npm': _NODE_VALUES | words('-C -w -L'), 'npx': _NODE_VALUES | words('-C -w -p -L'),
    'pnpm': _NODE_VALUES | words('-C -F --allow-build --store-dir --virtual-store-dir'),
    'yarn': _NODE_VALUES | words('--use-yarnrc --mode --cache-folder --modules-folder --mutex --use'),
    'yarn-dlx': _NODE_VALUES | words('-p'),
    'bun': _NODE_VALUES | words('--backend --linker --env-file --filter'),
    'bunx': _NODE_VALUES | words('-p --env-file'),
    'pip': _PY_VALUES | _PIP_VALUES,
    'uv': _PY_VALUES | _UV_VALUES | _PIP_VALUES | {'--script'},
    'uv-run': _PY_VALUES | _UV_VALUES | _PIP_VALUES,
    'pipx': _PY_VALUES | words('--pip-args --suffix --backend --spec'),
    'poetry': _PY_VALUES | words('-C -P --source --group -G --extras -E --python --platform --lockfile'),
}
_FLAGS = {
    'npm': _NODE_FLAGS | words('-p --no-global --local'), 'npx': _NODE_FLAGS | words('--no-global --local'),
    'pnpm': _NODE_FLAGS | words('-p -r -w --workspace-root --recursive --lockfile-only'),
    'yarn': _NODE_FLAGS | words('-T -R -i --ignore-engines'), 'yarn-dlx': _NODE_FLAGS | words('-q'),
    'bun': _NODE_FLAGS | words('-d --trust --no-verify --no-save --bun'),
    'bunx': _NODE_FLAGS | words('--bun -b'), 'pip': _PY_FLAGS | _PIP_FLAGS,
    'uv': _PY_FLAGS | _UV_FLAGS | _PIP_FLAGS,
    'uv-run': _PY_FLAGS | _UV_FLAGS | _PIP_FLAGS | {'--script', '-s', '--gui-script'},
    'pipx': _PY_FLAGS | words('--force --include-deps --include-apps --system-site-packages --global --no-cache'),
    'poetry': _PY_FLAGS | words('-D --dev --optional --allow-prereleases --lock --no-interaction --no-ansi --no-root'),
}
_RELOCATION = words('''--prefix --workspace --workspaces --filter --directory --cwd --dir --project --userconfig
    --globalconfig --config --config-file --location --workspace-root --recursive --workspace-root-dir
    --isolated --pip-args --env-file --use-yarnrc --global-folder --no-config --script-shell --with-requirements''')
_SHORT_RELOCATION = {'npm': {'-C', '-w'}, 'npx': {'-C', '-w'}, 'pnpm': {'-C', '-F', '-r', '-w'},
                     'yarn': {'-R'}, 'poetry': {'-C', '-P'}, 'uv': {'-p'}, 'uv-run': {'-p'}}


@dataclass
class Scan:
    operands: list = field(default_factory=list)
    options: list = field(default_factory=list)
    uncertain: bool = False
    consumed: int = 0


# npm 10.9.4 @npmcli/config/lib/definitions: these supported one-letter
# shorthands expand either to a Boolean option or to --loglevel plus a value.
# Other managers share _NODE_FLAGS, but that doesn't make their letters npm shorthands.
_NPM_BOOL_SHORT = frozenset('gDPOESByfvp')
_NPM_FIXED_SHORT = frozenset('sqd')
_NPM_LOGLEVEL = words('-d -dd -ddd -q -s --quiet --silent --verbose')


def _npm_cluster(raw, args, i):
    letters = raw[1:]
    if any(ch not in _NPM_BOOL_SHORT | _NPM_FIXED_SHORT for ch in letters):
        return raw, None, i+1, False
    if letters[-1] in _NPM_BOOL_SHORT and args[i+1:i+2] in (('true',), ('false',)):
        return raw, args[i+1], i+2, True
    return raw, None, i+1, True


def _option(args, i, style):
    """Return key, value, next index, known. Slice indexes retain original argv."""
    raw = args[i]
    key, equals, value = raw.partition('=')
    if style == 'npx' and not key.startswith('--') and len(key) > 2:
        # npm 10.9.4 bin/npx-cli.js finds the -- boundary before nopt
        # expands clusters. Unknown switch words can swallow a positional.
        return raw, None, i+1, False
    values, flags = _VALUES[style], _FLAGS[style]
    if key not in values and key not in flags and not key.startswith('--'):
        if raw[:2] in values:
            return raw[:2], raw[2:].lstrip('='), i + 1, style not in {'npm', 'npx'}
        if len(raw) > 2 and all('-' + ch in flags for ch in raw[1:]):
            if style == 'npm':
                return _npm_cluster(raw, args, i)
            return raw, None, i + 1, True
        for n, ch in enumerate(raw[1:], 1):
            key = '-' + ch
            if key in values:
                attached = raw[n+1:]
                # A mixed cluster may expand into quite different options. Its
                # provisional operand can explain abstention, never drive a lookup.
                if attached:
                    return key, attached.lstrip('='), i+1, False
                return key, args[i+1] if i+1 < len(args) else '', min(i+2, len(args)), False
            if key not in flags:
                key = raw
                break
    if key in values:
        if equals:
            return key, value, i + 1, True
        return key, args[i+1] if i+1 < len(args) else '', min(i+2, len(args)), True
    if key in flags:
        if style in {'npm', 'npx'}:
            if key == '-W' or key in _NPM_LOGLEVEL and equals:
                return key, None, i+1, False
            if key not in _NPM_LOGLEVEL and not equals and args[i+1:i+2] in (('true',), ('false',)):
                if style == 'npx':
                    # npx can insert -- before the literal; don't guess whether
                    # its separate preprocessor recognized this switch.
                    return key, None, i+1, False
                return key, args[i+1], i+2, True
        return key, value if equals else None, i + 1, True
    # Opaque arity: no subsequent operand is safe to send to a public service.
    return key, None, i + (1 if equals or i+1 == len(args) or args[i+1].startswith('-') else 2), False


def scan(args, style, first=False):
    """Scan manager options, optionally stopping after its first positional."""
    result = Scan()
    i, ended = 0, False
    while i < len(args):
        arg = args[i]
        if not ended and arg == '--':
            ended = True
            i += 1
        elif ended or not arg.startswith('-') or arg == '-':
            result.operands.append(arg)
            i += 1
            if first:
                break
        else:
            key, value, i, known = _option(args, i, style)
            result.options.append((key, value))
            relocate = _SHORT_RELOCATION.get(style, ())
            cluster = not key.startswith('--') and value is None and any('-'+c in relocate for c in key[1:])
            relocates = key in _RELOCATION
            if style in {'npm', 'npx'}:
                if key in ('--location', '-L'):
                    relocates = value not in ('global', 'project', 'user')
                elif key in ('--global', '--no-global', '--local', '-g') and value not in (None, 'true', 'false'):
                    relocates = True  # nopt can return an invalid boolean as a positional.
            if not known or relocates or key in relocate or cluster:
                result.uncertain = True
    result.consumed = i
    return result


def npm_global_mode(options, env, file_modes):
    """npm's global flag OR location=global. Unknown file precedence stays unknown."""
    config = {'global': {'false'}, 'location': {'user'}}
    for k, default in config.items():
        # Ancestor configs are a conservative superset of npm's actual sources.
        default.update(file_modes.get(k, ()))
        values = {v for key, v in env.items() if key.lower() == 'npm_config_' + k and v}
        if values:
            config[k] = values
    for key, value in options:
        if key in ('--global', '--no-global', '--local'):
            enabled = value != 'false'
            if key in ('--no-global', '--local'):
                enabled = not enabled
            config['global'] = {str(enabled).lower()}
        elif key.startswith('-') and not key.startswith('--') and 'g' in key[1:]:
            # nopt expands a short cluster first; its boolean value binds to
            # the last flag only: -sg false differs from -gs false.
            config['global'] = {'false' if key.endswith('g') and value == 'false' else 'true'}
        elif key in ('--location', '-L'):
            config['location'] = {value}
    flags, locations = config['global'], config['location']
    if flags == {'true'} or locations == {'global'}:
        return True
    if flags == {'false'} and locations <= {'project', 'user'}:
        return False
    return None
