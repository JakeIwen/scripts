# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH`. Pi package releases preserve its package path.
Pi shell utilities and the packaged video service include the current release's
`shared/python` for standalone `sonos_tasks` imports; changes to that helper are
part of the video's restart digest. `sns.sh` preserves inherited paths for the
frozen flat video's saved-unit rollback. No current deployer writes the retired
`/home/pi/scripts/python-automation/` tree. The Mac wrapper remains unchanged. See
[Pi deployment and rollback](../pi/docs/deployment.md).

Compute-specific cross-host modules live under `pi/van_compute/scripts/`, not
here. They are deployed atomically by the compute installer to
`/home/pi/van_compute/scripts/`; the general Pi sync has no compute-specific
exceptions or ownership.
