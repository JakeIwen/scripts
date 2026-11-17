"""Status command and watcher lifecycle against a fake Pi `serve` behind a fake ssh."""
import json
import os
import signal
import socket
import stat
import time
import unittest

from media_scrub_fixture import SAMPLE, ScrubCase, alive


class ColdStart(ScrubCase):
    def test_cold_start_with_wait_returns_a_fresh_sample(self):
        status = self.status(wait=5)
        self.assertTrue(status["ok"])
        self.assertEqual((status["source"], status["title"], status["media_id"]),
                         ("vlc", "Movie", "vlc:item:7"))
        self.assertTrue(status["seekable"])
        self.assertLess(status["age"], 2)
        self.assertTrue(alive(self.watcher_pid()))
        info = os.lstat(os.path.join(self.state, "ctl.sock"))
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    def test_status_without_wait_returns_at_once_even_when_cold(self):
        status = json.loads(self.run_cli("status").stdout)
        self.assertFalse(status["ok"])

    def test_text_forms(self):
        result = self.run_cli("status", "--text", "--wait", "5")
        self.assertEqual(result.stdout.rstrip("\n"), "Movie \n0:01:05 / 1:30:00")

    def test_nothing_playing_prints_an_empty_text(self):
        self.configure(sample=dict(SAMPLE, source="none", state="STOPPED", title=None, media_id=None,
                                   can_seek=False, position=0.0, duration=0.0))
        result = self.run_cli("status", "--text", "--wait", "5")
        self.assertEqual(result.stdout, "\n")

    def test_status_unavailable_when_no_sample_ever_arrives(self):
        self.configure(no_samples=True)
        result = self.run_cli("status", "--text", "--wait", "1")
        self.assertEqual(result.stdout.rstrip("\n"), "Status unavailable")
        self.assertEqual(result.returncode, 0)

    def test_unsafe_state_dir_is_refused(self):
        os.chmod(self.state, 0o755)
        result = self.run_cli("status", "--text")
        self.assertEqual(result.stdout.rstrip("\n"), "Status unavailable")
        self.assertEqual(self.serve_pids(), [])
        self.assertEqual(json.loads(self.run_cli("status").stdout)["ok"], False)


class SingleWatcher(ScrubCase):
    def test_overlapping_status_calls_spawn_one_watcher(self):
        calls = [self.popen_cli("status", "--wait", "5") for _ in range(6)]
        outputs = [call.communicate(timeout=20)[0] for call in calls]
        self.assertTrue(all(json.loads(out)["ok"] for out in outputs), outputs)
        self.assertEqual(len(self.serve_pids()), 1)
        self.assertEqual(self.read_state("watch.json")["pid"], self.watcher_pid())


class Lifecycle(ScrubCase):
    idle_seconds = "1"

    def test_heartbeat_idle_ends_watcher_ssh_and_serve(self):
        self.start_watcher()
        watcher, serve = self.watcher_pid(), self.serve_pids()[0]
        self.wait_gone(watcher, 10)
        self.wait_gone(serve, 5)
        self.assertFalse(os.path.exists(os.path.join(self.state, "ctl.sock")))
        self.assertIsNone(self.read_state("failures.json"))

    def test_sigterm_reaps_the_ssh_child(self):
        self.start_watcher()
        watcher, serve = self.watcher_pid(), self.serve_pids()[0]
        os.kill(watcher, signal.SIGTERM)
        self.wait_gone(watcher, 5)
        self.wait_gone(serve, 5)

    def test_stubborn_ssh_is_terminated_after_stdin_closes(self):
        self.configure(ignore_eof=True)
        self.start_watcher()
        watcher, serve = self.watcher_pid(), self.serve_pids()[0]
        os.kill(watcher, signal.SIGTERM)
        self.wait_gone(watcher, 8)
        self.wait_gone(serve, 3)

    def test_kill_9_of_the_watcher_ends_serve_on_stdin_eof(self):
        self.start_watcher()
        watcher, serve = self.watcher_pid(), self.serve_pids()[0]
        os.kill(watcher, signal.SIGKILL)
        self.wait_gone(serve, 5)
        self.assertEqual(self.log_lines("eof"), ["eof"])

    def test_stale_ctl_sock_is_replaced(self):
        path = os.path.join(self.state, "ctl.sock")
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(path)
        stale.close()                                  # leaves a dead socket file behind
        self.start_watcher()
        self.assertEqual(self.seek()[0], 0)

    def test_regular_file_at_ctl_sock_is_replaced(self):
        open(os.path.join(self.state, "ctl.sock"), "w").close()
        self.start_watcher()
        self.assertEqual(self.seek()[0], 0)


class FailureBudget(ScrubCase):
    def setUp(self):
        super().setUp()
        self.counter = os.path.join(self.dir, "ssh-count")
        self.good_ssh = self.ssh
        self.set_ssh(self.write_ssh("bad-ssh", 'echo x >> "%s"\nexit 255' % self.counter))

    def attempts(self):
        try:
            with open(self.counter) as source:
                return len(source.read().split())
        except OSError:
            return 0

    def failure_count(self):
        return (self.read_state("failures.json") or {}).get("count", 0)

    def test_failures_back_off_and_a_good_sample_resets(self):
        first = self.status(wait=3)
        self.assertFalse(first["ok"])
        self.wait_for(lambda: self.failure_count() == 1, 5, "first failure")
        record = self.read_state("failures.json")
        self.assertAlmostEqual(record["next_start_at"] - time.time(), 2, delta=1.5)
        self.assertEqual(self.attempts(), 1)
        self.assertIn("ssh session ended", self.status(wait=0)["error"])

        for _ in range(3):                             # inside the window: no new attempt
            self.run_cli("status")
        self.assertEqual(self.attempts(), 1)

        self.expire_window()
        self.run_cli("status")
        self.wait_for(lambda: self.failure_count() == 2, 5, "second failure")
        self.assertEqual(self.attempts(), 2)
        self.assertAlmostEqual(self.read_state("failures.json")["next_start_at"] - time.time(), 4, delta=1.5)

        self.expire_window()
        self.set_ssh(self.good_ssh)
        self.assertTrue(self.status(wait=5)["ok"])
        self.assertIsNone(self.read_state("failures.json"))

    def expire_window(self):
        record = self.read_state("failures.json")
        record["next_start_at"] = time.time() - 1
        with open(os.path.join(self.state, "failures.json"), "w") as out:
            json.dump(record, out)
        os.unlink(os.path.join(self.state, "spawn.json"))   # the 3 s spawn claim, too


if __name__ == "__main__":
    unittest.main()
