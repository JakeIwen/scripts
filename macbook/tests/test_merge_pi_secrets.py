import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "merge_pi_secrets.sh"


class MergePiSecretsTests(unittest.TestCase):
    def make_repo(self, dev: Path, name: str, content: str, mode: int = 0o644) -> Path:
        secret = dev / name / "pi" / "secrets" / ".bash_variables"
        secret.parent.mkdir(parents=True)
        secret.write_text(content, encoding="utf-8")
        secret.chmod(mode)
        return secret

    def run_script(self, dev: Path, user_input: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), "--dev-dir", str(dev)],
            input=user_input,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_unions_variables_and_ignores_dot_bak(self):
        with tempfile.TemporaryDirectory() as directory:
            dev = Path(directory)
            first = self.make_repo(dev, "scripts", "export FIRST=one\n", 0o644)
            second = self.make_repo(dev, "scripts_2", "export SECOND=two\n", 0o600)
            backup = self.make_repo(dev, "scripts.bak", "export BACKUP=no\n")
            result = self.run_script(dev)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = "export FIRST=one\nexport SECOND=two\n"
            self.assertEqual(first.read_text(), expected)
            self.assertEqual(second.read_text(), expected)
            self.assertEqual(backup.read_text(), "export BACKUP=no\n")
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o600)

    def test_prompts_with_plaintext_values_and_uses_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            dev = Path(directory)
            first = self.make_repo(dev, "scripts", "export TOKEN=first-secret\n")
            second = self.make_repo(dev, "scripts_2", "export TOKEN=second-secret\n")
            result = self.run_script(dev, "2\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("first-secret", result.stdout)
            self.assertIn("second-secret", result.stdout)
            self.assertEqual(first.read_text(), "export TOKEN=second-secret\n")
            self.assertEqual(second.read_text(), "export TOKEN=second-secret\n")

    def test_creates_missing_file_in_non_backup_clone(self):
        with tempfile.TemporaryDirectory() as directory:
            dev = Path(directory)
            source = self.make_repo(dev, "scripts", "export TOKEN=value\n")
            missing_repo = dev / "scripts_2"
            missing_repo.mkdir()
            result = self.run_script(dev)
            self.assertEqual(result.returncode, 0, result.stderr)
            created = missing_repo / "pi" / "secrets" / ".bash_variables"
            self.assertEqual(created.read_text(), source.read_text())
            self.assertEqual(stat.S_IMODE(created.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
