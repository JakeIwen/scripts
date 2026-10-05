import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pi import deploy_python


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


DRIVER = r'''
import importlib.util
import json
import os
from html.parser import HTMLParser
from pathlib import Path
import runpy
import socket
import subprocess
import sys

import flask

stage = Path(sys.argv[1]).resolve()
repository = Path(sys.argv[2]).resolve()
record_calls = []
run_calls = []
dispatches = []


def fake_record(path, *args, **kwargs):
    record_calls.append({"path": str(path), "args": list(args), "kwargs": kwargs})


def fake_run(self, *args, **kwargs):
    run_calls.append({"args": list(args), "kwargs": kwargs})


def blocked(name):
    def reject(*args, **kwargs):
        raise AssertionError(f"blocked side effect: {name}")

    return reject


flask.Flask.run = fake_run
from pi import package_runtime

package_runtime.record_running_release = fake_record
from pi.apps.video_library import service as service_owner


class SafeVlc:
    def snapshot(self):
        return {
            "available": False,
            "state": "OFFLINE",
            "error": "boot smoke VLC disabled",
            "position": 0.0,
            "duration": 0.0,
        }

    def room_preparing(self):
        return False


class SafeSonos:
    def snapshot(self):
        return {
            "available": False,
            "device": None,
            "volume": None,
            "muted": None,
            "error": "boot smoke Sonos disabled",
        }


service_owner.VlcController = SafeVlc
service_owner.SonosVolumeController = SafeSonos
service_owner.default_sources = lambda: ()

real_run_module = runpy.run_module


def capture_run_module(name, *args, **kwargs):
    lookup_name = (
        name + ".__main__"
        if name == "pi.apps.video_library"
        else name
    )
    spec = importlib.util.find_spec(lookup_name)
    dispatches.append({"name": name, "origin": str(spec.origin) if spec else None})
    return real_run_module(name, *args, **kwargs)


runpy.run_module = capture_run_module
for name in ("run", "Popen", "call", "check_call", "check_output"):
    setattr(subprocess, name, blocked("subprocess." + name))
os.system = blocked("os.system")
os.popen = blocked("os.popen")
socket.create_connection = blocked("socket.create_connection")
socket.socket = blocked("socket.socket")

runpy.run_module("pi.apps.video_library", run_name="__main__")
from pi.apps.video_library import routes as routes_owner
from pi.apps.video_library import service as service_owner

app = routes_owner.app
client = app.test_client()


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value and value.startswith("/"):
                self.assets.add(value)


old_smoke_paths = (
    "/",
    "/static/video_library.js",
    "/static/video_library.css",
    "/manifest.webmanifest",
    "/app-icon.svg",
    "/api/status",
)
status_codes = {}
content_types = {}
root = client.get("/")
status_codes["/"] = root.status_code
content_types["/"] = root.content_type
assets = AssetParser()
assets.feed(root.get_data(as_text=True))
root.close()
rendered_assets = sorted(assets.assets)
paths = list(dict.fromkeys((*old_smoke_paths, *rendered_assets)))
api_status = None
for path in paths:
    if path == "/":
        continue
    response = client.get(path)
    status_codes[path] = response.status_code
    content_types[path] = response.content_type
    if path == "/api/status":
        api_status = response.get_json()
    response.close()

service = service_owner.active_service()
thread_started = bool(getattr(service, "thread", None))
if service.thread is not None:
    service.stop_event.set()
    service.thread.join(timeout=2)

loaded_modules = {}
for name, module in sorted(sys.modules.items()):
    if not (name == "pi" or name.startswith("pi.") or name == "shared" or name.startswith("shared.")):
        continue
    filename = getattr(module, "__file__", None)
    if not filename:
        continue
    path = Path(filename).resolve()
    if not path.is_relative_to(stage):
        raise AssertionError(f"module {name} escaped staged release: {path}")
    if path.is_relative_to(repository):
        raise AssertionError(f"module {name} imported from checkout: {path}")
    loaded_modules[name] = str(path)

if Path(sys.modules["pi"].__path__[0]).resolve() != stage / "pi":
    raise AssertionError("pi package did not resolve to staged release")

print(
    json.dumps(
        {
            "dispatches": dispatches,
            "loaded_modules": loaded_modules,
            "package_path": str(Path(sys.modules["pi"].__path__[0]).resolve()),
            "record_calls": record_calls,
            "rendered_assets": rendered_assets,
            "runs": run_calls,
            "status_codes": status_codes,
            "content_types": content_types,
            "api_status": api_status,
            "service_type": type(service).__module__ + "." + type(service).__name__,
            "thread_started": thread_started,
        },
        sort_keys=True,
    )
)
'''


class VideoPackageBootTests(unittest.TestCase):
    def test_video_package_boot_uses_staged_release(self):
        plan = deploy_python.build_plan(
            REPOSITORY_ROOT,
            mode="stage",
            selected=["video-library.service"],
        )
        with tempfile.TemporaryDirectory(prefix="video-package-boot-") as directory:
            target = Path(directory)
            release = target / "release"
            archive = io.BytesIO()
            deploy_python.make_archive(plan, REPOSITORY_ROOT, archive)
            archive.seek(0)
            manifest = deploy_python.unpack_archive(archive, release)
            release = release.resolve()
            self.assertEqual(manifest, plan["manifest"])
            self.assertFalse(release.is_relative_to(REPOSITORY_ROOT))

            home = target / "home"
            state = target / "state"
            runtime = target / "runtime"
            temporary = target / "tmp"
            poisoned = target / "poisoned"
            qbt_temp = target / "qbt-temp"
            qbt_final = target / "qbt-final"
            for path in (home, state, runtime, temporary, poisoned, qbt_temp, qbt_final):
                path.mkdir(parents=True)
            (poisoned / "pi.py").write_text(
                "raise AssertionError('poisoned cwd module was imported')\n",
                encoding="utf-8",
            )

            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "TMPDIR": str(temporary),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(
                    (str(release), str(release / "shared" / "python"))
                ),
                "VAN_VIDEO_STATE_PATH": str(state / "progress.sqlite3"),
                "VAN_VIDEO_LEGACY_POSITIONS": str(state / "vlc-positions.txt"),
                "VAN_VIDEO_XDG_RUNTIME_DIR": str(runtime),
                "VAN_VIDEO_SCAN_INTERVAL": "3600",
                "VAN_VIDEO_POLL_INTERVAL": "3600",
                "VAN_VIDEO_QBITTORRENT_TIMEOUT": "0.2",
                "VAN_VIDEO_QBITTORRENT_TEMP_ROOTS": str(qbt_temp),
                "VAN_VIDEO_QBITTORRENT_FINAL_ROOTS": str(qbt_final),
                "VAN_VIDEO_VLC": str(target / "vlc"),
                "VAN_VIDEO_SYSTEMD_RUN": str(target / "systemd-run"),
                "VAN_VIDEO_SYSTEMCTL": str(target / "systemctl"),
                "VAN_VIDEO_XSET": str(target / "xset"),
                "VAN_VIDEO_SONOS_SETUP": str(target / "sns.sh"),
                "VAN_VIDEO_MKVMERGE": str(target / "mkvmerge"),
                "VAN_VIDEO_PKILL": str(target / "pkill"),
                "VAN_VIDEO_DISPLAY": ":999",
            }
            result = subprocess.run(
                [sys.executable, "-P", "-B", "-c", DRIVER, str(release), str(REPOSITORY_ROOT)],
                cwd=poisoned,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)

            old_smoke_status = {
                "/": 200,
                "/static/video_library.js": 200,
                "/static/video_library.css": 200,
                "/manifest.webmanifest": 200,
                "/app-icon.svg": 200,
                "/api/status": 200,
            }
            self.assertEqual(
                {
                    path: payload["status_codes"][path]
                    for path in old_smoke_status
                },
                old_smoke_status,
            )
            rendered_assets = set(payload["rendered_assets"])
            self.assertTrue(rendered_assets)
            self.assertTrue(rendered_assets.issubset(payload["status_codes"]))
            self.assertTrue(
                all(payload["status_codes"][path] == 200 for path in rendered_assets)
            )
            content_types = payload["content_types"]
            self.assertTrue(content_types["/"].startswith("text/html"))
            self.assertIn("javascript", content_types["/static/video_library.js"])
            self.assertTrue(content_types["/static/video_library.css"].startswith("text/css"))
            self.assertTrue(
                content_types["/manifest.webmanifest"].startswith(
                    "application/manifest+json"
                )
            )
            self.assertTrue(content_types["/app-icon.svg"].startswith("image/svg+xml"))
            self.assertTrue(content_types["/api/status"].startswith("application/json"))
            self.assertIsInstance(payload["api_status"], dict)
            self.assertTrue(payload["api_status"]["ok"])

            self.assertEqual(
                payload["record_calls"],
                [{"args": [], "kwargs": {}, "path": "/run/video-library"}],
            )
            self.assertEqual(len(payload["runs"]), 1)
            self.assertEqual(
                payload["runs"][0],
                {"args": [], "kwargs": {"host": "0.0.0.0", "port": 8789, "threaded": True}},
            )
            self.assertEqual(
                payload["dispatches"],
                [
                    {
                        "name": "pi.apps.video_library",
                        "origin": str(
                            release
                            / "pi"
                            / "apps"
                            / "video_library"
                            / "__main__.py"
                        ),
                    },
                    {
                        "name": "pi.apps.video_library.video_library_server",
                        "origin": str(
                            release
                            / "pi"
                            / "apps"
                            / "video_library"
                            / "video_library_server.py"
                        ),
                    },
                ],
            )
            self.assertEqual(payload["package_path"], str(release / "pi"))
            self.assertEqual(
                payload["service_type"],
                "pi.apps.video_library.service.VideoService",
            )
            self.assertTrue(payload["thread_started"])
            self.assertTrue(payload["loaded_modules"])
            for name, filename in payload["loaded_modules"].items():
                path = Path(filename)
                self.assertTrue(path.is_relative_to(release), (name, path))
                self.assertFalse(path.is_relative_to(REPOSITORY_ROOT), (name, path))
                self.assertIn(path.relative_to(release).as_posix(), manifest["files"])


if __name__ == "__main__":
    unittest.main()
