"""The declared offsite snapshot closure matches what the code actually reaches.

Starting from the worker and every helper command it runs from its snapshot,
follow Python imports (lazy ones included), shell `.`/`source` and script
references, and inline `python3 -c 'import ...'`, transitively. Every reached
file must be declared frozen, and every absolute /home/pi path in frozen code
must be a declared live boundary or live data: an undeclared live code path
would run whatever is deployed at that moment instead of the frozen copy.
"""
import ast
from pathlib import Path
import re
import sys
import unittest

from offsite_config import FROZEN_IMPLEMENTATION, LIVE_BOUNDARIES, LIVE_DATA, LIVE_ROOT, command_paths
from offsite_remote import remote_commands

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
LIVE_PREFIX = str(LIVE_ROOT) + '/'
HOME = '/home/pi/'
SHELL_REFERENCE = re.compile(r'''[$A-Za-z0-9_./{}()"'-]*[A-Za-z0-9_-]\.(?:sh|py)\b''')
VARIABLE_PREFIX = re.compile(r'''^.*(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\)"?)/''')
ABSOLUTE_HOME = re.compile(r'/home/pi/[A-Za-z0-9_./-]+')
INLINE_IMPORT = re.compile(r'\bimport ([A-Za-z_][A-Za-z0-9_]*)')


def entry_points(root):
    """The worker plus every command it runs from its own snapshot root."""
    seeds = {'backup/offsite_backup.py'}
    for path in (*command_paths(root).values(), *remote_commands(root).values()):
        if Path(path).is_relative_to(root) and path.endswith(('.sh', '.py')):
            seeds.add(Path(path).relative_to(root).as_posix())
    return seeds


def python_references(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 'import', alias.name.split('.')[0] + '.py'
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield 'import', node.module.split('.')[0] + '.py'
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for match in ABSOLUTE_HOME.findall(node.value):
                yield 'absolute', match


def shell_references(text):
    for line in text.splitlines():
        code = re.sub(r'(^|\s)#.*$', '', line)
        for match in ABSOLUTE_HOME.findall(code):
            yield 'absolute', match
        for match in SHELL_REFERENCE.findall(code):
            # `${NAME:-/abs/path}` defaults leave the `-`; absolute paths are handled above.
            match = VARIABLE_PREFIX.sub('', match).lstrip('"\'-')
            if not match.startswith('/'):
                yield 'relative', match
        for module in INLINE_IMPORT.findall(code):
            yield 'import', module + '.py'


def scan(root):
    """Return (reached closure, escapes, unclassified, unresolved) for a scripts tree."""
    queue, reached = sorted(entry_points(root)), set()
    escapes, unclassified, unresolved = [], [], []
    while queue:
        relative = queue.pop()
        if relative in reached:
            continue
        reached.add(relative)
        path = root / relative
        text = path.read_text()
        references = python_references(text) if relative.endswith('.py') else shell_references(text)
        for kind, token in references:
            if kind == 'absolute':
                if token.startswith(LIVE_PREFIX):
                    if token[len(LIVE_PREFIX):] not in LIVE_BOUNDARIES:
                        escapes.append((relative, token))
                elif not token.startswith(LIVE_DATA) and token.rstrip('/') != str(LIVE_ROOT):
                    unclassified.append((relative, token))
                continue
            # Sibling first, then the scripts root (icloud_uplink.py imports from its parent).
            hit = next((candidate for candidate in (path.parent / token, root / token) if candidate.is_file()), None)
            if hit is not None:
                hit = hit.resolve().relative_to(root.resolve()).as_posix()
                if hit not in LIVE_BOUNDARIES:
                    queue.append(hit)
            elif not (kind == 'import' and token[:-3] in sys.stdlib_module_names):
                unresolved.append((relative, token))
    return reached, escapes, unclassified, unresolved


class ClosureTests(unittest.TestCase):
    def test_declared_closure_is_exactly_what_the_run_reaches(self):
        reached, escapes, unclassified, unresolved = scan(SCRIPTS)
        self.assertEqual(sorted(reached - set(FROZEN_IMPLEMENTATION)), [], 'reached but not frozen')
        self.assertEqual(sorted(set(FROZEN_IMPLEMENTATION) - reached), [], 'frozen but never reached')
        self.assertEqual(unresolved, [])
        self.assertEqual(unclassified, [], 'absolute /home/pi paths must be declared live data')
        self.assertEqual(escapes, [], 'frozen code must reach the scripts tree script-relatively')

    def test_scanner_notices_a_new_helper_and_an_absolute_live_path(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'scripts'
            for relative in FROZEN_IMPLEMENTATION:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SCRIPTS / relative, root / relative)
            (root / 'backup' / 'new_helper.sh').write_text('true\n')
            (root / 'samba_share_control.sh').write_text(
                '. "$script_dir/disk_policy.sh"\n"$script_dir/backup/new_helper.sh"\n'
                '. /home/pi/scripts/backup/backup_conf.sh\ncat /home/pi/elsewhere/data\n')
            reached, escapes, unclassified, _ = scan(root)
        self.assertIn('backup/new_helper.sh', reached)
        self.assertIn(('samba_share_control.sh', '/home/pi/scripts/backup/backup_conf.sh'), escapes)
        self.assertIn(('samba_share_control.sh', '/home/pi/elsewhere/data'), unclassified)

    def test_boundaries_and_data_never_overlap_the_frozen_tree(self):
        for relative in FROZEN_IMPLEMENTATION:
            self.assertFalse(relative.startswith('/'), relative)
            self.assertTrue((SCRIPTS / relative).is_file(), relative)
        self.assertFalse(set(LIVE_BOUNDARIES) & set(FROZEN_IMPLEMENTATION))
        for prefix in LIVE_DATA:
            self.assertFalse(prefix.startswith(LIVE_PREFIX), prefix)


if __name__ == '__main__':
    unittest.main()
