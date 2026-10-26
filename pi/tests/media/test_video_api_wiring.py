import re
import subprocess
import tempfile
import unittest
from pathlib import Path



REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class DeploymentApiWiringTests(unittest.TestCase):
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
        pp = next(line for line in bashrc.splitlines() if line.startswith("pp() {"))
        return videoapi, vlcmd, vid, pp

    @staticmethod
    def _run_bashrc_video_helpers(videoapi, vlcmd, vid, pp):
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
                    "shopt -s expand_aliases",
                    pp,
                    "vantv_active() { return 1; }",
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
                    "pp",
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
        self.assertEqual(len(calls), len(expected_actions) + 3)
        for call, action in zip(calls[: len(expected_actions)], expected_actions):
            self.assertIn(b"POST", call)
            self.assertIn(b"http://localhost:8789/api/control", call)
            self.assertIn(f"action={action}".encode(), call)

    def _assert_override_and_vid_calls(self, calls):
        self.assertIn(b"http://override.example/api/control", calls[-3])
        self.assertIn(b"http://localhost:8789/api/control", calls[-1])
        for call in calls[-3:]:
            self.assertIn(b"POST", call)
            self.assertIn(b"action=toggle", call)

    def test_bashrc_video_helpers_use_the_video_api(self):
        videoapi, vlcmd, vid, pp = self._load_bashrc_video_helpers()
        completed = self._run_bashrc_video_helpers(videoapi, vlcmd, vid, pp)
        calls = self._curl_calls(completed)
        self._assert_default_video_action_calls(calls)
        self._assert_override_and_vid_calls(calls)

    def test_btt_vantv_routes_do_not_call_vlc_or_schedule_shell_repairs(self):
        source = (REPOSITORY_ROOT / "pi" / ".bashrc").read_text()
        _videoapi, vlcmd, _vid, pp = self._load_bashrc_video_helpers()
        script = "\n".join((
            "set -e; shopt -s expand_aliases", pp, vlcmd,
            self._extract_bash_function(source, "vantv_vlcmd"),
            "vantv_active() { return 0; }",
            'vantv_ctl() { printf "%s\\n" "$1"; }',
            "curl() { return 97; }; wake_display() { return 98; }",
            "pp", "vlcmd Play", "vlcmd PlayPause",
        ))
        result = subprocess.run(
            ["/bin/bash", "-c", script], env={"PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, check=True, timeout=5,
        )
        self.assertEqual(result.stdout.splitlines(), ["toggle", "resume", "toggle"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
