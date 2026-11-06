"""One daemon writer samples existing read adapters; no tool launch paths live here."""
from datetime import datetime
import logging
import threading
import time

from crontab_adapter import program_name
from logs_adapter import log_samples
from panel_contracts import HistorySample, Level, Section
from usage_adapter import WINDOWS

CADENCES = {Section.SERVICES: 60, Section.METERED: 60, Section.RESTORE: 60,
            Section.CRONTAB: 300, Section.DEFERRED: 300, Section.USAGE: 900, Section.LOGS: 900}
PRUNE_INTERVAL = 3600
LOG = logging.getLogger(__name__)


def service_level(row):
    if row['loaded'] is None:
        return Level.UNKNOWN
    if row['loaded'] is False:
        return Level.IDLE if row['disabled'] else Level.WARN
    if row['pid']:
        return Level.OK
    return Level.WARN if row['last_exit'] not in (None, 0) else Level.IDLE


def service_samples(payload):
    return [(row['label'], service_level(row), row['pid'],
             f"{row['state']}; last exit {row['last_exit']}") for row in payload['services']]


def metered_samples(payload, ts):
    state = payload['state']
    fresh = payload['fresh']
    metered = state['verdict']['metered'] if fresh else None
    level = Level.UNKNOWN if metered is None else Level.WARN if metered else Level.OK
    # The CLI exposes only matched SSIDs, not the current unmetered Wi-Fi identity.
    ssids = sorted({row['ssid'] for row in state['verdict']['matched']}) if fresh else []
    rows = [('verdict', level, None if metered is None else int(metered),
             'unknown' if metered is None else 'metered' if metered else 'unmetered'),
            ('ssid', level if ssids else Level.UNKNOWN, None, ', '.join(ssids) or None),
            ('low_data_mode', Level.ERROR if payload['low_data_mode']['error'] else Level.OK,
             int(payload['low_data_mode']['enabled']), None)]
    for host_state, valid in ((state, fresh), (payload['pi_state'], None)):
        if host_state is None:
            continue
        if valid is None:
            valid = host_state['evaluated_at'] <= ts < (host_state['valid_until'] or 0)
        for key, consumer in host_state['consumers'].items():
            actual = consumer['actual'] if valid else 'unknown'
            level = (Level.UNKNOWN if not valid or actual == 'unknown' else
                     Level.OK if consumer['permitted'] else Level.IDLE if actual == 'held' else Level.WARN)
            rows.append((f'consumer:{key}', level, None, actual))
    return rows


def deferred_samples(payload):
    rows = []
    for job in payload['jobs']:
        if not job['valid']:
            rows.append((job['id'], Level.ERROR, None, 'invalid'))
            continue
        due = datetime.fromisoformat(job['due']).timestamp()
        rows.append((job['id'], Level.ERROR if job['state'] == 'failed' else Level.IDLE,
                     due, job['state']))
    return rows


def usage_samples(payload):
    levels = {'ok': Level.OK, 'constrained': Level.WARN, 'out': Level.ERROR, 'unknown': Level.UNKNOWN}
    rows = []
    for key in WINDOWS:
        window = payload[key]
        family = 'gpt' if key.startswith('codex') else 'claude'
        rows.append((key + '_left', levels[payload['availability'][family]] if window else Level.UNKNOWN,
                     window['left'] if window else None, None))
    position = payload.get('claude_position')
    rows += [('credits', levels[payload['availability']['gpt']], (payload['credits'] or {}).get('left'), None),
             ('claude_line_left', Level.OK if position and 'line_left' in position else Level.UNKNOWN,
              position['line_left'] if position and 'line_left' in position else None, None),
             ('lean', Level.OK, None, payload.get('lean'))]
    return rows


def samples(section, payload, ts):
    """Pure payload-to-record conversion; ts is supplied by the sampling runtime."""
    if section == Section.SERVICES:
        rows = service_samples(payload)
    elif section == Section.METERED:
        rows = metered_samples(payload, ts)
    elif section == Section.DEFERRED:
        rows = deferred_samples(payload)
    elif section == Section.USAGE:
        rows = usage_samples(payload)
    elif section == Section.CRONTAB:
        rows = [(job['id'], Level.IDLE if job['paused'] else Level.OK,
                 job['log_modified'], f"{job['expression']} · {program_name(job['command'])}") for job in payload['jobs']]
    elif section == Section.LOGS:
        rows = log_samples(payload)
    elif section == Section.RESTORE:
        rows = [('sessions', Level.OK if payload['sessions_open'] is not None else Level.UNKNOWN,
                 payload['sessions_open'], None),
                ('snapshot', Level.OK if payload['last_snapshot'] is not None else Level.UNKNOWN,
                 payload['last_snapshot'], None)]
    else:
        raise ValueError('Unsupported history section')
    return [HistorySample(section=section, key=key, ts=ts, level=level, value=value, text=text)
            for key, level, value, text in rows]


class Sampler:
    def __init__(self, adapters, store, cadences=None, clock=time.monotonic, wall_clock=time.time):
        self.adapters, self.store = adapters, store
        self.cadences = CADENCES if cadences is None else cadences
        self.clock, self.wall_clock = clock, wall_clock
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name='panel-history', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        # CLI calls already have deadlines; do not close adapters beneath an in-flight read.
        self.thread.join()

    def run(self):
        due = dict.fromkeys(self.cadences, 0.0)
        prune_at = 0.0
        while not self.stopped.is_set():
            now = self.clock()
            for section, interval in self.cadences.items():
                if self.stopped.is_set():
                    break
                if now < due[section]:
                    continue
                try:
                    payload = self.adapters[section].read()
                    self.store.record(samples(section, payload, self.wall_clock()))
                except Exception:
                    LOG.exception('History sampling failed for %s', section.value)
                due[section] = self.clock() + interval
            if now >= prune_at:
                try:
                    self.store.prune()
                except Exception:
                    LOG.exception('History pruning failed')
                prune_at = self.clock() + PRUNE_INTERVAL
            deadline = min([*due.values(), prune_at])
            self.stopped.wait(max(0.01, deadline - self.clock()))
