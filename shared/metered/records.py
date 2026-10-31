"""Versioned wire records; reject malformed input before policy evaluation."""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from enum import Enum
import ipaddress
import math
import re
from typing import Dict, List, Optional, Union, get_args, get_origin, get_type_hints


MAX_OVERRIDE_SECONDS = 86400.0


class Host(str, Enum):
    PI = 'pi'
    MAC = 'mac'


class ModeValue(str, Enum):
    AUTO = 'auto'
    METERED = 'metered'
    UNMETERED = 'unmetered'


class ApprovalKind(str, Enum):
    UNTIL = 'until'
    EPISODE = 'episode'


class MemberKind(str, Enum):
    UBNT = 'ubnt'
    STATION = 'station'
    WIRED = 'wired'


class EffectiveSource(str, Enum):
    VERDICT = 'verdict'
    MODE = 'mode'


class Actual(str, Enum):
    RUNNING = 'running'
    HELD = 'held'
    MANUAL = 'stopped_manually'
    UNKNOWN = 'unknown'
    NA = 'n/a'


class Handling(str, Enum):
    TICK = 'tick'
    GUARD = 'guard'
    RUN = 'run'
    EXTERNAL = 'external'
    ADVISORY = 'advisory'
    UNPROTECTED = 'unprotected'


class PolicyOperation(str, Enum):
    MODE = 'mode'
    APPROVE = 'approve'
    REVOKE = 'revoke'
    EXEMPT = 'exempt'


class SSIDOperation(str, Enum):
    ADD = 'add'
    REMOVE = 'remove'


class SharedOperation(str, Enum):
    ADD_SSID = 'add_ssid'


def ssid(value):
    if not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 32:
        raise ValueError('SSID must contain 1 to 32 UTF-8 bytes')
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('SSID contains a control character')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
        raise ValueError('invalid identifier')
    return value


def decode(kind, value):
    """Strict recursive conversion for this module's dataclass wire schema."""
    origin, args = get_origin(kind), get_args(kind)
    if origin is Union:
        for option in args:
            try:
                return decode(option, value)
            except (ValueError, TypeError):
                pass
        raise ValueError('value does not match the wire type')
    if origin is list:
        if not isinstance(value, list):
            raise ValueError('expected an array')
        return [decode(args[0], item) for item in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError('expected an object')
        return {decode(args[0], key): decode(args[1], item) for key, item in value.items()}
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if is_dataclass(kind):
        names = {f.name for f in fields(kind)}
        required = {f.name for f in fields(kind) if f.default is MISSING and f.default_factory is MISSING}
        if not isinstance(value, dict) or set(value) - names or not required <= set(value):
            raise ValueError('unexpected fields for ' + kind.__name__)
        hints = get_type_hints(kind)
        return kind(**{key: decode(hints[key], item) for key, item in value.items()})
    if kind is float:
        if type(value) not in (int, float):
            raise ValueError('expected a finite number')
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError('expected a finite number') from error
        if not math.isfinite(number):
            raise ValueError('expected a finite number')
        return number
    if type(value) is not kind:
        raise ValueError('incorrect scalar type')
    return value


class Record:
    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return decode(cls, value)


@dataclass(frozen=True)
class HomeNetwork(Record):
    ssid: str
    gateway: str
    gateway_mac: str

    def __post_init__(self):
        ssid(self.ssid)
        ipaddress.IPv4Address(self.gateway)
        if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', self.gateway_mac):
            raise ValueError('gateway_mac must be a lowercase hardware address')


@dataclass(frozen=True)
class Receipt(Record):
    op_id: str
    digest: str

    def __post_init__(self):
        identifier(self.op_id)
        if not re.fullmatch(r'[0-9a-f]{64}', self.digest):
            raise ValueError('invalid receipt digest')


@dataclass(frozen=True)
class SharedConfig(Record):
    schema: int
    revision: int
    metered_ssids: List[str]
    home_network: HomeNetwork
    receipts: List[Receipt] = field(default_factory=list)

    def __post_init__(self):
        if self.schema != 1 or self.revision < 0:
            raise ValueError('unsupported config schema or revision')
        for value in self.metered_ssids:
            ssid(value)
        if len(set(self.metered_ssids)) != len(self.metered_ssids):
            raise ValueError('duplicate SSID')
        if len({row.op_id for row in self.receipts}) != len(self.receipts):
            raise ValueError('duplicate receipt')


@dataclass(frozen=True)
class Mode(Record):
    value: ModeValue = ModeValue.AUTO
    until: Optional[float] = None
    set_by: str = 'default'
    set_at: float = 0.0

    def __post_init__(self):
        identifier(self.set_by)
        if self.value == ModeValue.UNMETERED and self.until is None:
            raise ValueError('unmetered mode requires an expiry')
        if self.until is not None and not 0 < self.until - self.set_at <= MAX_OVERRIDE_SECONDS:
            raise ValueError('mode expiry must be positive and at most 24 hours')


@dataclass(frozen=True)
class Approval(Record):
    kind: ApprovalKind
    until: Optional[float]
    episode: Optional[str]
    set_by: str
    set_at: float

    def __post_init__(self):
        identifier(self.set_by)
        if self.kind == ApprovalKind.UNTIL:
            if (self.until is None or not 0 < self.until - self.set_at <= MAX_OVERRIDE_SECONDS
                    or self.episode is not None):
                raise ValueError('timed approval requires only a future expiry')
        elif self.episode is None or self.until is not None:
            raise ValueError('episode approval requires only an episode identifier')
        if self.episode is not None:
            identifier(self.episode)


@dataclass(frozen=True)
class HostPolicy(Record):
    schema: int = 1
    revision: int = 0
    mode: Mode = field(default_factory=Mode)
    approvals: Dict[str, Approval] = field(default_factory=dict)
    exempt: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.schema != 1 or self.revision < 0 or len(set(self.exempt)) != len(self.exempt):
            raise ValueError('invalid host policy')
        for value in [*self.approvals, *self.exempt]:
            identifier(value)


@dataclass(frozen=True)
class UplinkMember(Record):
    name: str
    percent: float
    online: bool
    kind: MemberKind
    ssid_verified: Optional[str]
    ssid_configured: Optional[str]

    def __post_init__(self):
        identifier(self.name)
        if not 0 <= self.percent <= 100:
            raise ValueError('invalid member weight')
        for value in (self.ssid_verified, self.ssid_configured):
            if value is not None:
                ssid(value)


@dataclass(frozen=True)
class UplinkEvidence(Record):
    collected_at: float
    policy: Optional[str]
    members: List[UplinkMember]
    unsupported: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MacNetwork(Record):
    ssid: Optional[str]
    gateway: Optional[str]
    gateway_mac: Optional[str]
    connected: bool


@dataclass(frozen=True)
class MatchedSSID(Record):
    member: str
    ssid: str


@dataclass(frozen=True)
class Verdict(Record):
    metered: Optional[bool]
    reason: str
    matched: List[MatchedSSID]
    evidence_at: Optional[float]


@dataclass(frozen=True)
class Effective(Record):
    metered: bool
    source: EffectiveSource
    episode: Optional[str]


@dataclass(frozen=True)
class ConsumerState(Record):
    permitted: bool
    reason: str
    approval: Optional[Approval]
    actual: Actual = Actual.UNKNOWN


@dataclass(frozen=True)
class HostState(Record):
    host: Host
    evaluated_at: float
    verdict: Verdict
    effective: Effective
    consumers: Dict[str, ConsumerState]
    config_revision: int = 0
    policy_revision: int = 0
    route_signature: Optional[str] = None
    valid_until: Optional[float] = None


@dataclass(frozen=True)
class Consumer(Record):
    id: str
    host: Host
    label: str
    how: Handling
    note: str

    def __post_init__(self):
        identifier(self.id)
        if not self.id.startswith(self.host.value + '.'):
            raise ValueError('consumer id must be host-qualified')


@dataclass(frozen=True)
class MeteredStatus(Record):
    config: SharedConfig
    policy: HostPolicy
    state: HostState
    registry: List[Consumer]
    mac_report: Optional[HostState]


@dataclass(frozen=True)
class AddSSID(Record):
    op_id: str
    op: SharedOperation
    ssid: str

    def __post_init__(self):
        identifier(self.op_id)
        ssid(self.ssid)


@dataclass(frozen=True)
class SyncRequest(Record):
    ops: List[AddSSID]
    mac_report: Optional[HostState]

    def __post_init__(self):
        if len(self.ops) > 128:
            raise ValueError('at most 128 operations per sync')
        if self.mac_report is not None and self.mac_report.host != Host.MAC:
            raise ValueError('sync accepts only a Mac report')


@dataclass(frozen=True)
class SSIDChange(Record):
    op: SSIDOperation
    ssid: str
    revision: int

    def __post_init__(self):
        ssid(self.ssid)


@dataclass(frozen=True)
class PolicyChange(Record):
    op: PolicyOperation
    revision: int
    consumer: Optional[str] = None
    value: Optional[str] = None
    seconds: Optional[float] = None
    enabled: Optional[bool] = None

    def __post_init__(self):
        if self.seconds is not None and not 0 < self.seconds <= MAX_OVERRIDE_SECONDS:
            raise ValueError('duration must be positive and at most 24 hours')
        expected = {
            PolicyOperation.MODE: ('value', 'seconds'),
            PolicyOperation.APPROVE: ('consumer', 'value', 'seconds'),
            PolicyOperation.REVOKE: ('consumer',),
            PolicyOperation.EXEMPT: ('consumer', 'enabled'),
        }[self.op]
        for name in ('consumer', 'value', 'seconds', 'enabled'):
            if name not in expected and getattr(self, name) is not None:
                raise ValueError('unexpected policy operation field')
        if self.op in (PolicyOperation.MODE, PolicyOperation.APPROVE) and self.value is None:
            raise ValueError('policy operation requires a value')
        if self.op != PolicyOperation.MODE and self.consumer is None:
            raise ValueError('policy operation requires a consumer')


@dataclass(frozen=True)
class SyncResponse(Record):
    config: SharedConfig
    pi_state: HostState
    applied: List[str]
