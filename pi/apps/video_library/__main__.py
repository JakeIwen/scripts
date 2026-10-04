"""Package entrypoint for the video library service."""

import runpy

from pi.package_runtime import record_running_release


record_running_release("/run/video-library")
runpy.run_module("pi.apps.video_library.video_library_server", run_name="__main__")
