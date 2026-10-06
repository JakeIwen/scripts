import re
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from pi.apps.video_library import routes
from pi.tests.media import test_video_library_server as server_tests


REPOSITORY_ROOT = server_tests.REPOSITORY_ROOT


class ServedPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "link" and attributes.get("href"):
            self.assets.append(attributes["href"])
        elif tag == "script" and attributes.get("src"):
            self.assets.append(attributes["src"])
        elif tag == "input" and attributes.get("id"):
            self.inputs[attributes["id"]] = attributes


class ServedVideoApiRouteTests(unittest.TestCase):
    # Reuse the fixture without inheriting and rediscovering its tests.
    setUp = server_tests.ApiRouteTests.setUp
    post = server_tests.ApiRouteTests.post

    def test_served_javascript_posts_use_secured_post_routes(self):
        with self.client.get("/static/video_library.js") as javascript_response:
            self.assertEqual(javascript_response.status_code, 200)
            javascript = javascript_response.get_data(as_text=True)
        endpoints = set(re.findall(r'''\bpost\(\s*["']([^"']+)["']''', javascript))
        self.assertTrue(endpoints, "served JavaScript did not expose any literal post calls")
        self.assertTrue(
            {
                "play",
                "control",
                "seek",
                "position",
                "progress",
                "sleep",
            }.issubset(endpoints)
        )

        request_data = {
            "control": {"action": "pause"},
            "fullscreen": {},
            "play": {"item": self.item.id},
            "position": {"position": "0"},
            "progress": {"item": self.item.id, "action": "watched"},
            "rate": {"value": "1"},
            "rescan": {},
            "seek": {"seconds": "0"},
            "sleep": {"minutes": "0"},
            "surprise": {"type": "invalid"},
            "volume": {"value": "47"},
        }
        self.assertEqual(set(request_data), endpoints)
        for endpoint in sorted(endpoints):
            with self.subTest(endpoint=endpoint):
                path = f"/api/{endpoint}"
                self.assertTrue(
                    any(
                        rule.rule == path and "POST" in rule.methods
                        for rule in routes.app.url_map.iter_rules()
                    ),
                    f"{path} is not bound to POST",
                )
                missing_header_data = dict(request_data[endpoint])
                routes.active_service.reset_mock()
                missing_header = self.post(
                    endpoint,
                    missing_header_data,
                    headers={"Origin": "http://localhost"},
                )
                self.assertEqual(missing_header.status_code, 403)
                self.assertEqual(
                    missing_header.get_json()["message"], "video control header missing"
                )
                routes.active_service.assert_not_called()

                routes.active_service.reset_mock()
                secured = self.post(
                    endpoint,
                    dict(request_data[endpoint]),
                    headers={
                        "Origin": "http://localhost",
                        "X-Van-Video": "1",
                    },
                )
                self.assertIn(secured.status_code, (200, 400, 404, 409))

        # The runtime policy only requires X-Van-Video for browser-originated
        # mutations; CLI-style requests without Origin remain allowed.
        cli_style = self.post("control", {"action": "pause"})
        self.assertEqual(cli_style.status_code, 200)


class DeploymentApiWiringTests(unittest.TestCase):
    def test_template_static_assets_dashboard_and_cli_use_the_same_api(self):
        client = routes.app.test_client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        parser = ServedPageParser()
        parser.feed(page.get_data(as_text=True))
        self.assertTrue(parser.assets, "served page did not expose any assets")
        self.assertIn("/static/video_library.css", parser.assets)
        self.assertIn("/static/video_library.js", parser.assets)

        expected_content_types = {
            ".css": ("text/css",),
            ".js": ("javascript",),
            ".svg": ("image/svg+xml",),
            ".webmanifest": ("application/manifest+json", "application/json"),
        }
        for asset in parser.assets:
            with self.subTest(asset=asset):
                parsed = urlsplit(asset)
                self.assertFalse(parsed.netloc, f"asset unexpectedly points off-origin: {asset}")
                with client.get(parsed.path) as response:
                    self.assertEqual(response.status_code, 200)
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                    self.assertTrue(content_type, f"asset has no content type: {asset}")
                expected = expected_content_types[Path(parsed.path).suffix]
                self.assertTrue(
                    any(token in content_type for token in expected),
                    f"unexpected content type {content_type!r} for {asset}",
                )

        volume = parser.inputs.get("volume")
        self.assertIsNotNone(volume, "served page did not render the volume input")
        self.assertEqual(volume["max"], "100")
        self.assertIn("sonos", volume["aria-label"].casefold())

        with client.get("/static/video_library.js") as javascript_response:
            self.assertEqual(javascript_response.status_code, 200)
            javascript = javascript_response.get_data(as_text=True)
        with client.get("/static/video_library.css") as stylesheet_response:
            self.assertEqual(stylesheet_response.status_code, 200)
            stylesheet = stylesheet_response.get_data(as_text=True)

        # Browser execution is intentionally out of scope here: API route tests
        # cover behavior, while these served-text checks pin only the request
        # header and the audio/player volume field semantics.
        self.assertIn('"X-Van-Video": "1"', javascript)
        self.assertIn("const audio = payload.audio || {};", javascript)
        self.assertIn('$("volume").disabled = !audio.available;', javascript)
        self.assertIn("Number.isFinite(audio.volume)", javascript)
        self.assertNotIn("Number.isFinite(player.volume)", javascript)

        # This is a narrow served styling contract, not a CSS behavior harness.
        self.assertIn(".player-shell", stylesheet)

    @staticmethod
    def _extract_bash_function(source, name):
        lines = source.splitlines(keepends=True)
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith(f"{name}() {{")
            ),
            None,
        )
        if start is None:
            raise AssertionError(f"{name}() definition not found")

        # These bashrc helpers end at an unindented closing brace. Extract
        # only the definition, never surrounding top-level shell statements.
        for index in range(start + 1, len(lines)):
            if lines[index].strip("\r\n") == "}":
                return "".join(lines[start : index + 1])
        raise AssertionError(f"{name}() definition has no top-level closing brace")

    def _load_bashrc_video_helpers(self):
        bashrc = (REPOSITORY_ROOT / "pi" / ".bashrc").read_text(encoding="utf-8")
        videoapi = next(
            (
                line
                for line in bashrc.splitlines()
                if re.fullmatch(r'VIDEOAPI="[^"]+"', line)
            ),
            None,
        )
        self.assertIsNotNone(videoapi, "VIDEOAPI assignment not found")
        vlcmd = self._extract_bash_function(bashrc, "vlcmd")
        vid = self._extract_bash_function(bashrc, "vid")
        return videoapi, vlcmd, vid

    @staticmethod
    def _run_bashrc_video_helpers(videoapi, vlcmd, vid):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake_curl = root / "curl"
            fake_curl.write_text(
                "#!/bin/bash\nprintf '%s\\0' \"$@\"\nprintf '\\n'\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o700)
            script = "\n".join(
                (
                    "set -e",
                    videoapi,
                    vlcmd,
                    vid,
                    "wake_display() { return 97; }",
                    "dbus-send() { return 98; }",
                    "vlcmd PlayPause",
                    "vlcmd Play",
                    "vlcmd Pause",
                    "vlcmd Next",
                    "vlcmd Previous",
                    "vlcmd Stop",
                    'VIDEOAPI="http://override.example/api"',
                    "vlcmd PlayPause",
                    'VIDEOAPI="http://localhost:8789/api"',
                    "vid -p",
                )
            )
            env = {
                "HOME": str(root / "home"),
                "PATH": str(root),
            }
            env.pop("BASH_ENV", None)
            return subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-c", script],
                cwd=root,
                env=env,
                capture_output=True,
                check=True,
                timeout=10,
            )

    @staticmethod
    def _curl_calls(completed):
        return [
            record.split(b"\0")[:-1]
            for record in completed.stdout.splitlines()
            if record
        ]

    def _assert_default_video_action_calls(self, calls):
        expected_actions = ("toggle", "play", "pause", "next", "previous", "stop")
        self.assertEqual(len(calls), len(expected_actions) + 2)
        for call, action in zip(calls[: len(expected_actions)], expected_actions):
            self.assertEqual(
                call,
                [
                    b"-sS",
                    b"-X",
                    b"POST",
                    b"http://localhost:8789/api/control",
                    b"--data-urlencode",
                    f"action={action}".encode(),
                ],
            )

    def _assert_override_and_vid_calls(self, calls):
        self.assertEqual(
            calls[-2],
            [
                b"-sS",
                b"-X",
                b"POST",
                b"http://override.example/api/control",
                b"--data-urlencode",
                b"action=toggle",
            ],
        )
        self.assertEqual(
            calls[-1],
            [
                b"-sS",
                b"-X",
                b"POST",
                b"http://localhost:8789/api/control",
                b"--data-urlencode",
                b"action=toggle",
            ],
        )

    def test_bashrc_video_helpers_use_the_video_api(self):
        videoapi, vlcmd, vid = self._load_bashrc_video_helpers()
        completed = self._run_bashrc_video_helpers(videoapi, vlcmd, vid)
        calls = self._curl_calls(completed)
        self._assert_default_video_action_calls(calls)
        self._assert_override_and_vid_calls(calls)


if __name__ == "__main__":
    unittest.main()
