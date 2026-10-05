#!/usr/bin/env python3
"""CLI and recorder redaction shim retained for script and package consumers."""

if __package__:
    from .system_monitor.journal import redact_log_message
    from .system_monitor.cli import main
else:
    from system_monitor.journal import redact_log_message
    from system_monitor.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
