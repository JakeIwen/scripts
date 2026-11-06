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


class UnderDirectoryTests(SessionSearchPathsTests):
    """--under leaves out transcripts whose folder name or first line rules them out."""

    def claude_project(self, cwd, name, modified_ns):
        folder = "projects/" + "".join(c if c.isalnum() else "-" for c in cwd)
        return self.transcript("claude", folder + "/" + name + ".jsonl", modified_ns)

    def codex_session(self, name, first_line, modified_ns):
        path = self.transcript("codex", "sessions/2026/11/05/" + name + ".jsonl", modified_ns)
        path.write_text(first_line + "\n")
        os.utime(path, ns=(modified_ns, modified_ns))
        return path

    def test_claude_folders_are_kept_only_when_their_name_can_lie_within(self):
        base = str(self.root.resolve() / "work")
        kept = [self.claude_project(base, "same", 9),
                self.claude_project(base + "/sub.dir", "below", 8)]
        self.claude_project(base + "2", "sibling", 7)
        self.claude_project(str(self.root.resolve()), "parent", 6)
        self.transcript("claude", "projects/unknown/no-cwd.jsonl", 5)
        result = self.run_helper("--under", base, "claude", self.claude)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.records(result), [("claude", str(path)) for path in kept])

    def test_codex_first_lines_rule_sessions_out_only_when_they_name_a_cwd(self):
        base = str(self.root.resolve() / "work")
        meta = '{"type": "session_meta", "payload": {"cwd": "%s"}}'
        kept = [self.codex_session("same", meta % base, 9),
                self.codex_session("below", meta % (base + "/sub"), 8),
                self.codex_session("unknown", '{"type": "event_msg"}', 7),
                self.codex_session("unparsable", "not json", 6)]
        self.codex_session("sibling", meta % (base + "2"), 5)
        self.codex_session("elsewhere", meta % "/tmp", 4)
        result = self.run_helper("--under", base, "codex", self.codex)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.records(result), [("codex", str(path)) for path in kept])

    def test_the_root_directory_keeps_everything(self):
        paths = [self.claude_project("/Users/x", "a", 9),
                 self.codex_session("b", '{"type": "session_meta", "payload": {"cwd": "/x"}}', 8)]
        result = self.run_helper("--under", "/", "claude", self.claude, "codex", self.codex)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([path for _, path in self.records(result)], list(map(str, paths)))


if __name__ == "__main__":
    unittest.main()
