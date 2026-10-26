"""Loopback-only fake Responses API for offline Codex TUI tests and demos."""
import argparse
from dataclasses import dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading


class Scenario(str, Enum):
    FAIL_THEN_SUCCEED = "fail-then-succeed"
    ALWAYS_FAIL = "always-fail"
    MID_STREAM = "mid-stream"
    SUCCESS = "success"


@dataclass(frozen=True)
class RequestRecord:
    count: int
    path: str
    user_texts: tuple
    turn_number: int

    @property
    def contains_continue(self):
        return any(text.strip() == "continue" for text in self.user_texts)

    def as_dict(self):
        return {"count": self.count, "path": self.path, "turn_number": self.turn_number,
                "user_texts": list(self.user_texts), "contains_continue": self.contains_continue}


def user_texts(body):
    data = body.get("input", [])
    if isinstance(data, str):
        return (data,)
    return tuple(part.get("text", "") for item in data if item.get("role") == "user"
                 for part in item.get("content", []) if isinstance(part, dict))


def event(kind, **fields):
    return ("event: {}\ndata: {}\n\n".format(
        kind, json.dumps({"type": kind, **fields}))).encode("utf-8")


def response_events(number, failure, partial):
    response_id = "resp_{}".format(number)
    yield event("response.created", response={"id": response_id})
    message = {"id": "message_{}".format(number), "type": "message", "role": "assistant",
               "content": [{"type": "output_text", "text": "Fixture success {}.".format(number)}]}
    if partial:
        yield event("response.output_item.added", output_index=0,
                    item={**message, "content": []})
        yield event("response.content_part.added", item_id=message["id"], output_index=0,
                    content_index=0, part={"type": "output_text", "text": ""})
        yield event("response.output_text.delta", item_id=message["id"], output_index=0,
                    content_index=0, delta="Partial fixture output before overload.")
    if failure:
        yield event("response.failed", response={"id": response_id, "error": {
            "code": "server_is_overloaded", "message": "Fixture capacity error"}})
    else:
        yield event("response.output_item.done", output_index=0, item=message)
        yield event("response.completed", response={"id": response_id, "output": [message],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                              "input_tokens_details": None, "output_tokens_details": None}})


class ResponsesServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, scenario=Scenario.FAIL_THEN_SUCCEED, failures=1, port=0, log_path=None):
        self.scenario = Scenario(scenario)
        self.failures = failures
        self.log_path = Path(log_path) if log_path else None
        self.records = []
        self.changed = threading.Condition()
        super().__init__(("127.0.0.1", port), ResponsesHandler)

    @property
    def base_url(self):
        return "http://127.0.0.1:{}/v1".format(self.server_address[1])

    def record(self, path, body):
        with self.changed:
            texts = user_texts(body)
            # Codex also asks this provider to title the thread; that is not a failed turn.
            title_request = any(text.startswith("Generate a concise, single-line task title")
                                for text in texts)
            turn_number = (0 if title_request else
                           sum(record.turn_number > 0 for record in self.records) + 1)
            record = RequestRecord(len(self.records) + 1, path, texts, turn_number)
            self.records.append(record)
            if self.log_path:
                with self.log_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record.as_dict()) + "\n")
            self.changed.notify_all()
            return record

    def snapshot(self):
        with self.changed:
            return list(self.records)

    def turn_requests(self):
        return [record for record in self.snapshot() if record.turn_number]

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class ResponsesHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ("/v1/responses", "/responses"):
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            record = self.server.record(self.path, body)
        except (ValueError, TypeError, AttributeError):
            self.send_error(400)
            return
        failure = record.turn_number > 0 and (
            self.server.scenario == Scenario.ALWAYS_FAIL
            or self.server.scenario in (Scenario.FAIL_THEN_SUCCEED, Scenario.MID_STREAM)
            and record.turn_number <= self.server.failures)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for data in response_events(record.count, failure,
                                        failure and self.server.scenario == Scenario.MID_STREAM):
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=[item.value for item in Scenario],
                        default=Scenario.FAIL_THEN_SUCCEED.value)
    parser.add_argument("--failures", type=int, default=1)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args(argv)
    if args.failures < 0:
        parser.error("--failures must be nonnegative")
    server = ResponsesServer(args.scenario, args.failures, args.port, args.log)
    print(server.base_url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
