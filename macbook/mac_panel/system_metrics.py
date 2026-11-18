"""Machine-wide numbers from fixed commands, one probe at a time.

Each probe succeeds or is reported unavailable on its own, so one missing sysctl key or a
slow `pmset` never erases the other numbers. The memory split is an estimate built from
`vm_stat` counters; a term that comes out negative makes the split unavailable rather than
drawing an impossible gauge.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import re
import shutil
import time

from panel_contracts import ApiError

SYSCTL = '/usr/sbin/sysctl'
VM_STAT = '/usr/bin/vm_stat'
PMSET = '/usr/bin/pmset'
DISK_PATH = '/System/Volumes/Data'
PROBE_TIMEOUT = 5
# All probes together; whatever has not run when it is spent is reported unavailable.
SYSTEM_BUDGET = 8
LOG = logging.getLogger(__name__)


class Probe(str, Enum):
    MEMORY_TOTAL = 'memory_total'
    NCPU = 'ncpu'
    LOAD = 'load'
    SWAP = 'swap'
    PRESSURE = 'pressure'
    UPTIME = 'uptime'
    MEMORY = 'memory'
    THERMAL = 'thermal'
    POWER = 'power'
    DISK = 'disk'


class Pressure(str, Enum):
    NORMAL = 'normal'
    WARN = 'warn'
    CRITICAL = 'critical'


class Thermal(str, Enum):
    NOMINAL = 'nominal'
    WARNING = 'warning'


SYSCTL_KEYS = {Probe.MEMORY_TOTAL: 'hw.memsize', Probe.NCPU: 'hw.ncpu', Probe.LOAD: 'vm.loadavg',
               Probe.SWAP: 'vm.swapusage', Probe.PRESSURE: 'kern.memorystatus_vm_pressure_level',
               Probe.UPTIME: 'kern.boottime'}
# kern.memorystatus_vm_pressure_level values (sys/kern_memorystatus.h).
PRESSURE_LEVELS = {1: Pressure.NORMAL, 2: Pressure.WARN, 4: Pressure.CRITICAL}
POWER_SOURCES = {'AC Power': 'ac', 'Battery Power': 'battery', 'UPS Power': 'ups'}
SIZE_UNITS = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}
# A decimal number: digits with at most one fractional part, so `1..2` never matches.
NUMBER = r'(\d{1,15}(?:\.\d{1,15})?)'
LOAD = re.compile(rf'\{{\s*{NUMBER}\s+{NUMBER}\s+{NUMBER}\s*\}}')
SWAP = re.compile(rf'total = {NUMBER}([KMG])\s+used = {NUMBER}([KMG])')
BOOT = re.compile(r'\bsec = (\d+)')
PAGE_SIZE = re.compile(r'page size of (\d+) bytes')
VM_LINE = re.compile(r'^"?([A-Za-z][A-Za-z -]*?)"?:\s+(\d+)\.?$')
THERMAL_LEVEL = re.compile(r'thermal warning level[^\d\n]*(\d+)', re.I)
NO_THERMAL = 'No thermal warning level has been recorded'
POWER_SOURCE = re.compile(r"Now drawing from '([^']+)'")
BATTERY = re.compile(r'\t(\d{1,3})%;\s*([^;]+);')
VM_KEYS = {'free': 'Pages free', 'speculative': 'Pages speculative', 'wired': 'Pages wired down',
           'purgeable': 'Pages purgeable', 'file': 'File-backed pages', 'anonymous': 'Anonymous pages',
           'compressor': 'Pages occupied by compressor'}


class Unavailable(Exception):
    pass


@dataclass
class SystemFacts:
    values: dict
    unavailable: list[str]

    def get(self, probe):
        return self.values.get(probe)


def require(match):
    if match is None:
        raise Unavailable
    return match


def parse_int(text):
    try:
        return int(text.strip())
    except ValueError:
        raise Unavailable from None


def parse_float(text):
    try:
        return float(text)
    except ValueError:
        raise Unavailable from None


def parse_load(text):
    return [parse_float(value) for value in require(LOAD.search(text)).groups()]


def parse_swap(text):
    total, total_unit, used, used_unit = require(SWAP.search(text)).groups()
    return {'total': round(parse_float(total) * SIZE_UNITS[total_unit]),
            'used': round(parse_float(used) * SIZE_UNITS[used_unit])}


def parse_pressure(text):
    level = PRESSURE_LEVELS.get(parse_int(text))
    if level is None:
        raise Unavailable
    return level


def parse_boot(text):
    return int(require(BOOT.search(text)).group(1))


def parse_vm_stat(text):
    page = int(require(PAGE_SIZE.search(text)).group(1))
    counters = {}
    for line in text.splitlines()[1:]:
        match = VM_LINE.match(line.strip())
        if match:
            counters[match.group(1)] = int(match.group(2))
    try:
        pages = {key: counters[name] for key, name in VM_KEYS.items()}
    except KeyError:
        raise Unavailable from None
    return {key: value * page for key, value in pages.items()}


def memory_split(vm, total):
    """Activity Monitor-style estimate from vm_stat byte counts; None when a term is negative."""
    app = vm['anonymous'] - vm['purgeable']
    wired, compressed = vm['wired'], vm['compressor']
    used = app + wired + compressed
    cached = vm['file'] + vm['purgeable']
    free = vm['free'] + vm['speculative']
    other = total - used - cached - free
    split = {'total': total, 'used': used, 'app': app, 'wired': wired, 'compressed': compressed,
             'cached': cached, 'free': free, 'other': other}
    return None if any(value < 0 for value in split.values()) else split


def parse_thermal(text):
    if NO_THERMAL in text:
        return Thermal.NOMINAL
    level = require(THERMAL_LEVEL.search(text)).group(1)
    return Thermal.WARNING if int(level) > 0 else Thermal.NOMINAL


def parse_power(text):
    source = require(POWER_SOURCE.search(text)).group(1)
    battery = BATTERY.search(text)
    state = battery.group(2).strip() if battery else None
    return {'source': POWER_SOURCES.get(source, source.lower()),
            'percent': int(battery.group(1)) if battery else None,
            'charging': None if state is None else state == 'charging'}


SYSCTL_PARSERS = {Probe.MEMORY_TOTAL: parse_int, Probe.NCPU: parse_int, Probe.LOAD: parse_load,
                  Probe.SWAP: parse_swap, Probe.PRESSURE: parse_pressure, Probe.UPTIME: parse_boot}


class SystemMetrics:
    def __init__(self, runner, clock=time.monotonic, wall_clock=time.time,
                 disk_usage=shutil.disk_usage):
        self.runner, self.clock, self.wall_clock = runner, clock, wall_clock
        self.disk_usage = disk_usage

    def command(self, argv, deadline):
        remaining = min(PROBE_TIMEOUT, deadline - self.clock())
        if remaining <= 0:
            raise Unavailable
        try:
            result = self.runner.run(argv, timeout=remaining)
        except ApiError:
            raise Unavailable from None
        if result.returncode != 0 or not result.stdout.strip():
            raise Unavailable
        return result.stdout

    def probes(self, deadline):
        """(probe, thunk) pairs; each thunk returns the parsed value or raises Unavailable."""
        for probe, key in SYSCTL_KEYS.items():
            yield probe, lambda key=key, probe=probe: SYSCTL_PARSERS[probe](
                self.command((SYSCTL, '-n', key), deadline))
        yield Probe.MEMORY, lambda: parse_vm_stat(self.command((VM_STAT,), deadline))
        yield Probe.THERMAL, lambda: parse_thermal(self.command((PMSET, '-g', 'therm'), deadline))
        yield Probe.POWER, lambda: parse_power(self.command((PMSET, '-g', 'batt'), deadline))
        yield Probe.DISK, self.disk

    def disk(self):
        try:
            usage = self.disk_usage(DISK_PATH)
        except OSError:
            raise Unavailable from None
        return {'total': usage.total, 'free': usage.free}

    def collect(self) -> SystemFacts:
        deadline = self.clock() + SYSTEM_BUDGET
        values, unavailable = {}, []
        for probe, thunk in self.probes(deadline):
            try:
                values[probe] = thunk()
            except Unavailable:
                unavailable.append(probe.value)
            except (ValueError, ArithmeticError):
                # Each parser validates its own text; this keeps a parser gap to one probe.
                LOG.exception('Probe %s returned text it could not parse', probe.value)
                unavailable.append(probe.value)
        if Probe.UPTIME in values:
            values[Probe.UPTIME] = max(0, int(self.wall_clock()) - values[Probe.UPTIME])
        if Probe.MEMORY in values:
            split = (memory_split(values[Probe.MEMORY], values[Probe.MEMORY_TOTAL])
                     if Probe.MEMORY_TOTAL in values else None)
            if split is None:
                del values[Probe.MEMORY]
                unavailable.append(Probe.MEMORY.value)
            else:
                values[Probe.MEMORY] = split
        return SystemFacts(values, unavailable)


def cpu_percent(total_cpu, ncpu):
    """Σ pcpu / cores, capped: pcpu is a decaying average, so this is an estimate."""
    if not ncpu:
        return None
    return round(min(100.0, total_cpu / ncpu), 1)
