"""Mac-only durable records; shared wire vocabulary stays in shared.metered."""
from dataclasses import dataclass, field
from typing import List, Optional

from shared.metered.records import AddSSID, Consumer, HostPolicy, HostState, Record, SharedConfig
from shared.metered.records import Effective, EffectiveSource, Host
from shared.metered.rules import unknown


@dataclass(frozen=True)
class Pending(Record):
    ops: List[AddSSID] = field(default_factory=list)

    def __post_init__(self):
        if len(self.ops) > 128 or len({op.op_id for op in self.ops}) != len(self.ops):
            raise ValueError('pending SSID queue is full or has duplicate operation ids')


@dataclass(frozen=True)
class SyncStatus(Record):
    attempted_at: Optional[float] = None
    succeeded_at: Optional[float] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class Network(Record):
    interface: Optional[str]
    ssid: Optional[str]
    bssid: Optional[str]
    gateway: Optional[str]
    gateway_mac: Optional[str]
    connected: bool
    observed_at: float


@dataclass(frozen=True)
class Ownership(Record):
    interface: Optional[str] = None
    identity: Optional[str] = None
    set_at: float = 0.0
    seen_at: float = 0.0


@dataclass(frozen=True)
class LowDataSettings(Record):
    enabled: bool
    revision: str
    error: Optional[str]


@dataclass(frozen=True)
class MacStatus(Record):
    config: SharedConfig
    policy: HostPolicy
    state: HostState
    registry: List[Consumer]
    pi_state: Optional[HostState]
    pending: List[AddSSID]
    sync: SyncStatus
    fresh: bool
    config_cached: bool
    low_data_mode: LowDataSettings


def empty_state(host=Host.MAC):
    return HostState(host, 0.0, unknown('no evaluation available'),
                     Effective(True, EffectiveSource.VERDICT, None), {})
