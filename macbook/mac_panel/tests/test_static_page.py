from html.parser import HTMLParser
from pathlib import Path
import shutil
import unittest

from cli_runner import CliRunner
from panel_paths import DIRECTORY


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.tokens = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'script':
            self.assets.append(attrs.get('src'))
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.assets.append(attrs.get('href'))
        if tag == 'meta' and attrs.get('name') == 'panel-token':
            self.tokens.append(attrs.get('content'))


class StaticPageTests(unittest.TestCase):
    def test_local_assets_exist_and_javascript_parses(self):
        page = PageAssets()
        page.feed((DIRECTORY / 'static/index.html').read_text())
        self.assertEqual(page.tokens, ['__PANEL_TOKEN__'])
        self.assertEqual(set(page.assets), {'/app.js', '/style.css'})
        for asset in page.assets:
            self.assertTrue((DIRECTORY / 'static' / asset.lstrip('/')).is_file())
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required by the repository test environment')
        result = CliRunner().run((str(Path(node).resolve()), '--check', str(DIRECTORY / 'static/app.js')))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
