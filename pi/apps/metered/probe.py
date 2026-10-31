"""One bounded subprocess boundary to the existing flat-deployed Pi probes."""
import json
import subprocess
import time

from shared.metered.evidence import normalize
from shared.metered.records import UplinkEvidence
from .config import PROBE_TIMEOUT, UPLINK_HELPER


def collect(home_network, command=subprocess.run, wall_clock=time.time):
    started = wall_clock()
    try:
        result = command(['/usr/bin/python3', '-I', '-B', UPLINK_HELPER],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         timeout=PROBE_TIMEOUT)
        if result.returncode:
            raise ValueError('probe failed')
        return normalize(json.loads(result.stdout), home_network)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        # Raw SSH diagnostics and network identities do not belong in service logs.
        return UplinkEvidence(started, None, [], ['uplink inspection failed'])
