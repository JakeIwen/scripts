"""JSON CLI execution and the dashboard's single background-job lifecycle."""
import datetime
import json
import subprocess
import threading

from .van_dashboard_common import run_command


class CliBusy(RuntimeError):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class CliJobController:
    def __init__(self, cli, label, command=run_command, timeout=20, job_timeout=900):
        self.cli = tuple(cli)
        self.label = label
        self.command = command
        self.timeout = timeout
        self.job_timeout = job_timeout
        self._lock = threading.RLock()
        self._job = None

    def _run(self, *arguments, timeout=None):
        try:
            result = self.command([*self.cli, *arguments], timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'{self.label} command timed out; check status before retrying.') from exc
        except OSError as exc:
            raise RuntimeError(f'Could not start {self.label.lower()}.') from exc
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f'{self.label} returned invalid JSON.') from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f'{self.label} returned an invalid record.')
        if result.returncode or 'error' in payload and payload['error']:
            raise RuntimeError(str(payload.get('error') or f'{self.label} command failed.'))
        return payload

    def status(self):
        status = self._run('status')
        with self._lock:
            status['job'] = dict(self._job) if self._job else None
        return status

    def _start_job(self, stage, arguments):
        # Callers keep the lock through admission and thread start.
        self._job = dict(stage=stage, started_at=_now(), finished_at=None,
                         ok=None, error=None, result_id=None)
        job = dict(self._job)
        threading.Thread(target=self._work, args=(arguments,), daemon=True).start()
        return job

    def _work(self, arguments):
        try:
            result = self._run(*arguments, timeout=self.job_timeout)
            outcome = dict(ok=True, error=None, result_id=result.get('id'))
        except Exception as exc:
            outcome = dict(ok=False, error=str(exc), result_id=None)
        with self._lock:
            self._job.update(outcome, finished_at=_now())
