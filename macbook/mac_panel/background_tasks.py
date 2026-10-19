"""In-memory task receipts; the deferred CLI remains the durable job owner."""
from concurrent.futures import ThreadPoolExecutor
import secrets
import threading

from log_redaction import redact
from panel_contracts import ApiError, TaskRecord, TaskState


class BackgroundTasks:
    def __init__(self):
        self.lock = threading.Lock()
        self.records: dict[str, TaskRecord] = {}
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='panel-task')

    def start(self, job, operation) -> TaskRecord:
        with self.lock:
            running = [r for r in self.records.values() if r['state'] == TaskState.RUNNING]
            if any(r['job'] == job for r in running):
                raise ApiError(409, 'This job is already running from the panel')
            if len(running) >= 4:
                raise ApiError(409, 'The panel already has four running tasks')
            if len(self.records) >= 200:
                oldest = next(k for k, r in self.records.items() if r['state'] != TaskState.RUNNING)
                del self.records[oldest]
            task = TaskRecord(id=secrets.token_hex(16), job=job, state=TaskState.RUNNING,
                              exit_status=None, message='Running')
            self.records[task['id']] = task
            self.executor.submit(self.finish, task['id'], operation)
            return dict(task)

    def finish(self, task_id, operation):
        code = None
        try:
            result = operation()
            code = result.returncode
            message = (result.stderr or result.stdout).strip()
            if code == 75:
                message = 'The checker is running jobs; try again shortly'
            state = TaskState.DONE if code == 0 else TaskState.FAILED
        except Exception as exc:
            message, state = str(exc), TaskState.FAILED
        with self.lock:
            self.records[task_id].update(state=state, exit_status=code, message=redact(message))

    def get(self, task_id):
        with self.lock:
            if task_id not in self.records:
                raise ApiError(404, 'Task not found; a panel restart clears task receipts')
            return dict(self.records[task_id])

    def list(self):
        with self.lock:
            return [dict(task) for task in self.records.values()]

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
