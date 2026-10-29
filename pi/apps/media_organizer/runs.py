"""Durable per-item run journal and newest-first history."""
from . import config, records
from .storage import read_json, record_path, write_json


def save(run):
    write_json(record_path(config.RUNS_DIR, run.id), run.to_dict())


def load(run_id):
    return records.Run.from_dict(read_json(record_path(config.RUNS_DIR, run_id)))


def list_runs():
    return sorted((records.Run.from_dict(read_json(path)) for path in config.RUNS_DIR.glob('*.json')),
                  key=lambda run: (run.started_at, run.id), reverse=True)
