#!/usr/bin/env python3
"""Phone-friendly control surface for vanpi."""

import signal

from flask import Flask

from .http import reject_cross_origin_mutations
from .van_dashboard_common import PORT


def create_app():
    app = Flask(__name__, static_folder=None)

    # Initialize the one process-wide runtime only when an application is made.
    from . import runtime  # noqa: F401
    from .routes.backups import bp as backups_bp
    from .routes.common import bp as common_bp
    from .routes.cop import bp as cop_bp
    from .routes.disks import bp as disks_bp
    from .routes.history import bp as history_bp
    from .routes.home import bp as home_bp
    from .routes.integrations import bp as integrations_bp
    from .routes.network import bp as network_bp
    from .routes.sonos import bp as sonos_bp
    from .routes.storage import bp as storage_bp
    from .routes.system import bp as system_bp
    from .routes.telemetry import bp as telemetry_bp
    from .routes.usb import bp as usb_bp
    from .routes.vonstar import bp as vonstar_bp

    app.before_request(reject_cross_origin_mutations)
    for blueprint in (
        common_bp,
        cop_bp,
        vonstar_bp,
        telemetry_bp,
        home_bp,
        storage_bp,
        disks_bp,
        system_bp,
        network_bp,
        usb_bp,
        backups_bp,
        history_bp,
        integrations_bp,
        sonos_bp,
    ):
        app.register_blueprint(blueprint)
    return app


def main():
    app = create_app()
    from . import runtime

    def terminate(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    try:
        runtime.cop_alert.start()
        runtime.connectivity.start()
        runtime.starlink.start()
        app.run(host="0.0.0.0", port=PORT, threaded=True)
    finally:
        runtime.cop_alert.stop()


if __name__ == "__main__":
    main()
