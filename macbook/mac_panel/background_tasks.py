"""Bounded in-memory task receipts; each invoked tool owns its durable state."""
from concurrent.futures import ThreadPoolExecutor
import secrets
import threading
import time

from log_redaction import redact
from panel_contracts import ApiError, Section, TaskRecord, TaskState


class BackgroundTasks:
    def __init__(self):
        self.lock = threading.Lock()
        self.records: dict[str, TaskRecord] = {}
        self.owners: dict[str, Section] = {}
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='panel-task')

    def start(self, section: Section, job, operation) -> TaskRecord:
        with self.lock:
            running = [r for r in self.records.values() if r['state'] == TaskState.RUNNING]
            if any(self.owners[r['id']] == section and r['job'] == job for r in running):
                raise ApiError(409, 'This job is already running from the panel')
            if len(running) >= 4:
                raise ApiError(409, 'The panel already has four running tasks')
            if len(self.records) >= 200:
                oldest = next(k for k, r in self.records.items() if r['state'] != TaskState.RUNNING)
                del self.records[oldest]
                del self.owners[oldest]
            task = TaskRecord(id=secrets.token_hex(16), job=job, state=TaskState.RUNNING,
                              exit_status=None, message='Running', started_at=time.time(),
                              finished_at=None)
            self.records[task['id']] = task
            self.owners[task['id']] = section
            self.executor.submit(self.finish, task['id'], operation)
            return dict(task)

    def finish(self, task_id, operation):
        code = None
        try:
            result = operation()
            code = result.returncode
            message = (result.stderr or result.stdout).strip()
            if code and not message:
                message = f'Tool exited {code}; refresh its state before retrying'
            state = TaskState.DONE if code == 0 else TaskState.FAILED
        except Exception as exc:
            message, state = str(exc), TaskState.FAILED
        with self.lock:
            self.records[task_id].update(state=state, exit_status=code, message=redact(message),
                                         finished_at=time.time())

    def get(self, task_id):
        with self.lock:
            if task_id not in self.records:
                raise ApiError(404, 'Task not found; a panel restart clears task receipts')
            return dict(self.records[task_id])

    def list(self, section: Section):
        with self.lock:
            return [dict(task) for task in self.records.values()
                    if self.owners[task['id']] == section]

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
