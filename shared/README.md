# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH` and is normally flattened into vanpi's deployed
`/home/pi/scripts/python-automation/` directory by `pi/sync_scripts.sh`.

Compute-specific cross-host modules live under `pi/van_compute/scripts/`, not
here. They are deployed atomically by the compute installer to
`/home/pi/van_compute/scripts/`; the general Pi sync has no compute-specific
exceptions or ownership.
