"""The sole subprocess boundary: absolute argv, no shell, bounded duration."""
from dataclasses import dataclass
import json
import os
import pwd
import subprocess

from log_redaction import redact
from panel_contracts import ApiError
from panel_paths import CHILD_PATH


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''


def child_environment() -> dict[str, str]:
    user = pwd.getpwuid(os.getuid())
    return {'HOME': user.pw_dir, 'USER': user.pw_name, 'LOGNAME': user.pw_name,
            'LANG': 'en_US.UTF-8', 'PATH': CHILD_PATH}


class CliRunner:
    def run(self, argv, timeout=30) -> CommandResult:
        if not argv or not os.path.isabs(argv[0]) or not all(isinstance(x, str) for x in argv):
            raise ValueError('Commands require an absolute executable and string arguments')
        try:
            result = subprocess.run(list(argv), stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, errors='replace', timeout=timeout,
                                    env=child_environment(), shell=False)
        except subprocess.TimeoutExpired:
            raise ApiError(504, 'Tool timed out; inspect its state before retrying') from None
        except OSError as exc:
            raise ApiError(502, redact(str(exc))) from None
        if len(result.stdout) + len(result.stderr) > 2 * 1024 * 1024:
            raise ApiError(502, 'Tool output exceeded the display limit')
        return CommandResult(result.returncode, result.stdout, result.stderr)


def checked(result: CommandResult, allowed=(0,)) -> CommandResult:
    if result.returncode not in allowed:
        raise ApiError(502, redact((result.stderr or result.stdout).strip())
                       or f'Tool exited {result.returncode}')
    return result


def json_result(result: CommandResult, allowed=(0,)):
    checked(result, allowed)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ApiError(502, 'Tool returned invalid JSON') from None
