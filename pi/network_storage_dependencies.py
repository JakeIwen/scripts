"""Read-only system-monitor prerequisites for the storage installer."""
import re

import deploy_network_flight_recorder as base


MONITOR_DEPENDENCIES = {
    'pi/scripts/system_event_monitor.py': '/home/pi/scripts/system_event_monitor.py',
    **base.MONITOR_PACKAGE_DEPENDENCIES,
}


def make_pins(repo):
    pins = {}
    for source, destination in MONITOR_DEPENDENCIES.items():
        info = base.regular_info(repo / source)
        if info is None:
            raise ValueError('missing regular system monitor dependency: ' + source)
        pins[destination] = info['sha256']
    return pins


def validate_pins(plan, *, historical=False):
    # Saved releases predating these prerequisites must retain exact rollback.
    if historical and 'monitor_dependencies' not in plan:
        return
    pins = plan.get('monitor_dependencies')
    if not isinstance(pins, dict) or set(pins) != set(MONITOR_DEPENDENCIES.values()):
        raise ValueError('manifest differs from system monitor dependency allowlist')
    if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
           for value in pins.values()):
        raise ValueError('invalid system monitor dependency checksum')


def verify_pins(plan):
    for path, expected in plan['monitor_dependencies'].items():
        info = base.regular_info(path)
        if info is None or info['sha256'] != expected:
            raise ValueError('system monitor dependency differs from checked source: ' + path)


def verify_local_pins(plan, current):
    if plan['monitor_dependencies'] != current['monitor_dependencies']:
        raise ValueError('local system monitor dependency changed after check; create a fresh checked plan')
