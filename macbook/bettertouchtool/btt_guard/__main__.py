"""Run from the checkout root: python -m macbook.bettertouchtool.btt_guard."""
import sqlite3
import subprocess
import sys

from .cli import main
from .service import ServiceError

try:
    raise SystemExit(main())
except (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error, subprocess.SubprocessError, ServiceError) as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1)
