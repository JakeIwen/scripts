"""Owner-session BTT API boundary. The unattended monitor never imports/calls it."""
import json
from pathlib import Path
import subprocess
import tempfile
import time

from ..btt_common import ROOT
from .storage import write_json


def call_worker(name, document, mode=None):
    script = Path(__file__).with_name(name)
    with tempfile.TemporaryDirectory(prefix='btt-guard-api-') as directory:
        source = Path(directory)/'input.json'
        write_json(source, document)
        arguments = ['/usr/bin/osascript', '-l', 'JavaScript', str(script)]
        if mode:
            arguments.append(mode)
        arguments.extend([str(ROOT), str(source)])
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Native errors can include payload fragments; keep them off normal logs
        # and notifications. The private pre-operation backup remains available.
        raise RuntimeError('BTT API request failed. Use the owning user’s Terminal; no result was verified.')
    return result.stdout.strip()


def export_graph(snapshot):
    records = snapshot['records']
    roots = [uid for uid, row in records.items() if row['parent'] not in records]
    result = json.loads(call_worker('export.js', {'ids': roots}))
    if not isinstance(result, list):
        raise ValueError('BTT returned an invalid export shape.')
    return result


def wait_for_exports(snapshot, timeout=20):
    """A running process does not yet imply its Apple Events API is ready."""
    deadline = time.monotonic()+timeout
    while True:
        try:
            return export_graph(snapshot)
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise RuntimeError('BTT restarted, but its read-only API did not become ready in time.') from None
            time.sleep(min(.25, max(0, deadline-time.monotonic())))


def restart_btt():
    # Existing standalone maintenance modules have a legacy top-level import
    # contract; their CLI is the boundary, rather than another sys.path shim.
    result = subprocess.run(['/usr/bin/python3', '-B', str(ROOT/'macbook/bettertouchtool/btt.py'), 'restart'],
                            capture_output=True, text=True, timeout=75)
    if result.returncode:
        raise RuntimeError('BTT did not complete a clean restart; no force quit attempted.')
