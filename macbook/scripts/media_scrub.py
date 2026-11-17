#!/usr/bin/env python3
"""BTT entry: Pi media position status and track seeks (run as `/usr/bin/python3 -B <path>`)."""
import sys

from media_scrub.cli import main


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[0]))
