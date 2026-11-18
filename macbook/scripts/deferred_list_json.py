#!/usr/bin/python3 -IB
"""`deferred.zsh list --json`: turns the NUL-separated record stream that
deferred.zsh writes on stdin into the JSON inventory the Mac Panel reads.

A vanpi job's Pi run state is read here from its state files as data
(deferred_pi_state); a job whose state cannot be read becomes an invalid row
with the reason, and the other rows are unaffected."""
import json
import pathlib
import sys

# -I keeps the script's own folder off sys.path; the module it needs sits beside it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from deferred_pi_state import PiJob, StateError, snapshot  # noqa: E402


def tail_log(path):
    try:
        data = pathlib.Path(path).read_bytes()
    except FileNotFoundError:
        return None
    lines = data.splitlines(keepends=True)[-64:]
    return b"".join(lines)[-8192:].decode("utf-8", errors="replace")


def with_remote_state(record, pi_dir):
    """Adds remote_state, remote_run and remote_token; null for jobs that do not run on vanpi."""
    if not pi_dir:
        return {**record, "remote_state": None, "remote_run": None, "remote_token": None}
    try:
        state = snapshot(PiJob(pi_dir))
    except StateError as error:
        return {"id": record["id"], "valid": False, "problem": f"vanpi run state unreadable: {error}"}
    return {**record, "remote_state": state.state.value, "remote_run": state.run_id, "remote_token": state.token}


def main():
    fields = iter(part.decode("utf-8", errors="replace")
                  for part in sys.stdin.buffer.read().split(b"\0")[:-1])
    if next(fields) != "H":
        raise SystemExit("invalid deferred list stream")
    loaded = next(fields) == "1"
    has_last_run = next(fields) == "1"
    last_run = next(fields)
    stale = next(fields) == "1"
    result = {
        "checker": {
            "agent_loaded": loaded,
            "last_run": last_run if has_last_run else None,
            "stale": stale,
        },
        "jobs_dir": next(fields),
        "jobs": [],
    }
    for kind in fields:
        job_id = next(fields)
        if kind == "I":
            result["jobs"].append({"id": job_id, "valid": False,
                                   "problem": "invalid job directory, record or run file"})
            continue
        if kind != "V":
            raise SystemExit("invalid deferred job stream")
        record = {
            "id": job_id,
            "valid": True,
            "name": next(fields),
            "due": next(fields),
            "state": next(fields),
            "attempts": int(next(fields)),
            "max_attempts": int(next(fields)),
            "timeout": int(next(fields)),
            "description": next(fields),
            "created": next(fields),
            "origin": next(fields),
            "last_log": tail_log(next(fields)),
            "give_up": next(fields),
            "agent": next(fields) == "1",
            "ready": next(fields) or None,
            "last_not_ready": next(fields) or None,
            "not_ready_checks": int(next(fields)),
            "host": next(fields) or None,
            "model": next(fields) or None,
            "effort": next(fields) or None,
        }
        result["jobs"].append(with_remote_state(record, next(fields)))
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
