# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH` and is normally flattened into vanpi's deployed
`/home/pi/scripts/python-automation/` directory by `pi/sync_scripts.sh`.

`van_compute_protocol.py` is the exception: the compute installer deploys it
atomically to `/home/pi/scripts/compute/python-automation/` with the matching Pi
CLI and Mac worker. The general sync deliberately excludes it. The read-only
`van_compute_metrics.py` remains in the normal dashboard deployment and is also
carried by the compute installer so either documented dashboard path receives
the same file.

`python/van_compute_protocol.py` is the shared allowlist and command builder for the Pi analysis
queue and Mac worker. Keep task definitions offline-only and validate every exposed option; do not
add live CAN capture, diagnostic, bus-wake, interface, or service-control entry points.
