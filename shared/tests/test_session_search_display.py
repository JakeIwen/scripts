import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
import unicodedata
import unittest
from pathlib import Path


DISPLAY = Path(__file__).parents[1] / "sh" / "session_search_display.py"


def display_width(text):
    text = text.replace("\x1b[1;36m", "").replace("\x1b[0m", "")
    return sum(0 if unicodedata.combining(character) else
               2 if unicodedata.east_asian_width(character) in ("F", "W") else 1
               for character in text)


def record(provider, cwd, modified, matched_at, count, name, session, context):
    return {
        "provider": provider,
        "cwd": cwd,
        "modified": modified,
        "matched_at": matched_at,
        "count": count,
        "header": "age | " + name,
        "resume": session,
        "context": context,
    }


class SessionSearchDisplayTests(unittest.TestCase):
    def run_display(self, records, *args):
        source = "".join(json.dumps(item) + "\n" for item in records)
        return subprocess.run(
            [sys.executable, str(DISPLAY), "--group", *args],
            input=source,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_plain_grouped_output_aggregates_and_sorts_directory_groups(self):
        records = [
            record("claude", "/old", 10, 10, 2, "old", "resume old", "old match"),
            record("codex", "/new", 30, 30, 1, "codex", "resume c", "codex match"),
            record("claude", "/new", 20, 20, 3, "earlier", "resume a", "first"),
            record("claude", "/new", 25, 40, 4, "latest", "resume b", "latest match"),
        ]
        result = self.run_display(records)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        blocks = result.stdout.strip().split("\n\n")
        self.assertEqual([block.splitlines()[1] for block in blocks],
                         ["resume c", "resume b", "resume old"])
        self.assertEqual(blocks[1].splitlines()[0], "7 matches | age | latest")
        self.assertTrue(blocks[1].endswith("latest match"))

    def test_unknown_directories_do_not_merge_distinct_sessions(self):
        records = [
            record("claude", "", 20, 20, 2, "one", "resume one", "one"),
            record("claude", "", 10, 10, 3, "two", "resume two", "two"),
        ]
        result = self.run_display(records)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip().split("\n\n")), 2)
        self.assertIn("2 matches | age | one", result.stdout)
        self.assertIn("3 matches | age | two", result.stdout)

    def test_tty_shows_results_before_eof_then_leaves_updated_final_output(self):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 44, 0, 0))
        process = subprocess.Popen(
            [sys.executable, str(DISPLAY), "--group"],
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=subprocess.PIPE,
        )
        os.close(slave)
        try:
            first = record("claude", "/work", 10, 10, 1,
                           "first", "resume first", "first context")
            process.stdin.write((json.dumps(first) + "\n").encode())
            process.stdin.flush()
            chunks = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    chunks.append(os.read(master, 8192))
                    if b"first context" in b"".join(chunks):
                        break
            early = b"".join(chunks).decode(errors="replace")
            self.assertIn("Searching", early)
            self.assertIn("first context", early)
            self.assertIsNone(process.poll())

            update = record("claude", "/work", 20, 20, 4,
                            "newest", "resume newest", "updated context")
            process.stdin.write((json.dumps(update) + "\n").encode())
            process.stdin.close()
            chunks = []
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunks.append(os.read(master, 8192))
                    except OSError:
                        break
                if process.poll() is not None and not ready:
                    break
            process.wait(timeout=2)
            error = process.stderr.read().decode(errors="replace")
            process.stderr.close()
            output = b"".join(chunks).decode(errors="replace")
            final = output.rsplit("\x1b[J", 1)[-1].replace("\r\n", "\n")
            self.assertEqual(process.returncode, 0, error)
            self.assertIn("5 matches | age | newest", final)
            self.assertIn("resume newest", final)
            self.assertIn("updated context", final)
            self.assertNotIn("counts may increase", final)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.stderr and not process.stderr.closed:
                process.stderr.close()
            os.close(master)

    def test_tty_starts_immediately_and_clips_wide_unicode_safely(self):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 18, 0, 0))
        process = subprocess.Popen(
            [sys.executable, str(DISPLAY), "--group"],
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=subprocess.PIPE,
        )
        os.close(slave)
        try:
            ready, _, _ = select.select([master], [], [], 2)
            self.assertTrue(ready, "renderer did not show its initial progress frame")
            initial = os.read(master, 8192).decode(errors="replace")
            self.assertIn("Searching", initial)

            wide = record("claude", "/work", 10, 10, 1, "wide",
                          "resume wide", "界🙂e\u0301\x07abcdefghijk")
            process.stdin.write((json.dumps(wide) + "\n").encode())
            process.stdin.flush()
            chunks = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    chunks.append(os.read(master, 8192))
                    if b"resume wide" in b"".join(chunks):
                        break
            preview = b"".join(chunks).decode(errors="replace").replace("\r", "")
            frame = preview.rsplit("\x1b[J", 1)[-1]
            self.assertNotIn("\x07", frame)
            self.assertIn("?", frame)
            for line in frame.splitlines():
                self.assertLessEqual(display_width(line), 17, repr(line))

            process.stdin.close()
            process.wait(timeout=2)
            process.stderr.close()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.stderr and not process.stderr.closed:
                process.stderr.close()
            os.close(master)

    def test_invalid_record_reports_the_input_line(self):
        result = subprocess.run(
            [sys.executable, str(DISPLAY), "--group"], input='{"provider": 3}\n',
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("line 1 field 'provider' must be a string", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
