"""Behavior tests for session transcript path enumeration."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "sh" / "session_search_paths.py"


class SessionSearchPathsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.claude = self.root / "Claude store"
        self.codex = self.root / "Codex\nstore"

    def transcript(self, provider, relative, modified_ns):
        root = self.claude if provider == "claude" else self.codex
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
        os.utime(path, ns=(modified_ns, modified_ns))
        return path

    def run_helper(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @staticmethod
    def records(result):
        fields = result.stdout.split(b"\0")
        if fields[-1:] == [b""]:
            fields.pop()
        return [(fields[index].decode("ascii"), os.fsdecode(fields[index + 1]))
                for index in range(0, len(fields), 2)]

    def test_interleaves_providers_by_newest_mtime_and_preserves_paths(self):
        oldest = self.transcript("claude", "projects/a/old.jsonl", 1_000_000_001)
        newest = self.transcript("claude", "projects/a/new\nline.jsonl", 4_000_000_004)
        middle = self.transcript("codex", "sessions/2026/10/mid file.jsonl", 3_000_000_003)
        result = self.run_helper("claude", self.claude, "codex", self.codex)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.records(result), [
            ("claude", str(newest)), ("codex", str(middle)), ("claude", str(oldest)),
        ])

    def test_equal_timestamps_use_path_then_provider_as_stable_tiebreakers(self):
        modified = 2_000_000_002
        codex = self.transcript("codex", "sessions/a.jsonl", modified)
        claude = self.transcript("claude", "projects/p/z.jsonl", modified)
        arguments = ("claude", self.claude, "codex", self.codex)
        first = self.run_helper(*arguments)
        second = self.run_helper("codex", self.codex, "claude", self.claude)
        expected = sorted([("codex", str(codex)), ("claude", str(claude))],
                          key=lambda record: (os.fsencode(record[1]), record[0]))
        self.assertEqual(self.records(first), expected)
        self.assertEqual(self.records(second), expected)

    def test_claude_only_includes_project_session_files_at_exact_depth(self):
        included = self.transcript("claude", "projects/project/session.jsonl", 2)
        self.transcript("claude", "projects/project/session/subagent.jsonl", 4)
        self.transcript("claude", "projects/direct.jsonl", 5)
        self.transcript("claude", "projects/project/not-jsonl.txt", 6)
        result = self.run_helper("claude", self.claude)
        self.assertEqual(self.records(result), [("claude", str(included))])

    def test_codex_recurses_but_only_includes_jsonl_regular_files(self):
        included = self.transcript("codex", "sessions/year/month/session.jsonl", 2)
        self.transcript("codex", "sessions/year/month/notes.txt", 3)
        outside = self.transcript("codex", "elsewhere/outside.jsonl", 4)
        fifo = self.codex / "sessions/year/month/wait.jsonl"
        os.mkfifo(fifo)
        symlink = self.codex / "sessions/year/month/link.jsonl"
        symlink.symlink_to(included)
        result = self.run_helper("codex", self.codex)
        self.assertEqual(self.records(result), [("codex", str(included))])
        self.assertNotIn(os.fsencode(outside), result.stdout)
        self.assertNotIn(os.fsencode(fifo), result.stdout)
        self.assertNotIn(os.fsencode(symlink), result.stdout)

    def test_missing_and_empty_stores_are_ignored(self):
        (self.claude / "projects").mkdir(parents=True)
        result = self.run_helper(
            "claude", self.claude, "codex", self.root / "missing codex")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    def test_bad_arguments_report_usage(self):
        for arguments in [(), ("claude",), ("other", self.root)]:
            with self.subTest(arguments=arguments):
                result = self.run_helper(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
