"""Price-watch and system-monitor integrations.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "PriceCheckCommandError",
    "PriceCheckController",
    "SystemMonitorClient",
    "SystemMonitorCommandError",
]

if __package__:
    from .van_dashboard_common import (
        PRICE_CHECK_DB,
        PRICE_CHECK_TIMEOUT,
        PRICE_CHECK_TOOL,
        SYSTEM_MONITOR_DB,
        SYSTEM_MONITOR_TIMEOUT,
        SYSTEM_MONITOR_TOOL,
        json,
        run_command,
        subprocess,
        sys,
    )
else:
    from van_dashboard_common import (
        PRICE_CHECK_DB,
        PRICE_CHECK_TIMEOUT,
        PRICE_CHECK_TOOL,
        SYSTEM_MONITOR_DB,
        SYSTEM_MONITOR_TIMEOUT,
        SYSTEM_MONITOR_TOOL,
        json,
        run_command,
        subprocess,
        sys,
    )


class PriceCheckCommandError(RuntimeError):
    pass


class PriceCheckController:
    """Use the price-check CLI as the sole database and validation boundary."""

    def __init__(
        self,
        tool=PRICE_CHECK_TOOL,
        database=PRICE_CHECK_DB,
        command=run_command,
        timeout=PRICE_CHECK_TIMEOUT,
    ):
        self.tool = tool
        self.database = database
        self.command = command
        self.timeout = timeout

    def _run(self, *arguments, timeout=None):
        argv = [
            sys.executable,
            self.tool,
            "--db",
            self.database,
            "--json",
            *[str(argument) for argument in arguments],
        ]
        try:
            result = self.command(argv, timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as error:
            raise PriceCheckCommandError("price check timed out") from error
        except OSError as error:
            raise PriceCheckCommandError(f"could not start price checker: {error}") from error
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            detail = (result.stderr or result.stdout or "no output").strip()[-500:]
            raise PriceCheckCommandError(
                f"price checker returned invalid output: {detail}"
            ) from error
        if result.returncode or not isinstance(payload, dict) or payload.get("ok") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise PriceCheckCommandError(message or "price checker failed")
        return payload

    def status(self):
        return self._run("list", timeout=20)

    def add(self, parser, threshold, url, title=""):
        return self._run("add", parser, threshold, url, title, timeout=20)

    def edit(self, item_id, parser, threshold, url, title=""):
        return self._run(
            "edit", item_id, parser, threshold, url, title, timeout=20
        )

    def mute(self, item_id, days):
        return self._run("mute", item_id, days, timeout=20)

    def schedule(self):
        return self._run("schedule", timeout=25)

    def parse_schedule(self, expression):
        return self._run("schedule-parse", expression, timeout=25)

    def set_schedule(self, expression):
        return self._run("schedule-set", expression, timeout=30)

    def remove(self, item_id):
        return self._run("remove", item_id, timeout=20)

    def check(self, target="all"):
        return self._run("check", target)

    def add_search(self, parser, url, title=""):
        return self._run("search-add", parser, url, title, timeout=20)

    def remove_search(self, search_id):
        return self._run("search-remove", search_id, timeout=20)

    def dismiss_search_result(self, search_id, item_id):
        return self._run("search-dismiss", search_id, item_id, timeout=20)

    def check_search(self, target="all"):
        return self._run("search-check", target)


class SystemMonitorCommandError(RuntimeError):
    pass


class SystemMonitorClient:
    """Read the passive system monitor through bounded, fixed CLI commands."""

    def __init__(
        self,
        tool=SYSTEM_MONITOR_TOOL,
        database=SYSTEM_MONITOR_DB,
        command=run_command,
        timeout=SYSTEM_MONITOR_TIMEOUT,
    ):
        self.tool = tool
        self.database = database
        self.command = command
        self.timeout = timeout

    def _run_json(self, command_args):
        args = [
            sys.executable,
            self.tool,
            "--database",
            self.database,
            *command_args,
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            raise SystemMonitorCommandError(
                f"system monitor command timed out after {self.timeout:g} seconds"
            ) from error
        except OSError as error:
            raise SystemMonitorCommandError(str(error)) from error
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            detail = (result.stderr or result.stdout or "no output").strip()[-500:]
            raise SystemMonitorCommandError(
                f"system monitor returned invalid output: {detail}"
            ) from error
        if result.returncode or not isinstance(payload, dict) or payload.get("ok") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            detail = (result.stderr or "").strip()[-500:]
            raise SystemMonitorCommandError(message or detail or "system monitor command failed")
        return payload

    def report(self, hours=6):
        return self._run_json(
            [
                "report",
                "--hours",
                str(int(hours)),
                "--limit",
                "100",
                "--json",
            ]
        )

    def crash_analysis(self):
        return self._run_json(["crash-report", "--save", "--json"])

    def crash_history(self, limit=20):
        return self._run_json(
            [
                "crash-history",
                "--limit",
                str(max(1, min(int(limit), 100))),
                "--full",
                "--json",
            ]
        )
