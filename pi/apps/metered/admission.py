"""Cloud admission shares metering rules without weakening transport evidence."""
import time

from shared.metered.admission import cloud_permits
from shared.metered.config import ConfigStore
from shared.metered.policy import PolicyStore
from shared.metered.records import Handling, Host, HostState
from shared.metered.registry import consumer
from shared.metered.store import RecordStore
from .config import Paths
from .controller import empty_state


def decide(raw, now=None, consumer_id='pi.icloud_backup', config=None, policy=None, previous=None):
    now = time.time() if now is None else now
    try:
        if consumer(consumer_id, Host.PI).how != Handling.GUARD:
            raise ValueError('consumer is not a cloud worker')
        paths = Paths.default()
        config = config or ConfigStore(paths.shared).load()
        policy = policy or PolicyStore(Host.PI, paths.policy).load()
        if previous is None:
            previous = RecordStore(paths.state, HostState, empty_state).load()
        return cloud_permits(raw, config, policy, previous, now, consumer_id)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False, 'incomplete uplink evidence or invalid metering policy'
