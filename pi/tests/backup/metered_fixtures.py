"""Deterministic policy inputs for flat cloud-worker transport regressions."""
from shared.metered.admission import cloud_permits
from shared.metered.config import first_run_config
from shared.metered.records import HostPolicy


def decide(raw, now, consumer_id='pi.icloud_backup'):
    return cloud_permits(raw, first_run_config(), HostPolicy(), None, now, consumer_id)
