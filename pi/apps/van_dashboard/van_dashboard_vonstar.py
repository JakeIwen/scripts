"""Intent-only dashboard client for the private Vonstar Unix API."""

import http.client
import json
import math
import os
import secrets
import socket
import stat

__all__ = [
    "VONSTAR_ACTIONS",
    "VonstarClient",
    "VonstarClientError",
]

VONSTAR_SOCKET = os.environ.get(
    "VAN_DASHBOARD_VONSTAR_SOCKET",
    "/run/vonstar/api.sock",
)
VONSTAR_STATUS_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_VONSTAR_STATUS_TIMEOUT", "2")
)
VONSTAR_ACTION_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_VONSTAR_ACTION_TIMEOUT", "30")
)
VONSTAR_MAX_RESPONSE_BYTES = 64 * 1024
VONSTAR_ACTIONS = {
    "lock_all": {"label": "Lock All", "validation": "mapped_capture"},
    "unlock_front": {"label": "Unlock Front", "validation": "live_verified"},
    "unlock_cargo": {"label": "Unlock Cargo", "validation": "mapped_capture"},
}


class VonstarClientError(RuntimeError):
    def __init__(self, message, http_status=503):
        super().__init__(message)
        self.http_status = http_status


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class VonstarClient:
    """Expose only Vonstar's fixed high-level actions and sanitized results."""

    def __init__(
        self,
        socket_path=VONSTAR_SOCKET,
        status_timeout=VONSTAR_STATUS_TIMEOUT,
        action_timeout=VONSTAR_ACTION_TIMEOUT,
        connection_factory=UnixConnection,
        lstat=os.lstat,
    ):
        self.socket_path = socket_path
        self.status_timeout = status_timeout
        self.action_timeout = action_timeout
        self.connection_factory = connection_factory
        self.lstat = lstat

    def snapshot(self):
        try:
            response = self._request(
                "GET",
                "/v1/status",
                timeout=self.status_timeout,
            )
            return self._sanitize_status(response.get("vonstar"))
        except VonstarClientError as exc:
            return self._unavailable(str(exc))

    def perform(self, action):
        if action not in VONSTAR_ACTIONS:
            raise ValueError("unknown Vonstar action")
        request_id = "dash-" + secrets.token_hex(16)
        response = self._request(
            "POST",
            "/v1/actions/" + action,
            payload={"request_id": request_id},
            timeout=self.action_timeout,
        )
        return self._sanitize_result(response, expected_action=action)

    def read_access_state(self):
        request_id = "dash-state-" + secrets.token_hex(16)
        response = self._request(
            "POST",
            "/v1/access-state",
            payload={"request_id": request_id},
            timeout=self.action_timeout,
        )
        return self._sanitize_access_result(response, require_success=True)

    def _request(self, method, path, payload=None, timeout=None):
        self._require_socket()
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = self.connection_factory(
            self.socket_path,
            self.status_timeout if timeout is None else timeout,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(VONSTAR_MAX_RESPONSE_BYTES + 1)
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise VonstarClientError("Vonstar service is unavailable") from exc
        finally:
            connection.close()
        if len(raw) > VONSTAR_MAX_RESPONSE_BYTES:
            raise VonstarClientError("Vonstar response was too large")
        try:
            decoded = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VonstarClientError("Vonstar returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise VonstarClientError("Vonstar returned an invalid response")
        if response.status >= 400 or decoded.get("ok") is False:
            message = decoded.get("message") or decoded.get("error")
            if not isinstance(message, str) or not message or len(message) > 512:
                message = f"Vonstar action failed ({response.status})"
            raise VonstarClientError(message, self._proxy_status(response.status))
        if decoded.get("ok") is not True:
            raise VonstarClientError("Vonstar returned an invalid response")
        return decoded

    def _require_socket(self):
        try:
            metadata = self.lstat(self.socket_path)
        except OSError as exc:
            raise VonstarClientError("Vonstar service is unavailable") from exc
        if not stat.S_ISSOCK(metadata.st_mode):
            raise VonstarClientError("Vonstar service socket is invalid")

    def _sanitize_status(self, status):
        if not isinstance(status, dict):
            raise VonstarClientError("Vonstar returned an invalid status")
        schema_version = status.get("schema_version")
        if (
            status.get("service") != "vonstar"
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            raise VonstarClientError("Vonstar returned an unexpected status identity")
        mode = status.get("mode")
        available = status.get("available")
        busy = status.get("busy")
        if mode not in ("execute", "plan_only"):
            raise VonstarClientError("Vonstar returned an invalid mode")
        if not isinstance(available, bool) or not isinstance(busy, bool):
            raise VonstarClientError("Vonstar returned invalid availability")
        if available != (mode == "execute"):
            raise VonstarClientError("Vonstar returned inconsistent availability")
        actions = status.get("actions")
        if not isinstance(actions, dict) or set(actions) != set(VONSTAR_ACTIONS):
            raise VonstarClientError("Vonstar returned an unexpected action catalog")
        cooldown = status.get("cooldown_seconds")
        if (
            isinstance(cooldown, bool)
            or not isinstance(cooldown, (int, float))
            or not math.isfinite(cooldown)
            or not 0 <= cooldown <= 60
        ):
            raise VonstarClientError("Vonstar returned an invalid cooldown")
        last_result = status.get("last_result")
        if last_result is not None:
            if isinstance(last_result, dict) and last_result.get("operation") == "access_state":
                last_result = self._sanitize_access_result(last_result)
            else:
                last_result = self._sanitize_result(last_result)
        return {
            "service": "vonstar",
            "available": available,
            "mode": mode,
            "busy": busy,
            "actions": {name: dict(details) for name, details in VONSTAR_ACTIONS.items()},
            "cooldown_seconds": cooldown,
            "last_result": last_result,
            "error": None,
        }

    def _sanitize_result(self, result, expected_action=None):
        if not isinstance(result, dict):
            raise VonstarClientError("Vonstar returned an invalid action result")
        action = result.get("action")
        ok = result.get("ok")
        if action not in VONSTAR_ACTIONS or (
            expected_action is not None and action != expected_action
        ):
            raise VonstarClientError("Vonstar returned an unexpected action result")
        if not isinstance(ok, bool):
            raise VonstarClientError("Vonstar returned an invalid action result")
        sanitized = {
            "action": action,
            "label": VONSTAR_ACTIONS[action]["label"],
            "ok": ok,
        }
        for field in ("started_at", "completed_at", "error"):
            value = result.get(field)
            if value is not None:
                if not isinstance(value, str) or len(value) > 512:
                    raise VonstarClientError("Vonstar returned an invalid action result")
                sanitized[field] = value
        return sanitized

    def _sanitize_access_result(self, result, require_success=False):
        if not isinstance(result, dict) or result.get("operation") != "access_state":
            raise VonstarClientError("Vonstar returned an invalid access-state result")
        ok = result.get("ok")
        if not isinstance(ok, bool):
            raise VonstarClientError("Vonstar returned an invalid access-state result")
        if require_success and not ok:
            raise VonstarClientError("Vonstar returned a failed access-state result")
        sanitized = {
            "operation": "access_state",
            "ok": ok,
        }
        if ok:
            sanitized["access_state"] = self._sanitize_access_state(
                result.get("access_state")
            )
        else:
            error = result.get("error")
            if not isinstance(error, str) or not error or len(error) > 512:
                raise VonstarClientError("Vonstar returned an invalid access-state result")
            sanitized["error"] = error
        for field in ("started_at", "completed_at"):
            value = result.get(field)
            if value is not None:
                if not isinstance(value, str) or len(value) > 512:
                    raise VonstarClientError("Vonstar returned an invalid access-state result")
                sanitized[field] = value
        return sanitized

    def _sanitize_access_state(self, access_state):
        if not isinstance(access_state, dict):
            raise VonstarClientError("Vonstar returned an invalid access-state snapshot")
        observed_at = access_state.get("observed_at")
        complete = access_state.get("complete")
        if not isinstance(observed_at, str) or not observed_at or len(observed_at) > 128:
            raise VonstarClientError("Vonstar returned an invalid access-state timestamp")
        if not isinstance(complete, bool):
            raise VonstarClientError("Vonstar returned invalid access-state coverage")
        limitations = access_state.get("limitations")
        if not isinstance(limitations, list) or len(limitations) > 32:
            raise VonstarClientError("Vonstar returned invalid access-state limitations")
        if any(
            not isinstance(item, str) or len(item) > 512
            for item in limitations
        ):
            raise VonstarClientError("Vonstar returned invalid access-state limitations")
        wake = access_state.get("wake")
        if not isinstance(wake, dict):
            raise VonstarClientError("Vonstar returned invalid access-state wake metadata")
        wake_count = wake.get("count")
        restored = wake.get("restored_passive_after_sampling")
        if isinstance(wake_count, bool) or not isinstance(wake_count, int) or wake_count not in (0, 1):
            raise VonstarClientError("Vonstar returned invalid access-state wake metadata")
        if not isinstance(restored, bool):
            raise VonstarClientError("Vonstar returned invalid access-state wake metadata")
        return {
            "observed_at": observed_at,
            "complete": complete,
            "lock_domains": self._sanitize_lock_domains(access_state.get("lock_domains")),
            "doors": self._sanitize_doors(access_state.get("doors")),
            "wake": {
                "count": wake_count,
                "restored_passive_after_sampling": restored,
            },
            "observations": self._sanitize_json_value(
                access_state.get("observations"),
                depth=0,
            ),
            "limitations": list(limitations),
        }

    @staticmethod
    def _sanitize_lock_domains(lock_domains):
        if not isinstance(lock_domains, dict):
            raise VonstarClientError("Vonstar returned invalid lock-domain status")
        state = lock_domains.get("state")
        quality = lock_domains.get("quality")
        if not isinstance(state, str) or len(state) > 64:
            raise VonstarClientError("Vonstar returned invalid lock-domain status")
        if not isinstance(quality, str) or len(quality) > 64:
            raise VonstarClientError("Vonstar returned invalid lock-domain quality")
        result = {"state": state, "quality": quality}
        for field in ("front_locked", "cargo_locked"):
            value = lock_domains.get(field)
            if value is not None and not isinstance(value, bool):
                raise VonstarClientError("Vonstar returned invalid lock-domain status")
            result[field] = value
        return result

    @staticmethod
    def _sanitize_doors(doors):
        if not isinstance(doors, dict):
            raise VonstarClientError("Vonstar returned invalid door status")
        result = {}
        for name in ("driver", "passenger", "sliding", "rear"):
            door = doors.get(name)
            if not isinstance(door, dict):
                raise VonstarClientError("Vonstar returned invalid door status")
            item = {}
            for field in ("locked", "ajar", "reported_closed", "physical_state_observable"):
                value = door.get(field)
                if value is not None and not isinstance(value, bool):
                    raise VonstarClientError("Vonstar returned invalid door status")
                item[field] = value
            for field in ("lock_quality", "ajar_quality"):
                value = door.get(field)
                if not isinstance(value, str) or len(value) > 128:
                    raise VonstarClientError("Vonstar returned invalid door status")
                item[field] = value
            result[name] = item
        return result

    @classmethod
    def _sanitize_json_value(cls, value, depth):
        if depth > 6:
            raise VonstarClientError("Vonstar observations were too deeply nested")
        if value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise VonstarClientError("Vonstar observations contained an invalid number")
            return value
        if isinstance(value, str):
            if len(value) > 4096:
                raise VonstarClientError("Vonstar observations contained oversized text")
            return value
        if isinstance(value, list):
            if len(value) > 256:
                raise VonstarClientError("Vonstar observations contained an oversized list")
            return [cls._sanitize_json_value(item, depth + 1) for item in value]
        if isinstance(value, dict):
            if len(value) > 256:
                raise VonstarClientError("Vonstar observations contained an oversized object")
            result = {}
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise VonstarClientError("Vonstar observations contained an invalid key")
                result[key] = cls._sanitize_json_value(item, depth + 1)
            return result
        raise VonstarClientError("Vonstar observations contained an invalid value")

    @staticmethod
    def _proxy_status(status):
        if status == 400:
            return 400
        if status in (409, 429):
            return 409
        return 503

    @staticmethod
    def _unavailable(error):
        return {
            "service": "vonstar",
            "available": False,
            "mode": "unavailable",
            "busy": False,
            "actions": {name: dict(details) for name, details in VONSTAR_ACTIONS.items()},
            "cooldown_seconds": None,
            "last_result": None,
            "error": error,
        }
