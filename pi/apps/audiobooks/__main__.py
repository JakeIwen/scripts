"""Package entrypoint for the audiobook server."""

import runpy

from pi.package_runtime import record_running_release


record_running_release("/run/audiobooks")
runpy.run_module("pi.apps.audiobooks.audiobook_server", run_name="__main__")
