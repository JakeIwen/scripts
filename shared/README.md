# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH` and is flattened into vanpi's deployed
`/home/pi/scripts/python-automation/` directory by `pi/sync_scripts.sh`.

`python/van_compute_protocol.py` is the shared allowlist and command builder for the Pi analysis
queue and Mac worker. Keep task definitions offline-only and validate every exposed option; do not
add live CAN capture, diagnostic, bus-wake, interface, or service-control entry points.
