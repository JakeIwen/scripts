"""Rollback-safe management of the price checker's user crontab entry."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


PRICE_CHECK_COMMAND = (
    "/usr/bin/python3 /home/pi/scripts/price_check/main.py "
    "2>&1 | /usr/bin/logger -t price-check"
)


class CronScheduleError(RuntimeError):
    pass


class CronScheduleRateLimitError(CronScheduleError):
    pass


class CronScheduleManager:
    def __init__(
        self,
        *,
        crontab_bin="/usr/bin/crontab",
        parser=Path("/home/pi/scripts/parse_cron.sh"),
        command=PRICE_CHECK_COMMAND,
        runner=subprocess.run,
        temporary_directory=None,
    ):
        self.crontab_bin = str(crontab_bin)
        self.parser = Path(parser)
        self.command = command
        self.runner = runner
        self.temporary_directory = temporary_directory

    def _run(self, argv, *, timeout=20):
        try:
            return self.runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CronScheduleError(f"could not run {argv[0]}: {error}") from error

    @staticmethod
    def _detail(result) -> str:
        return (result.stderr or result.stdout or "unknown error").strip()[-500:]

    @staticmethod
    def normalize(expression: str) -> str:
        if any(character in expression for character in ("\x00", "\r", "\n")):
            raise CronScheduleError("cron schedule must be a single line")
        fields = expression.split()
        if len(fields) != 5:
            raise CronScheduleError("cron schedule must contain exactly five fields")
        normalized = " ".join(fields)
        if len(normalized) > 160:
            raise CronScheduleError("cron schedule is too long")
        return normalized

    def describe(self, expression: str) -> str:
        expression = self.normalize(expression)
        result = self._run([str(self.parser), expression])
        description = result.stdout.strip()
        if result.returncode == 75:
            raise CronScheduleRateLimitError("cron parser rate limited")
        if result.returncode or not description:
            detail = self._detail(result)
            raise CronScheduleError(
                f"cronp could not resolve that schedule ({detail}); crontab was not changed"
            )
        return description

    def _read_crontab(self) -> str:
        result = self._run([self.crontab_bin, "-l"], timeout=10)
        if result.returncode:
            raise CronScheduleError(
                f"could not read the existing crontab: {self._detail(result)}"
            )
        return result.stdout

    def _find_entry(self, crontab: str) -> tuple[int, str]:
        matches = []
        for index, line in enumerate(crontab.splitlines(keepends=True)):
            if line.lstrip().startswith("#"):
                continue
            fields = line.strip().split(None, 5)
            if len(fields) == 6 and fields[5] == self.command:
                matches.append((index, " ".join(fields[:5])))
        if not matches:
            raise CronScheduleError("price checker entry was not found in the user crontab")
        if len(matches) != 1:
            raise CronScheduleError(
                "multiple price checker entries were found; crontab was not changed"
            )
        return matches[0]

    def status(self) -> dict:
        crontab = self._read_crontab()
        _, expression = self._find_entry(crontab)
        try:
            description = self.describe(expression)
            error = None
            error_code = None
        except CronScheduleRateLimitError as parse_error:
            description = ""
            error = str(parse_error)
            error_code = "rate_limit"
        except CronScheduleError as parse_error:
            description = ""
            error = str(parse_error)
            error_code = "parse"
        return {
            "expression": expression,
            "description": description,
            "error": error,
            "error_code": error_code,
        }

    def preview(self, expression: str) -> dict:
        try:
            normalized = self.normalize(expression)
            description = self.describe(normalized)
            error = None
            error_code = None
        except CronScheduleRateLimitError as parse_error:
            normalized = " ".join(expression.split())
            description = ""
            error = str(parse_error)
            error_code = "rate_limit"
        except CronScheduleError as parse_error:
            normalized = " ".join(expression.split())
            description = ""
            error = str(parse_error)
            error_code = "parse"
        return {
            "expression": normalized,
            "description": description,
            "error": error,
            "error_code": error_code,
        }

    def _rollback(self, backup: Path, cause: str) -> None:
        restored = self._run([self.crontab_bin, str(backup)], timeout=10)
        if restored.returncode:
            raise CronScheduleError(
                f"{cause}; automatic restore also failed: {self._detail(restored)}"
            )
        expected = backup.read_text(encoding="utf-8")
        try:
            current = self._read_crontab()
        except CronScheduleError as error:
            raise CronScheduleError(
                f"{cause}; automatic restore could not be verified: {error}"
            ) from error
        if current != expected:
            raise CronScheduleError(
                f"{cause}; automatic restore verification failed"
            )
        raise CronScheduleError(f"{cause}; previous crontab restored")

    def update(self, expression: str) -> dict:
        expression = self.normalize(expression)
        previous = self._read_crontab()
        entry_index, _ = self._find_entry(previous)

        # cronp's parser is both the user-facing description and a required precondition.
        # Do not even dry-run a replacement until it resolves a non-empty value.
        description = self.describe(expression)

        lines = previous.splitlines(keepends=True)
        old_line = lines[entry_index]
        ending = old_line[len(old_line.rstrip("\r\n")) :]
        lines[entry_index] = f"{expression} {self.command}{ending}"
        candidate_text = "".join(lines)

        with tempfile.TemporaryDirectory(
            prefix="price-cron-", dir=self.temporary_directory
        ) as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            backup = directory / "previous.crontab"
            candidate = directory / "candidate.crontab"
            backup.write_text(previous, encoding="utf-8")
            candidate.write_text(candidate_text, encoding="utf-8")
            os.chmod(backup, 0o600)
            os.chmod(candidate, 0o600)

            validation = self._run(
                [self.crontab_bin, "-n", str(candidate)], timeout=10
            )
            if validation.returncode:
                raise CronScheduleError(
                    "cron schedule was rejected; crontab was not changed: "
                    f"{self._detail(validation)}"
                )

            installed = self._run([self.crontab_bin, str(candidate)], timeout=10)
            if installed.returncode:
                self._rollback(
                    backup, f"crontab update failed: {self._detail(installed)}"
                )

            try:
                current = self._read_crontab()
            except CronScheduleError as error:
                self._rollback(backup, f"could not verify crontab update: {error}")
            if current != candidate_text:
                self._rollback(backup, "crontab verification failed")

        return {
            "expression": expression,
            "description": description,
            "error": None,
            "error_code": None,
        }
