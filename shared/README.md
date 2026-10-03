# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH`. Pi package releases preserve its package path;
flat-safe legacy consumers continue receiving an explicit utility subset in
`/home/pi/scripts/python-automation/`. See
[Pi deployment and rollback](../pi/docs/deployment.md).

Compute-specific cross-host modules live under `pi/van_compute/scripts/`, not
here. They are deployed atomically by the compute installer to
`/home/pi/van_compute/scripts/`; the general Pi sync has no compute-specific
exceptions or ownership.
