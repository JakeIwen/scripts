"""Photo inbox behavior with no Apple Events or writes to real terminal sessions."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from macbook.scripts import llm_photo_inbox as inbox


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "macbook/scripts/llm_photo_inbox.py"


def tiny_png(width=3, height=2):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    rows = (b"\0" + b"\xff\0\0" * width) * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class PhotoRulesTests(unittest.TestCase):
    def test_magic_bytes(self):
        samples = [(b"\xff\xd8\xffrest", "jpg"), (tiny_png(), "png"),
                   (b"GIF87a", "gif"), (b"GIF89a", "gif"),
                   (b"RIFF\x10\0\0\0WEBP", "webp")]
        samples.extend((b"\0\0\0\x18ftyp" + brand + b"\0" * 8,
                        "heif" if brand in {b"mif1", b"msf1"} else "heic")
                       for brand in inbox.HEIF_BRANDS)
        for data, extension in samples:
            with self.subTest(data=data):
                self.assertEqual(inbox.classify_image(data), extension)

    def test_rejects_nonimages_and_incomplete_signatures(self):
        for data in (b"", b"hello", b"\xff\xd8", b"\x89PN", b"GIF",
                     b"RIFF\0\0\0\0WAVE", b"\0\0\0\x18ftypavif", b"ftypheic"):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "^not an image$"):
                    inbox.classify_image(data)

    def test_filename_has_timestamp_random_suffix_and_safe_extension(self):
        with patch.object(inbox.secrets, "token_hex", return_value="ab12") as token:
            name = inbox.photo_filename("jpg", datetime(2026, 10, 28, 0, 30, 12))
        self.assertEqual(name, "20261028-003012-ab12.jpg")
        token.assert_called_once_with(2)
        for extension in inbox.IMAGE_EXTENSIONS:
            self.assertRegex(inbox.photo_filename(extension),
                             r"^\d{8}-\d{6}-[0-9a-f]{4}\.[a-z]+$")
        with self.assertRaises(ValueError):
            inbox.photo_filename("png; touch unwanted")

    def test_current_llm_session_wins_over_other_candidates(self):
        sessions = [inbox.Session("ttys001", "scripts (claude)"),
                    inbox.Session("/dev/ttys002", "castit")]
        target = inbox.choose_target(sessions, "/dev/ttys001", {"ttys001", "/dev/ttys002"})
        self.assertEqual(target, sessions[0])

    def test_single_candidate_wins_even_when_plain_shell_is_current(self):
        sessions = [inbox.Session("ttys001", "shell"), inbox.Session("ttys002", "scripts")]
        self.assertEqual(inbox.choose_target(sessions, "ttys001", {"ttys002"}), sessions[1])
        self.assertEqual(inbox.choose_target(sessions, None, {"/dev/ttys002"}), sessions[1])

    def test_several_candidates_without_current_llm_are_ambiguous(self):
        sessions = [inbox.Session("ttys001", "scripts"), inbox.Session("ttys002", "castit")]
        self.assertIsNone(inbox.choose_target(sessions, "ttys003", {"ttys001", "ttys002"}))

    def test_no_llm_candidate_never_selects_a_shell_or_unknown_tty(self):
        sessions = [inbox.Session("ttys001", "shell"), inbox.Session("??", "unknown")]
        for ttys in (set(), {"ttys002"}, {"?", "??"}):
            with self.subTest(ttys=ttys):
                self.assertIsNone(inbox.choose_target(sessions, "ttys001", ttys))
        self.assertIsNone(inbox.choose_target([], None, {"ttys001"}))

    def test_applescript_literal_escapes_special_characters(self):
        self.assertEqual(inbox.applescript_string('a\\b"c\n\r\t'), '"a\\\\b\\"c\\n\\r\\t"')
        self.assertEqual(inbox.applescript_string(""), '""')


class PhotoAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        home = patch.dict(os.environ, {"HOME": str(self.home)})
        home.start()
        self.addCleanup(home.stop)
        # Any unmocked process call here is a test bug, not permission to contact iTerm.
        commands = patch.object(inbox.subprocess, "run", side_effect=AssertionError("unfaked command"))
        self.run_command = commands.start()
        self.addCleanup(commands.stop)

    def completed(self, output="", status=0):
        self.run_command.side_effect = None
        self.run_command.return_value = subprocess.CompletedProcess([], status, stdout=output)

    def test_save_is_private_and_does_not_overwrite_a_collision(self):
        data = tiny_png()
        with patch.object(inbox, "photo_filename", side_effect=["first.png", "first.png", "next.png"]):
            first = inbox._save_photo(data, "png")
            second = inbox._save_photo(data + b"second", "png")
        self.assertEqual(first.parent, self.home / ".agent/inbox")
        self.assertEqual(first.read_bytes(), data)
        self.assertEqual(second.read_bytes(), data + b"second")
        self.assertEqual(stat.S_IMODE(first.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o600)

    def test_ps_accepts_only_named_llm_binaries_with_real_ttys(self):
        self.completed("ttys001 /usr/local/bin/claude\n/dev/ttys002 /opt/Codex App/codex\n"
                       "? codex\n?? claude\nttys003 zsh\nttys004 claude-worker\n"
                       "incomplete\nttys005 /usr/bin/python3\n")
        self.assertEqual(inbox._llm_ttys(), {"ttys001": "claude", "ttys002": "codex"})
        self.assertEqual(self.run_command.call_args.args[0], ["/bin/ps", "-axo", "tty=,comm="])

    def test_ps_failure_is_no_llm_ttys(self):
        self.run_command.side_effect = subprocess.TimeoutExpired("ps", 5)
        self.assertEqual(inbox._llm_ttys(), {})

    def test_stopped_iterm_never_runs_osascript_for_listing_or_writing(self):
        self.completed(status=1)
        self.assertEqual(inbox._iterm_sessions(), ([], None))
        self.assertFalse(inbox._write_path(inbox.Session("ttys001", "scripts"), Path("/photo.png")))
        for call in self.run_command.call_args_list:
            self.assertEqual(call.args[0], ["/usr/bin/pgrep", "-x", "iTerm2"])

    def test_iterm_names_are_trimmed_but_job_suffix_and_internal_spaces_remain(self):
        self.completed("/dev/ttys002\n/dev/ttys001\t  scripts  (claude)  \n"
                       "/dev/ttys002\tcastit\nmalformed\n")
        with patch.object(inbox, "_iterm_running", return_value=True):
            sessions, current = inbox._iterm_sessions()
        self.assertEqual(sessions, [inbox.Session("ttys001", "scripts  (claude)"),
                                    inbox.Session("ttys002", "castit")])
        self.assertEqual(current, "ttys002")

    def test_iterm_without_current_window_can_still_list_sessions(self):
        self.completed("\n/dev/ttys001\tscripts\n")
        with patch.object(inbox, "_iterm_running", return_value=True):
            self.assertEqual(inbox._iterm_sessions(), ([inbox.Session("ttys001", "scripts")], None))

    def test_automation_failure_is_no_iterm_sessions(self):
        self.run_command.side_effect = subprocess.CalledProcessError(1, "osascript")
        with patch.object(inbox, "_iterm_running", return_value=True):
            self.assertEqual(inbox._iterm_sessions(), ([], None))

    def test_write_addresses_tty_and_explicitly_omits_newline(self):
        self.completed("sent\n")
        path = Path('/private/photo "quoted"\\file.png')
        with patch.object(inbox, "_iterm_running", return_value=True):
            self.assertTrue(inbox._write_path(inbox.Session("ttys001", "scripts"), path))
        command = self.run_command.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn('if tty of s is "/dev/ttys001" then', command[2])
        self.assertIn("tell s to write text " + inbox.applescript_string(str(path) + " ") + " newline no",
                      command[2])

    def test_write_failure_or_vanished_session_reports_failure(self):
        with patch.object(inbox, "_iterm_running", return_value=True):
            self.completed("")
            self.assertFalse(inbox._write_path(inbox.Session("ttys001", "scripts"), Path("/x.png")))
            self.run_command.side_effect = subprocess.TimeoutExpired("osascript", 5)
            self.assertFalse(inbox._write_path(inbox.Session("ttys001", "scripts"), Path("/x.png")))

    @patch.object(inbox, "_image_edge", return_value=4096)
    def test_heif_conversion_deletes_original_only_after_success(self, _edge):
        original = inbox._save_photo(b"original heif", "heif")

        def convert(command, **kwargs):
            self.assertEqual(command[:4], ["/usr/bin/sips", "-s", "format", "jpeg"])
            self.assertIn("--resampleHeightWidthMax", command)
            destination = Path(command[command.index("--out") + 1])
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            destination.write_bytes(b"\xff\xd8\xffjpeg")
            return subprocess.CompletedProcess(command, 0)

        self.run_command.side_effect = convert
        result = inbox._normalize_photo(original)
        self.assertEqual(result.suffix, ".jpg")
        self.assertTrue(result.exists())
        self.assertFalse(original.exists())
        self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o600)

    @patch.object(inbox, "_image_edge", return_value=4096)
    def test_failed_heic_conversion_keeps_original_and_removes_partial_output(self, _edge):
        original = inbox._save_photo(b"original heic", "heic")
        self.run_command.side_effect = subprocess.CalledProcessError(1, "sips")
        self.assertEqual(inbox._normalize_photo(original), original)
        self.assertEqual(original.read_bytes(), b"original heic")
        self.assertEqual(list(original.parent.iterdir()), [original])

    @patch.object(inbox, "_image_edge", return_value=4096)
    def test_empty_conversion_result_does_not_delete_original(self, _edge):
        original = inbox._save_photo(b"original heic", "heic")
        self.completed()
        self.assertEqual(inbox._normalize_photo(original), original)
        self.assertEqual(list(original.parent.iterdir()), [original])

    @patch.object(inbox, "_image_edge", return_value=4096)
    def test_png_jpeg_gif_and_webp_use_in_place_shrink_without_conversion(self, _edge):
        self.completed()
        for extension in ("png", "jpg", "gif", "webp"):
            with self.subTest(extension=extension):
                original = inbox._save_photo(b"image", extension)
                self.assertEqual(inbox._normalize_photo(original), original)
                self.assertEqual(self.run_command.call_args.args[0], [
                    "/usr/bin/sips", "--resampleHeightWidthMax", "2048", str(original)])

    @patch.object(inbox, "_image_edge", return_value=4096)
    def test_failed_shrink_still_retains_original(self, _edge):
        original = inbox._save_photo(tiny_png(), "png")
        self.run_command.side_effect = FileNotFoundError("sips")
        self.assertEqual(inbox._normalize_photo(original), original)
        self.assertEqual(original.read_bytes(), tiny_png())


    def test_sips_dimension_query_reports_longest_edge(self):
        self.completed("/image.png\n  pixelWidth: 4096\n  pixelHeight: 2\n")
        self.assertEqual(inbox._image_edge(Path("/image.png")), 4096)
        self.assertEqual(self.run_command.call_args.args[0], [
            "/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", "/image.png"])

    def test_unreadable_dimensions_keep_saved_file(self):
        self.completed("/image.png\n  pixelWidth: <nil>\n  pixelHeight: <nil>\n")
        original = inbox._save_photo(tiny_png(), "png")
        self.assertEqual(inbox._normalize_photo(original), original)
        self.assertEqual(original.read_bytes(), tiny_png())


class PhotoMainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.writes = []
        self.sessions = [inbox.Session("ttys001", "scripts")]
        for replacement in (
            patch.dict(os.environ, {"HOME": str(self.home)}),
            patch.object(inbox, "_llm_ttys", return_value={"ttys001": "claude"}),
            patch.object(inbox, "_iterm_sessions", side_effect=lambda: (self.sessions, "ttys001")),
            patch.object(inbox, "_write_path", side_effect=self.write_path),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def write_path(self, session, path):
        self.writes.append((session, path))
        return True

    def run_main(self, data, arguments=()):
        stdout = io.StringIO()
        stream = io.TextIOWrapper(io.BytesIO(data))
        with stream, patch.object(inbox.sys, "stdin", stream), redirect_stdout(stdout):
            status = inbox.main(list(arguments))
        return status, stdout.getvalue()

    def saved_path(self):
        paths = list((self.home / ".agent/inbox").iterdir())
        self.assertEqual(len(paths), 1)
        return paths[0]

    @unittest.skipUnless(sys.platform == "darwin", "native /usr/bin/sips requires macOS")
    def test_main_saves_tiny_png_and_runs_real_sips_before_inserting_path(self):
        with patch.object(inbox.subprocess, "run", wraps=subprocess.run) as command:
            status, output = self.run_main(tiny_png(4096, 2))
        path = self.saved_path()
        self.assertEqual(status, 0)
        self.assertRegex(path.name, r"^\d{8}-\d{6}-[0-9a-f]{4}\.png$")
        self.assertEqual(inbox.classify_image(path.read_bytes()), "png")
        self.assertEqual(struct.unpack(">II", path.read_bytes()[16:24]), (2048, 1))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(command.call_args.args[0], [
            "/usr/bin/sips", "--resampleHeightWidthMax", "2048", str(path)])
        self.assertEqual(self.writes, [(self.sessions[0], path)])
        self.assertEqual(output, f"→ scripts (claude): {path}\n")

    @unittest.skipUnless(sys.platform == "darwin", "native /usr/bin/sips requires macOS")
    def test_real_sips_does_not_enlarge_small_images(self):
        status, output = self.run_main(tiny_png(), ["--dry-run"])
        path = self.saved_path()
        self.assertEqual(status, 0)
        self.assertEqual(struct.unpack(">II", path.read_bytes()[16:24]), (3, 2))
        self.assertEqual(output, f"would send to scripts (claude): {path}\n")
        self.assertEqual(self.writes, [])

    def test_dry_run_still_saves_and_normalizes_but_never_writes(self):
        with patch.object(inbox, "_normalize_photo", side_effect=lambda path: path) as normalize:
            status, output = self.run_main(tiny_png(), ["--dry-run"])
        path = self.saved_path()
        self.assertEqual(status, 0)
        normalize.assert_called_once_with(path)
        self.assertEqual(output, f"would send to scripts (claude): {path}\n")
        self.assertEqual(self.writes, [])

    def test_no_llm_session_is_successfully_saved_without_writing(self):
        self.sessions.clear()
        with patch.object(inbox, "_normalize_photo", side_effect=lambda path: path):
            status, output = self.run_main(tiny_png())
        self.assertEqual(status, 0)
        self.assertEqual(output, f"saved, no LLM tab in front: {self.saved_path()}\n")
        self.assertEqual(self.writes, [])

    def test_ambiguous_sessions_preserve_names_in_one_notification(self):
        self.sessions.append(inbox.Session("ttys002", "castit (codex)"))
        with patch.object(inbox, "_llm_ttys", return_value={"ttys001": "claude", "ttys002": "codex"}), \
                patch.object(inbox, "_iterm_sessions", return_value=(self.sessions, "ttys003")), \
                patch.object(inbox, "_normalize_photo", side_effect=lambda path: path):
            status, output = self.run_main(tiny_png())
        self.assertEqual(status, 0)
        self.assertEqual(output, f"saved, several LLM tabs (scripts, castit (codex)): {self.saved_path()}\n")
        self.assertEqual(self.writes, [])

    def test_write_failure_does_not_turn_a_saved_photo_into_an_error(self):
        with patch.object(inbox, "_write_path", return_value=False), \
                patch.object(inbox, "_normalize_photo", side_effect=lambda path: path):
            status, output = self.run_main(tiny_png())
        self.assertEqual(status, 0)
        self.assertEqual(output, f"saved, could not write to scripts (claude): {self.saved_path()}\n")

    def test_invalid_or_oversized_input_is_one_line_and_does_not_save(self):
        with patch.object(inbox, "MAX_IMAGE_BYTES", 100):
            for data, message in ((b"", "empty image"), (b"text", "not an image"),
                                  (b"\x89PNG" + b"x" * 97, "image exceeds 60 MB")):
                with self.subTest(message=message):
                    status, output = self.run_main(data)
                    self.assertEqual(status, 1)
                    self.assertEqual(output, message + "\n")
        self.assertFalse((self.home / ".agent/inbox").exists())
        self.assertEqual(self.writes, [])

    def test_isolated_system_python_cli_rejects_input_without_contacting_iterm(self):
        python = "/usr/bin/python3" if sys.platform == "darwin" else sys.executable
        result = subprocess.run([python, "-I", "-B", str(SCRIPT)], input=b"not an image",
                                capture_output=True, cwd=self.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"not an image\n")
        self.assertEqual(result.stderr, b"")


class SessionLabelTests(unittest.TestCase):
    def test_job_suffix_is_dropped_and_plain_names_kept(self):
        for name, expected in (
            ('◐ DNS vanpi-ts definition ("Kapture MCP Bridge")', "◐ DNS vanpi-ts definition"),
            ('scripts ("claude")  ', "scripts"),
            ("castit (codex)", "castit (codex)"),
            ('say "hi" (tab)', 'say "hi" (tab)'),
        ):
            with self.subTest(name=name):
                self.assertEqual(inbox.session_label(name), expected)


if __name__ == "__main__":
    unittest.main()
