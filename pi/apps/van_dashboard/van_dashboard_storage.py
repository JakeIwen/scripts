"""Requested storage and torrent policy controller.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = ["PolicyCommandError", "StoragePolicyManager"]

if __package__:
    from .van_dashboard_common import (
        POLICYCTL,
        POLICYCTL_TIMEOUT,
        json,
        run_command,
        subprocess,
    )
else:
    from van_dashboard_common import (
        POLICYCTL,
        POLICYCTL_TIMEOUT,
        json,
        run_command,
        subprocess,
    )


class PolicyCommandError(RuntimeError):
    pass


class StoragePolicyManager:
    """Strict policyctl boundary for requested and observed storage state."""

    TARGETS = {
        "disks_enabled": "disks",
        "torrents_enabled": "torrents",
        "allow_starlink_torrents": "starlink-torrents",
    }
    STATUS_FIELDS = {"version", *TARGETS, "runtime"}
    RUNTIME_FIELDS = {
        "disks_mounted",
        "mounted_disk_labels",
        "qbittorrent_running",
    }

    def __init__(self, command=run_command, timeout=POLICYCTL_TIMEOUT):
        self.command = command
        self.timeout = timeout

    @classmethod
    def parse_status(cls, output):
        try:
            status = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PolicyCommandError(f"policyctl returned invalid JSON: {exc}") from exc
        if not isinstance(status, dict) or set(status) != cls.STATUS_FIELDS:
            raise PolicyCommandError("policyctl returned an unexpected status schema")
        if type(status["version"]) is not int or status["version"] != 1:
            raise PolicyCommandError("policyctl returned an unsupported policy version")
        for field in cls.TARGETS:
            if type(status[field]) is not bool:
                raise PolicyCommandError(f"policyctl field {field} was not boolean")
        runtime = status["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != cls.RUNTIME_FIELDS:
            raise PolicyCommandError("policyctl returned an unexpected runtime schema")
        for field in ("disks_mounted", "qbittorrent_running"):
            if type(runtime[field]) is not bool:
                raise PolicyCommandError(
                    f"policyctl runtime field {field} was not boolean"
                )
        labels = runtime["mounted_disk_labels"]
        if (
            not isinstance(labels, list)
            or any(not isinstance(label, str) or not label for label in labels)
            or len(labels) != len(set(labels))
        ):
            raise PolicyCommandError(
                "policyctl runtime mounted_disk_labels was invalid"
            )
        if runtime["disks_mounted"] is not bool(labels):
            raise PolicyCommandError("policyctl returned inconsistent disk runtime state")
        return status

    def _run(self, args, expect_json):
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise PolicyCommandError(
                f"policyctl timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise PolicyCommandError(f"could not start policyctl: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "policyctl failed").strip()
            raise PolicyCommandError(detail[-300:])
        return self.parse_status(result.stdout) if expect_json else None

    def status(self):
        return self._run([POLICYCTL, "--json", "status"], expect_json=True)

    def update(self, field, enabled):
        target = self.TARGETS.get(field)
        if target is None:
            raise ValueError("unknown policy field")
        if type(enabled) is not bool:
            raise ValueError("policy value must be boolean")
        requested = self._run(
            [POLICYCTL, "--json", target, "on" if enabled else "off"],
            expect_json=True,
        )
        if requested[field] is not enabled:
            raise PolicyCommandError(f"policyctl did not confirm {field}")
        return requested

    def reconcile(self):
        self._run([POLICYCTL, "reconcile"], expect_json=False)
