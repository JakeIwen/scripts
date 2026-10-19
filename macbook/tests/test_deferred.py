"""Deferred one-off jobs: run once when due, purge on success, warn on failure.

Each test drives macbook/scripts/deferred.zsh against a temporary job root and
a loopback stand-in for the ntfy warning topic; launchd is never touched.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "macbook" / "scripts" / "deferred.zsh"
PLIST = REPO / "macbook" / "launchagents" / "com.jacobr.deferred-jobs.plist"
LIVE_CHECKOUT = "/Users/jacobr/dev/scripts/"


class WarningTopic(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self.received.append((self.headers.get("Title"), self.headers.get("Priority"), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class DeferredJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="deferred-tests-")).resolve()
        self.addCleanup(subprocess.run, ["/bin/rm", "-rf", str(self.tmp)])
        self.root = self.tmp / "root"
        WarningTopic.received = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), WarningTopic)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        secrets = self.tmp / "secrets"
        secrets.write_text(f"export NTFY_WARNING_URL='http://127.0.0.1:{server.server_port}/warn'\n")
        self.env = {
            "HOME": os.environ["HOME"],
            "PATH": "/usr/bin:/bin",
            "DEFERRED_ROOT": str(self.root),
            "DEFERRED_SECRETS": str(secrets),
            "DEFERRED_LABEL": "com.jacobr.deferred-jobs-test",
            "DEFERRED_WAKE_GRACE": "0",
            "DEFERRED_LOCK_WAIT": "0.1",
        }

    def deferred(self, *args, check=True):
        result = subprocess.run(
            ["/bin/zsh", str(SCRIPT), *args], env=self.env, cwd=self.tmp,
            capture_output=True, text=True, timeout=60,
        )
        if check and result.returncode != 0:
            self.fail(f"deferred {' '.join(args)} exited {result.returncode}: {result.stderr}")
        return result

    def script(self, body, name="job.zsh"):
        path = self.tmp / name
        path.write_text("#!/bin/zsh\n" + body + "\n")
        return path

    def add(self, due, name, body, *options):
        return Path(self.deferred("add", *options, due, name, str(self.script(body))).stdout.strip())

    def meta(self, job):
        return dict(line.split("=", 1) for line in (job / "job").read_text().splitlines())

    def hold_checker_lock(self):
        lock = self.root / "lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(
            [
                "/bin/zsh", "-c",
                'zmodload zsh/system; : >> "$1"; zsystem flock "$1"; '
                'print -r -- ready; sleep 30',
                "lock-holder", str(lock),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        def stop_holder():
            os.killpg(holder.pid, signal.SIGTERM)
            holder.communicate(timeout=5)

        self.addCleanup(stop_holder)
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        return holder

    def test_due_job_runs_once_in_its_directory_and_is_purged(self):
        marker = self.tmp / "ran"
        job = self.add("2020-01-01", "past", f'print -r -- "$PWD $DEFERRED_JOB_DIR" >> {marker}')
        self.assertEqual(stat.S_IMODE(job.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((job / "run").stat().st_mode), 0o700)
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(marker.read_text().split(), [str(job), str(job)])
        self.assertIn("done 2020-01-01-past", (self.root / "deferred.log").read_text())
        self.assertEqual(WarningTopic.received, [])

    def test_future_job_waits(self):
        job = self.add("+2d", "later", "exit 0")
        self.deferred("run-due")
        self.assertTrue(job.exists())
        self.assertEqual(self.meta(job)["attempts"], "0")
        self.assertIn("pending", self.deferred("list").stdout)

    def test_failure_warns_retries_then_gives_up(self):
        job = self.add("2020-01-01 09:30", "broken", "print oops; exit 3", "--attempts", "2")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["attempts"], "1")
        self.assertEqual((job / "last.log").read_text().strip(), "oops")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assertEqual(self.meta(job)["attempts"], "2")
        titles = [title for title, _, _ in WarningTopic.received]
        self.assertEqual(titles, ["Deferred job failed: 2020-01-01-broken",
                                  "Deferred job gave up: 2020-01-01-broken"])
        self.assertEqual({priority for _, priority, _ in WarningTopic.received}, {"high"})
        bodies = [body for _, _, body in WarningTopic.received]
        self.assertNotIn("oops", "".join(bodies))
        self.assertTrue(all("Mac Panel (macpanel)" in body for body in bodies))
        self.assertTrue(bodies[-1].rstrip().endswith(
            f"Job kept at {job} (see last.log); review it in Mac Panel (macpanel)."
        ))

    def test_job_writable_by_others_is_not_run(self):
        marker = self.tmp / "ran"
        job = self.add("2020-01-01", "unsafe", f"touch {marker}")
        (job / "run").chmod(0o722)
        self.deferred("run-due")
        self.assertFalse(marker.exists())
        self.assertTrue(job.exists())
        self.assertEqual(WarningTopic.received[0][0], "Deferred job not run: 2020-01-01-unsafe")
        self.assertIn("Mac Panel (macpanel)", WarningTopic.received[0][2])

    def test_incomplete_record_warns_once_and_runs_after_repair(self):
        marker = self.tmp / "ran"
        job = self.add("2020-01-01", "repairable", f"touch {marker}")
        record = (job / "job").read_text()
        trimmed = [line for line in record.splitlines() if not line.startswith("timeout=")]
        (job / "job").write_text("\n".join(trimmed) + "\n")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertFalse(marker.exists())
        self.assertEqual([title for title, _, _ in WarningTopic.received],
                         ["Deferred job not run: 2020-01-01-repairable"])
        self.assertIn("incomplete job record", self.deferred("list").stdout)
        (job / "job").write_text(record)
        self.deferred("run-due")
        self.assertTrue(marker.exists())
        self.assertEqual(list((self.root / "flagged").iterdir()), [])

    def test_timeout_stops_a_hung_job(self):
        job = self.add("2020-01-01", "hung", "sleep 30", "--timeout", "1", "--attempts", "1")
        started = time.monotonic()
        self.deferred("run-due")
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(self.meta(job)["state"], "failed")

    def test_background_child_does_not_hold_the_lock(self):
        self.add("2020-01-01", "spawner", "sleep 5 >/dev/null 2>&1 &!")
        self.deferred("run-due")
        marker = self.tmp / "second"
        self.add("2020-01-02", "second", f"touch {marker}")
        self.deferred("run-due")
        self.assertTrue(marker.exists())

    def test_materials_are_copied_and_purged_with_the_job(self):
        material = self.tmp / "notes.txt"
        material.write_text("keep me\n")
        job = self.add("+1d", "material", "cat notes.txt", "--file", str(material))
        self.assertEqual((job / "notes.txt").read_text(), "keep me\n")
        self.deferred("cancel", "material")
        self.assertFalse(job.exists())

    def test_list_json_reports_valid_invalid_and_logs_without_losing_text(self):
        description = 'quote " backslash \\ tab\t café $(touch never)'
        script = self.script("exit 0", "json-job.zsh")
        pending = Path(self.deferred(
            "add", "2099-01-02", "encoded", str(script), description,
        ).stdout.strip())
        failed = self.add(
            "2020-01-01", "json-failed", "print -r -- 'failure output'; exit 4",
            "--attempts", "1",
        )
        self.deferred("run-due")
        invalid = self.root / "jobs" / "2099-01-03-invalid"
        invalid.mkdir(mode=0o700)

        payload = json.loads(self.deferred("list", "--json").stdout)
        self.assertEqual(set(payload), {"checker", "jobs_dir", "jobs"})
        self.assertEqual(set(payload["checker"]), {"agent_loaded", "last_run", "stale"})
        self.assertIsInstance(payload["checker"]["agent_loaded"], bool)
        self.assertIsInstance(payload["checker"]["stale"], bool)
        self.assertIsInstance(payload["checker"]["last_run"], (str, type(None)))
        self.assertEqual(payload["jobs_dir"], str(self.root / "jobs"))
        jobs = {record["id"]: record for record in payload["jobs"]}
        encoded = jobs[pending.name]
        self.assertTrue(encoded["valid"])
        self.assertEqual(encoded["description"], description)
        self.assertIsNone(encoded["last_log"])
        self.assertEqual(encoded["state"], "pending")
        for key in ("attempts", "max_attempts", "timeout"):
            self.assertIsInstance(encoded[key], int)
        self.assertEqual(jobs[failed.name]["last_log"], "failure output\n")
        self.assertEqual(jobs[invalid.name], {"id": invalid.name, "valid": False})

    def test_retry_resets_failed_job_and_refuses_pending_job(self):
        job = self.add("2020-01-01", "retryable", "exit 9", "--attempts", "1")
        self.deferred("run-due")
        result = self.deferred("retry", "retryable")
        self.assertEqual(result.stdout.strip(), f"retrying {job.name} at the next check")
        self.assertEqual(self.meta(job)["due"], "2020-01-01T00:00")
        self.assertEqual(self.meta(job)["state"], "pending")
        self.assertEqual(self.meta(job)["attempts"], "0")
        refused = self.deferred("retry", job.name, check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("already pending", refused.stderr)

    def test_run_now_success_purges_job(self):
        marker = self.tmp / "manual-success"
        job = self.add("2099-01-01", "manual-success", f"touch {marker}")
        result = self.deferred("run-now", "manual-success")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), f"done {job.name}")
        self.assertTrue(marker.exists())
        self.assertFalse(job.exists())
        self.assertIn(f"manual done {job.name}", (self.root / "deferred.log").read_text())

    def test_run_now_failure_records_attempts_without_alerting(self):
        job = self.add(
            "2099-01-01", "manual-failure", "print manual-oops; exit 7",
            "--attempts", "2",
        )
        first = self.deferred("run-now", job.name, check=False)
        self.assertEqual(first.returncode, 3)
        self.assertIn(f"failed {job.name} exit 7", first.stdout)
        self.assertEqual(self.meta(job)["attempts"], "1")
        self.assertEqual(self.meta(job)["state"], "pending")
        self.assertEqual((job / "last.log").read_text().strip(), "manual-oops")
        self.assertEqual(WarningTopic.received, [])

        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 3)
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 3)
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assertEqual(self.meta(job)["attempts"], "3")
        self.assertEqual(WarningTopic.received, [])
        self.assertIn(f"manual failed {job.name}", (self.root / "deferred.log").read_text())

    def test_run_now_refuses_invalid_job(self):
        job = self.add("2099-01-01", "manual-invalid", "exit 0")
        (job / "run").chmod(0o722)
        result = self.deferred("run-now", job.name, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not runnable", result.stderr)
        self.assertTrue(job.exists())

    def test_mutations_refuse_invalid_job_but_cancel_removes_it(self):
        job = self.add("2099-01-01", "invalid-mutations", "exit 0")
        (job / "job").write_text("name=invalid-mutations\n")
        for command in (
            ("retry", job.name),
            ("run-now", job.name),
            ("reschedule", job.name, "2099-02-02"),
        ):
            with self.subTest(command=command[0]):
                result = self.deferred(*command, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn("not runnable", result.stderr)
                self.assertTrue(job.exists())
        self.deferred("cancel", job.name)
        self.assertFalse(job.exists())

    def test_reschedule_renames_and_resets_failed_jobs(self):
        job = self.add("2020-01-01", "move-me", "exit 8", "--attempts", "1")
        self.deferred("run-due")
        result = self.deferred("reschedule", job.name, "2099-02-03 04:05")
        moved = self.root / "jobs" / result.stdout.strip()
        self.assertEqual(moved.name, "2099-02-03-move-me")
        self.assertFalse(job.exists())
        self.assertEqual(self.meta(moved)["due"], "2099-02-03T04:05")
        self.assertEqual(self.meta(moved)["state"], "pending")
        self.assertEqual(self.meta(moved)["attempts"], "0")

    def test_reschedule_refuses_collision_and_bad_due(self):
        first = self.add("2099-03-01", "collision", "exit 0")
        second = self.add("2099-04-01", "collision", "exit 0")
        collision = self.deferred(
            "reschedule", first.name, "2099-04-01 12:00", check=False,
        )
        self.assertEqual(collision.returncode, 1)
        self.assertIn("already exists", collision.stderr)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(self.meta(first)["due"], "2099-03-01T00:00")
        bad_due = self.deferred("reschedule", first.name, "not-a-date", check=False)
        self.assertEqual(bad_due.returncode, 1)
        self.assertIn("invalid due time", bad_due.stderr)
        self.assertEqual(self.meta(first)["due"], "2099-03-01T00:00")

    def test_mutations_exit_75_while_checker_lock_is_held(self):
        job = self.add("2099-01-01", "locked", "exit 0")
        record = (job / "job").read_text()
        self.hold_checker_lock()
        commands = [
            ("retry", job.name),
            ("run-now", job.name),
            ("reschedule", job.name, "2099-02-02"),
            ("cancel", job.name),
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                result = self.deferred(*command, check=False)
                self.assertEqual(result.returncode, 75)
                self.assertIn("the checker is running jobs; try again shortly", result.stderr)
                self.assertTrue(job.exists())
                self.assertEqual((job / "job").read_text(), record)
                self.assertFalse((job / "last.log").exists())

    def test_add_rejects_bad_input(self):
        good = str(self.script("exit 0"))
        for due, name in [("2026-02-30", "x"), ("tomorrow", "x"), ("2026-11-16", "Bad_Name"),
                          ("2026-11-16", "../escape")]:
            with self.subTest(due=due, name=name):
                self.assertNotEqual(self.deferred("add", due, name, good, check=False).returncode, 0)
        plain = self.tmp / "plain"
        plain.write_text("echo no shebang\n")
        self.assertNotEqual(self.deferred("add", "+1d", "plain", str(plain), check=False).returncode, 0)
        self.deferred("add", "2026-11-16", "twice", good)
        self.assertNotEqual(self.deferred("add", "2026-11-16", "twice", good, check=False).returncode, 0)
        self.assertNotEqual(self.deferred("cancel", "../root", check=False).returncode, 0)

    def test_watchdog_warns_only_when_jobs_wait_on_a_silent_checker(self):
        self.deferred("watchdog")
        self.assertEqual(WarningTopic.received, [])
        self.add("+1d", "waiting", "exit 0")
        self.deferred("watchdog")
        self.assertEqual(WarningTopic.received[0][0], "Deferred jobs checker stopped")
        self.assertIn("Check Mac Panel or: deferred.zsh status", WarningTopic.received[0][2])
        self.deferred("run-due")
        WarningTopic.received.clear()
        self.deferred("watchdog")
        self.assertEqual(WarningTopic.received, [])

    def test_missing_warning_url_is_logged_not_fatal(self):
        self.env["DEFERRED_SECRETS"] = str(self.tmp / "absent")
        self.add("2020-01-01", "quiet", "exit 1", "--attempts", "1")
        self.deferred("run-due")
        self.assertIn("warning undelivered", (self.root / "deferred.log").read_text())


class DeferredLaunchAgentTests(unittest.TestCase):
    def test_plist_runs_the_checker_hourly_and_at_login(self):
        plist = plistlib.loads(PLIST.read_bytes())
        self.assertEqual(plist["Label"], "com.jacobr.deferred-jobs")
        self.assertEqual(plist["ProgramArguments"][2], "run-due")
        program = plist["ProgramArguments"][1]
        self.assertTrue(program.startswith(LIVE_CHECKOUT))
        self.assertEqual(REPO / program[len(LIVE_CHECKOUT):], SCRIPT)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["StartInterval"], 3600)


if __name__ == "__main__":
    unittest.main()
