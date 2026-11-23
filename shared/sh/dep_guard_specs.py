"""Literal public-registry operands only; never resolve requirements or expand shell words."""

import re
from dataclasses import dataclass
from typing import Optional

NPM = 'npm'
PYPI = 'PyPI'
_NPM_PART = r'[A-Za-z0-9~-][A-Za-z0-9._~-]*'
_NPM_NAME = rf'(?:@{_NPM_PART}/)?{_NPM_PART}'
_NPM = re.compile(rf'({_NPM_NAME})(?:@(.*))?', re.ASCII)
_SEMVER = re.compile(r'=?\s*v?((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)'
                     r'(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?'
                     r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)', re.ASCII)
_PY_NAME = r'[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?'
_PY = re.compile(rf'({_PY_NAME})(?:\s*\[\s*{_PY_NAME}(?:\s*,\s*{_PY_NAME})*\s*\])?\s*(.*)', re.ASCII)
# PEP 440 spelling, not a comparator: the verdict owner interprets the version.
_PY_VERSION = r'v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:[._-]?(?:a|alpha|b|beta|rc|c|pre|preview)[._-]?[0-9]*))?(?:-[0-9]+|[._-]?(?:post|rev|r)[._-]?[0-9]*)?(?:[._-]?dev[._-]?[0-9]*)?(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?'
_PY_PIN = re.compile(r'(?:===|==)\s*(' + _PY_VERSION + ')', re.I | re.ASCII)
_ARCHIVES = ('.tgz', '.tar', '.tar.gz', '.tar.bz2', '.tar.xz', '.whl', '.zip')
_DYNAMIC = re.compile(r'[$`\x00-\x1f\x7f]')
_NEXT_NAME = re.compile(r'\s*[a-zA-Z0-9]')


@dataclass(frozen=True)
class Spec:
    name: str
    pin: Optional[str] = None


def canonical_name(ecosystem, name):
    """Overrides use actual registry names, not aliases, pins or extras."""
    if ecosystem == NPM and re.fullmatch(_NPM_NAME, name) and len(name) <= 214:
        return name
    if ecosystem == PYPI and re.fullmatch(_PY_NAME, name) and len(name) <= 214:
        return re.sub(r'[-_.]+', '-', name).lower()
    return None


def npm_spec(text, initializer=False):
    if len(text) > 4096 or _DYNAMIC.search(text) or text.startswith(('-', '~')):
        return None
    if initializer and text.startswith('@') and '/' not in text:
        scope = re.fullmatch(rf'(@{_NPM_PART})(@.*)?', text)
        if scope:
            text = scope[1] + '/create' + (scope[2] or '')
            initializer = False
    match = _NPM.fullmatch(text)
    if not match:
        return None
    name, version = match.groups()
    if not canonical_name(NPM, name) or name.lower().endswith(_ARCHIVES):
        return None
    if version and version.startswith('npm:') and not initializer:
        target = version[4:]
        return None if 'npm:' in target else npm_spec(target)
    # File paths, tarball URLs, VCS refs, patch/workspace protocols etc. are not
    # requests for this name on the public registry. A tag/range is not a pin.
    if version and (not re.fullmatch(r'[a-zA-Z0-9._~^*<>=+| -]+', version)
                    or version.startswith(('./', '../', '.\\'))):
        return None
    if initializer:
        scope, slash, base = name.rpartition('/')
        name = scope + slash + 'create-' + base
    exact = _SEMVER.fullmatch(version or '')
    return Spec(name, exact[1] if exact else None)


def pypi_spec(text, poetry=False, tool_version=False):
    if len(text) > 4096 or _DYNAMIC.search(text):
        return None
    requirement = text.split(';', 1)[0].strip()
    match = _PY.fullmatch(requirement)
    if not match:
        return None
    name, version = match.groups()
    name = canonical_name(PYPI, name)
    if not name or requirement.lower().endswith(_ARCHIVES):
        return None
    version = version.strip()
    if version.startswith('(') and version.endswith(')'):
        version = version[1:-1].strip()
    if (poetry or tool_version) and version.startswith('@'):
        version = version[1:].strip()
        if version == 'latest':
            version = ''
        elif re.fullmatch(_PY_VERSION, version, re.I | re.ASCII):
            version = '==' + version
        elif not version.startswith(('^', '~', '=', '<', '>', '!')):
            return None
    if version:
        if not version.startswith(('===', '==', '!=', '~=', '<', '>') + (('^', '~') if poetry else ())):
            return None
        if not re.fullmatch(r'[A-Za-z0-9.,+*!=~^<>_ -]+', version):
            return None
    exact = _PY_PIN.fullmatch(version)
    return Spec(name, exact[1] if exact else None)


def with_requirements(value):
    """uv accepts comma lists; commas in extras and comparator lists stay put."""
    if '://' in value or '@' in value or '/' in value:
        return  # A URL/path may itself contain a comma followed by private text.
    start = depth = 0
    for i, char in enumerate(value):
        if char == '[':
            depth += 1
        elif char == ']':
            depth = max(0, depth-1)
        elif char == ',' and not depth and _NEXT_NAME.match(value, i+1):
            yield value[start:i].strip()
            start = i + 1
    yield value[start:].strip()
