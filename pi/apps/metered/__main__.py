"""Module launch for the packaged metering controller."""
import sys
from .cli import main

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:] or ['run']))
