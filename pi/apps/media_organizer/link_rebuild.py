"""Schedule the existing detached full link rebuild, outside the media lock."""
import subprocess
from . import config, records


def schedule():
    try:
        result = subprocess.run([str(config.ALIAS_MEDIA_SCRIPT)], capture_output=True, timeout=30)
        return records.ALIAS_SCHEDULED if result.returncode == 0 else records.ALIAS_FAILED
    except (OSError, subprocess.SubprocessError):
        return records.ALIAS_FAILED


def alias(arguments):
    try:
        from .alias_main import main
    except ImportError:
        raise RuntimeError('Python alias builder is not installed yet') from None
    if main(arguments) != 0:
        raise RuntimeError('alias builder could not be scheduled')
    return {'status': records.ALIAS_SCHEDULED}
