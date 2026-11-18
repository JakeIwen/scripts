#!/usr/bin/python3 -IB
"""`deferred.zsh list --json`: turns the NUL-separated record stream that
deferred.zsh writes on stdin into the JSON inventory the Mac Panel reads."""
import json
import pathlib
import sys


def tail_log(path):
    try:
        data = pathlib.Path(path).read_bytes()
    except FileNotFoundError:
        return None
    lines = data.splitlines(keepends=True)[-64:]
    return b"".join(lines)[-8192:].decode("utf-8", errors="replace")


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
            result["jobs"].append({"id": job_id, "valid": False})
            continue
        if kind != "V":
            raise SystemExit("invalid deferred job stream")
        result["jobs"].append({
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
            "remote_state": next(fields) or None,
            "remote_run": next(fields) or None,
        })
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
