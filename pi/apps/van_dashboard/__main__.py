import json
import os
from pathlib import Path
import sys
import tempfile

import pi
from .van_dashboard import main
from .van_dashboard_common import RUNTIME_DIR


def record_package_release():
    """Publish the path already pinned by pi, never a fresh lookup of current."""
    temporary = None
    try:
        record = {'package_path': pi.__path__[0], 'pid': os.getpid(),
                  'invocation_id': os.environ.get('INVOCATION_ID')}
        descriptor, temporary = tempfile.mkstemp(prefix='.package-release-', dir=RUNTIME_DIR)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(record, handle)
            handle.write('\n')
        os.replace(temporary, Path(RUNTIME_DIR) / 'package-release')
    except Exception as error:
        # GC refuses an absent/stale record; monitoring must still start.
        print(f'WARNING: cannot record dashboard package release: {error}', file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


record_package_release()
main()
