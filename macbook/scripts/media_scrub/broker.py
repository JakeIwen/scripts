"""Seek ordering: one request in flight, the latest one pending, per-page seq fencing.

Pure logic: the watcher supplies I/O callbacks, so the rules are testable alone."""
from . import config


def refusal(reason, message):
    return {"ok": False, "superseded": reason == "superseded", "reason": reason, "message": message}


class SeekBroker:
    def __init__(self, send_to_pi, reply, check):
        self._send = send_to_pi      # (message dict) -> None, may raise OSError
        self._reply = reply          # (client, result dict) -> None
        self._check = check          # (entry) -> refusal message or None
        self.last_seq = {}           # page -> highest seq accepted, whatever the arrival order
        self.in_flight = None
        self.pending = None
        self.next_req = 1
        self._unpublished_ack = None
        self.ack = None

    def submit(self, client, request, now):
        page, seq = request["page"], request["seq"]
        if seq <= self.last_seq.get(page, -1):
            self._reply(client, refusal("superseded", "a newer seek for this page was already accepted"))
            return
        self.last_seq[page] = seq
        entry = dict(request, client=client)
        message = self._check(entry)
        if message:
            self._reply(client, refusal(message[0], message[1]))
            return
        if self.in_flight is None:
            self._dispatch(entry, now)
            return
        if self.pending is not None:
            self._reply(self.pending["client"],
                        refusal("superseded", "replaced by a newer seek"))
        self.pending = entry

    def _dispatch(self, entry, now):
        req = self.next_req
        self.next_req += 1
        entry["req"] = req
        entry["deadline"] = now + config.SEEK_BROKER_TIMEOUT
        self.in_flight = entry
        try:
            self._send({"type": "seek", "req": req, "position": entry["position"],
                        "source": entry["source"], "media_id": entry["media_id"]})
        except OSError:
            self.in_flight = None
            self._reply(entry["client"], refusal("unavailable", "could not reach the Pi"))
            raise

    def _dispatch_pending(self, now):
        entry, self.pending = self.pending, None
        if entry is None:
            return
        message = self._check(entry)
        if message:
            self._reply(entry["client"], refusal(message[0], message[1]))
            return
        self._dispatch(entry, now)

    def on_result(self, result, now):
        flight = self.in_flight
        if flight is None or result["req"] != flight["req"]:
            return                    # stray or late: no ack, nobody waiting for it
        self.in_flight = None
        self._reply(flight["client"], {"ok": result["ok"], "superseded": False,
                                       "reason": "ok" if result["ok"] else "failed",
                                       "message": result["message"]})
        self._unpublished_ack = {"page": flight["page"], "seq": flight["seq"],
                                 "ok": result["ok"], "message": result["message"]}
        self._dispatch_pending(now)

    def on_sample(self):
        """Called for each sample after a result; only now may the ack become visible."""
        if self._unpublished_ack is not None:
            self.ack, self._unpublished_ack = self._unpublished_ack, None
        return self.ack

    def expire(self, now):
        flight = self.in_flight
        if flight is not None and now >= flight["deadline"]:
            self.in_flight = None
            self._reply(flight["client"], refusal("timeout", "the Pi did not answer in time"))
            self._dispatch_pending(now)

    def fail_all(self, message):
        for entry in (self.in_flight, self.pending):
            if entry is not None:
                self._reply(entry["client"], refusal("unavailable", message))
        self.in_flight = self.pending = None

    def state(self):
        key = lambda entry: None if entry is None else [entry["page"], entry["seq"]]
        return {"in_flight": key(self.in_flight), "pending": key(self.pending)}
