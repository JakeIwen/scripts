import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from shared.codex_autocontinue.fake_server import ResponsesServer, Scenario


class FakeServerTests(unittest.TestCase):
    def server(self, scenario, **options):
        server = ResponsesServer(scenario, **options).start()
        self.addCleanup(server.stop)
        return server

    def request(self, server, text="hello"):
        body = {"input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}
        request = Request(server.base_url + "/responses", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream")
            raw = response.read().decode()
        return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]

    def test_fail_k_times_then_succeed_and_log_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.jsonl"
            server = self.server(Scenario.FAIL_THEN_SUCCEED, failures=2, log_path=path)
            kinds = [self.request(server, text)[-1]["type"] for text in ("hello", "continue", "continue")]
            self.assertEqual(kinds, ["response.failed", "response.failed", "response.completed"])
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([item["count"] for item in records], [1, 2, 3])
            self.assertEqual([item["contains_continue"] for item in records], [False, True, True])

    def test_all_failures_use_capacity_code(self):
        server = self.server(Scenario.ALWAYS_FAIL)
        for _ in range(3):
            events = self.request(server)
            self.assertEqual(events[-1]["response"]["error"]["code"], "server_is_overloaded")

    def test_partial_stream_fails_after_output_then_succeeds(self):
        server = self.server(Scenario.MID_STREAM, failures=1)
        events = self.request(server)
        self.assertIn("response.output_text.delta", [item["type"] for item in events[:-1]])
        self.assertEqual(events[-1]["type"], "response.failed")
        self.assertEqual(self.request(server)[-1]["type"], "response.completed")

    def test_plain_success_and_background_titles_do_not_consume_failure_budget(self):
        server = self.server(Scenario.SUCCESS)
        self.assertEqual(self.request(server)[-1]["type"], "response.completed")
        server = self.server(Scenario.FAIL_THEN_SUCCEED, failures=1)
        title = "Generate a concise, single-line task title for this fixture"
        self.assertEqual(self.request(server, title)[-1]["type"], "response.completed")
        self.assertEqual(self.request(server)[-1]["type"], "response.failed")
        self.assertEqual(len(server.snapshot()), 2)
        self.assertEqual(len(server.turn_requests()), 1)

    def test_unknown_routes_fail_closed(self):
        server = self.server(Scenario.SUCCESS)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(server.base_url + "/unknown", data=b"{}"), timeout=3)
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(server.snapshot(), [])
