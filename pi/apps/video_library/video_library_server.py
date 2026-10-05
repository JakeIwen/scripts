#!/usr/bin/env python3
"""Package entrypoint shim retained for runpy dispatch and unit prechecks."""


def main():
    import os

    from .config import DISPLAY, PORT, RUNTIME_DIR, SESSION_BUS
    from .routes import app
    from .service import active_service

    os.environ.setdefault("DISPLAY", DISPLAY)
    os.environ.setdefault("XDG_RUNTIME_DIR", RUNTIME_DIR)
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", SESSION_BUS)
    service = active_service()
    service.start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
