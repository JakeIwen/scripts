from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import time
import unittest

from cli_runner import CliRunner
from panel_paths import DIRECTORY, HOST
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, request, running_server
from server import STATIC_NAME, source_changed, static_file

STATIC = DIRECTORY / 'static'
IMPORT = re.compile(r'''^import\b[^;]*?\bfrom\s+['"]\./([^'"]+)['"];''', re.M)


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.scripts = []
        self.tokens = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'script':
            self.assets.append(attrs.get('src'))
            self.scripts.append(attrs.get('type'))
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.assets.append(attrs.get('href'))
        if tag == 'meta' and attrs.get('name') == 'panel-token':
            self.tokens.append(attrs.get('content'))


class StaticPageTests(unittest.TestCase):
    def test_page_loads_one_module_entry_and_stylesheet(self):
        page = PageAssets()
        page.feed((STATIC / 'index.html').read_text())
        self.assertEqual(page.tokens, ['__PANEL_TOKEN__'])
        self.assertEqual(set(page.assets), {'/app.mjs', '/style.css'})
        self.assertEqual(page.scripts, ['module'])
        for asset in page.assets:
            self.assertIsNotNone(static_file(asset), asset)

    def test_every_module_parses_and_imports_only_served_files(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required by the repository test environment')
        modules = sorted(STATIC.glob('*.mjs'))
        self.assertIn(STATIC / 'app.mjs', modules)
        imported = set()
        for module in modules:
            result = CliRunner().run((str(Path(node).resolve()), '--check', str(module)))
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in IMPORT.findall(module.read_text()):
                self.assertTrue(STATIC_NAME.fullmatch(name), f'{module.name} imports unservable {name}')
                self.assertTrue((STATIC / name).is_file(), f'{module.name} imports missing {name}')
                imported.add(name)
        self.assertEqual({module.name for module in modules} - imported, {'app.mjs'})

    def test_static_names_are_restricted_to_page_assets(self):
        self.assertEqual(static_file('/'), ('index.html', 'text/html; charset=utf-8'))
        self.assertEqual(static_file('/app.mjs'), ('app.mjs', 'text/javascript; charset=utf-8'))
        self.assertEqual(static_file('/style.css'), ('style.css', 'text/css; charset=utf-8'))
        for path in ('/index.html', '/../server.py', '/App.mjs', '/missing.mjs', '/style.css/', '/app.js', '/tests'):
            self.assertIsNone(static_file(path), path)

    def test_server_serves_modules_and_health_reports_source_changes(self):
        routes = PanelRoutes(runner=FakeRunner([]))
        try:
            with running_server(routes) as server:
                status, headers, body = request(server, '/app.mjs', headers={'Host': HOST})
                self.assertEqual(status, 200)
                self.assertTrue(headers['Content-Type'].startswith('text/javascript'))
                self.assertIn('import', body)
                status, _, _ = request(server, '/server.py', headers={'Host': HOST})
                self.assertEqual(status, 404)
                status, _, body = request(server, '/healthz', headers={'Host': HOST})
                self.assertEqual(status, 200)
                self.assertIs(json.loads(body)['source_changed'], False)
        finally:
            routes.close()
        self.assertTrue(source_changed(0))
        self.assertFalse(source_changed(time.time() + 60))


if __name__ == '__main__':
    unittest.main()
