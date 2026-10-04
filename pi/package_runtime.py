"""Runtime ownership records for pinned Pi package releases."""

import json
import os
from pathlib import Path
import sys
import tempfile

import pi


def record_running_release(runtime_dir, *, warning_prefix=None):
    """Atomically publish the release pinned by this process's Pi package."""
    temporary = None
    try:
        record = {
            "package_path": pi.__path__[0],
            "pid": os.getpid(),
            "invocation_id": os.environ.get("INVOCATION_ID"),
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".package-release-", dir=runtime_dir
        )
        with os.fdopen(descriptor, "w") as handle:
            json.dump(record, handle)
            handle.write("\n")
        os.replace(temporary, Path(runtime_dir) / "package-release")
    except Exception as error:
        # GC refuses an absent/stale record; monitoring must still start.
        prefix = f"{warning_prefix} " if warning_prefix else ""
        print(
            f"WARNING: cannot record {prefix}package release: {error}",
            file=sys.stderr,
        )
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
