"""Tuya switch and Home Assistant lighting controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = ["LightingCommandError", "LightingController", "TuyaSwitchManager"]

if __package__:
    from .van_dashboard_common import (
        LIGHT_COMMAND_TIMEOUT,
        LIGHT_GROUPS,
        LIGHT_HUE_MODES,
        LIGHT_POWER_SWITCHES,
        TUYA_LIGHT,
        TUYA_POLL_INTERVAL,
        TUYA_STATUS,
        TUYA_TOGGLE,
        json,
        math,
        re,
        run_command,
        subprocess,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        LIGHT_COMMAND_TIMEOUT,
        LIGHT_GROUPS,
        LIGHT_HUE_MODES,
        LIGHT_POWER_SWITCHES,
        TUYA_LIGHT,
        TUYA_POLL_INTERVAL,
        TUYA_STATUS,
        TUYA_TOGGLE,
        json,
        math,
        re,
        run_command,
        subprocess,
        threading,
        time,
    )


class TuyaSwitchManager:
    """Cache and safely toggle one Home Assistant/Tuya switch."""

    def __init__(
        self,
        entity,
        command=run_command,
        interval=TUYA_POLL_INTERVAL,
        wall_clock=time.time,
    ):
        self.entity = entity
        self.command = command
        self.interval = interval
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = "unknown"
        self.checked_at = None
        self.last_error = None
        self.refreshing = False
        self.changing = False

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(
                target=self._loop, name=f"tuya-{self.entity}", daemon=True
            )
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def snapshot(self):
        with self.lock:
            return {
                "entity": self.entity,
                "state": self.state,
                "available": self.state in ("on", "off"),
                "checked_at": self.checked_at,
                "last_error": self.last_error,
                "refreshing": self.refreshing,
                "changing": self.changing,
            }

    def refresh(self):
        with self.operation_lock:
            return self._refresh()

    def toggle(self):
        with self.operation_lock:
            with self.lock:
                current = self.state
                if current not in ("on", "off"):
                    raise ValueError(f"{self.entity} status is unavailable")
                desired = "off" if current == "on" else "on"
                self.state = "unknown"
                self.changing = True
                self.last_error = None
            try:
                result = self.command([TUYA_TOGGLE, self.entity, desired], timeout=20)
            except (OSError, subprocess.TimeoutExpired) as exc:
                message = f"could not turn {self.entity} {desired}: {exc}"
                self._mark_unknown(message, changing=False)
                raise RuntimeError(message) from exc
            if result.returncode:
                detail = (result.stderr or result.stdout or "command failed").strip()
                message = f"could not turn {self.entity} {desired}: {detail[-300:]}"
                self._mark_unknown(message, changing=False)
                raise RuntimeError(message)

            # Read the authoritative Tuya/HA state after the service call. If
            # that verification fails, the UI returns to neutral grey rather
            # than presenting the requested state as confirmed.
            status = self._refresh()
            if not status["available"]:
                raise RuntimeError(f"{self.entity} changed but status verification failed")
            return status

    def _refresh(self):
        with self.lock:
            self.refreshing = True
        try:
            result = self.command([TUYA_STATUS, self.entity], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._mark_unknown(f"could not read {self.entity}: {exc}", changing=False)
        else:
            state = result.stdout.strip().lower()
            if result.returncode == 0 and state in ("on", "off"):
                with self.lock:
                    self.state = state
                    self.checked_at = int(self.wall_clock())
                    self.last_error = None
                    self.refreshing = False
                    self.changing = False
            else:
                detail = (result.stderr or result.stdout or "status unavailable").strip()
                self._mark_unknown(f"could not read {self.entity}: {detail[-300:]}", changing=False)
        return self.snapshot()

    def _mark_unknown(self, message, changing):
        with self.lock:
            self.state = "unknown"
            self.checked_at = int(self.wall_clock())
            self.last_error = message
            self.refreshing = False
            self.changing = changing

    def _loop(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(max(1.0, self.interval))


class LightingCommandError(RuntimeError):
    pass


class LightingController:
    """Strict Home Assistant boundary for the dashboard's configured lights."""

    VALID_STATES = {"on", "off", "unavailable", "unknown"}

    def __init__(self, command=run_command, timeout=LIGHT_COMMAND_TIMEOUT):
        self.command = command
        self.timeout = timeout
        self.operation_lock = threading.Lock()
        ordered_entities = tuple(
            entity
            for _group_id, _group_label, lights in LIGHT_GROUPS
            for entity, _label in lights
        )
        self.ordered_entities = ordered_entities
        self.entities = set(ordered_entities)
        self.switch_entities = {
            entity for entity, _label in LIGHT_POWER_SWITCHES.values()
        }
        self.targets = {"all": ordered_entities}
        self.targets.update(
            {
                f"group:{group_id}": tuple(entity for entity, _label in lights)
                for group_id, _group_label, lights in LIGHT_GROUPS
            }
        )
        self.targets.update({entity: (entity,) for entity in self.entities})
        self.targets.update({entity: (entity,) for entity in self.switch_entities})

    @classmethod
    def parse_status(cls, output):
        try:
            values = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LightingCommandError(f"Home Assistant returned invalid JSON: {exc}") from exc
        if not isinstance(values, list):
            raise LightingCommandError("Home Assistant returned an unexpected light schema")
        parsed = {}
        for item in values:
            base_fields = {"entity_id", "state", "brightness"}
            color_fields = {
                "color_mode",
                "supported_color_modes",
                "hs_color",
                "color_temp_kelvin",
                "min_color_temp_kelvin",
                "max_color_temp_kelvin",
            }
            if not isinstance(item, dict) or set(item) not in (
                base_fields,
                base_fields | color_fields,
            ):
                raise LightingCommandError("Home Assistant returned an unexpected light schema")
            entity = item["entity_id"]
            state = item["state"]
            brightness = item["brightness"]
            if not isinstance(entity, str) or not (
                re.fullmatch(r"light\.[a-z0-9_]+", entity)
                or entity in {
                    switch_entity
                    for switch_entity, _label in LIGHT_POWER_SWITCHES.values()
                }
            ):
                raise LightingCommandError("Home Assistant returned an invalid lighting entity")
            if state not in cls.VALID_STATES:
                state = "unknown"
            if brightness is not None and (
                type(brightness) is not int or not 0 <= brightness <= 255
            ):
                raise LightingCommandError(
                    f"Home Assistant returned invalid brightness for {entity}"
                )
            color_mode = item.get("color_mode")
            supported_modes = item.get("supported_color_modes", [])
            hs_color = item.get("hs_color")
            color_temp = item.get("color_temp_kelvin")
            min_color_temp = item.get("min_color_temp_kelvin")
            max_color_temp = item.get("max_color_temp_kelvin")
            if color_mode is not None and (
                not isinstance(color_mode, str)
                or not re.fullmatch(r"[a-z0-9_]{1,32}", color_mode)
            ):
                raise LightingCommandError(
                    f"Home Assistant returned invalid color mode for {entity}"
                )
            if (
                not isinstance(supported_modes, list)
                or len(supported_modes) > 16
                or any(
                    not isinstance(mode, str)
                    or not re.fullmatch(r"[a-z0-9_]{1,32}", mode)
                    for mode in supported_modes
                )
                or len(supported_modes) != len(set(supported_modes))
            ):
                raise LightingCommandError(
                    f"Home Assistant returned invalid supported color modes for {entity}"
                )
            if hs_color is not None and (
                not isinstance(hs_color, list)
                or len(hs_color) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in hs_color
                )
                or not 0 <= hs_color[0] <= 360
                or not 0 <= hs_color[1] <= 100
            ):
                raise LightingCommandError(
                    f"Home Assistant returned invalid hue for {entity}"
                )
            for name, value in (
                ("color temperature", color_temp),
                ("minimum color temperature", min_color_temp),
                ("maximum color temperature", max_color_temp),
            ):
                if value is not None and (
                    type(value) is not int or not 1000 <= value <= 10000
                ):
                    raise LightingCommandError(
                        f"Home Assistant returned invalid {name} for {entity}"
                    )
            if (
                min_color_temp is not None
                and max_color_temp is not None
                and min_color_temp > max_color_temp
            ):
                raise LightingCommandError(
                    f"Home Assistant returned an invalid color temperature range for {entity}"
                )
            if entity in parsed:
                raise LightingCommandError(f"Home Assistant returned duplicate {entity}")
            parsed[entity] = {
                "state": state,
                "brightness": brightness,
                "color_mode": color_mode,
                "supported_color_modes": supported_modes,
                "hs_color": hs_color,
                "color_temp_kelvin": color_temp,
                "min_color_temp_kelvin": min_color_temp,
                "max_color_temp_kelvin": max_color_temp,
            }
        return parsed

    @staticmethod
    def aggregate(lights):
        states = [light["state"] for light in lights]
        if states and all(state == "on" for state in states):
            return "on"
        if states and all(state == "off" for state in states):
            return "off"
        if any(state in ("on", "off") for state in states):
            return "mixed"
        return "unknown"

    def _run(self, args, expect_status=False):
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise LightingCommandError(
                f"lighting command timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise LightingCommandError(f"could not start lighting command: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "lighting command failed").strip()
            raise LightingCommandError(detail[-300:])
        return self.parse_status(result.stdout) if expect_status else None

    def status(self):
        try:
            observed = self._run([TUYA_LIGHT, "list"], expect_status=True)
        except LightingCommandError as exc:
            # A dashboard-only deployment used to omit tuya_light.sh. Keep
            # status useful with that older helper, while the deployment tool
            # now installs and health-checks both files as one unit.
            if "usage: tuya_light.sh" not in str(exc):
                raise
            observed = self._legacy_status()
        groups = []
        all_lights = []
        for group_id, group_label, configured in LIGHT_GROUPS:
            lights = []
            for entity, label in configured:
                value = observed.get(entity, {"state": "unknown", "brightness": None})
                brightness = value["brightness"]
                supported_modes = set(value.get("supported_color_modes") or ())
                supports_hue = bool(supported_modes & LIGHT_HUE_MODES)
                supports_color_temp = "color_temp" in supported_modes
                min_color_temp = value.get("min_color_temp_kelvin")
                max_color_temp = value.get("max_color_temp_kelvin")
                if supports_color_temp:
                    min_color_temp = min_color_temp or 2000
                    max_color_temp = max_color_temp or 7000
                light = {
                    "entity_id": entity,
                    "label": label,
                    "state": value["state"],
                    "available": value["state"] in ("on", "off"),
                    "brightness": (
                        round(brightness * 100 / 255) if brightness is not None else None
                    ),
                    "color_mode": value.get("color_mode"),
                    "supports_hue": supports_hue,
                    "hue": (
                        round(float(value["hs_color"][0]), 1)
                        if supports_hue and value.get("hs_color") is not None
                        else None
                    ),
                    "supports_color_temperature": supports_color_temp,
                    "color_temp_kelvin": value.get("color_temp_kelvin"),
                    "min_color_temp_kelvin": min_color_temp,
                    "max_color_temp_kelvin": max_color_temp,
                }
                lights.append(light)
                all_lights.append(light)
            switch_config = LIGHT_POWER_SWITCHES.get(group_id)
            power_switch = None
            if switch_config is not None:
                switch_entity, switch_label = switch_config
                switch_value = observed.get(
                    switch_entity, {"state": "unknown", "brightness": None}
                )
                power_switch = {
                    "entity_id": switch_entity,
                    "label": switch_label,
                    "state": switch_value["state"],
                    "available": switch_value["state"] in ("on", "off"),
                }
            groups.append(
                {
                    "id": group_id,
                    "label": group_label,
                    "state": self.aggregate(lights),
                    "lights": lights,
                    "power_switch": power_switch,
                }
            )
        return {
            "state": self.aggregate(all_lights),
            "on_count": sum(light["state"] == "on" for light in all_lights),
            "available_count": sum(light["available"] for light in all_lights),
            "total_count": len(all_lights),
            "groups": groups,
        }

    def _legacy_status(self):
        observed = {}
        successful = 0
        for entity in self.ordered_entities:
            try:
                result = self.command(
                    [TUYA_LIGHT, "status", entity], timeout=self.timeout
                )
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is None or result.returncode:
                observed[entity] = {"state": "unknown", "brightness": None}
                continue
            try:
                item = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError):
                item = None
            state = item.get("state") if isinstance(item, dict) else None
            brightness = item.get("brightness") if isinstance(item, dict) else None
            if state not in self.VALID_STATES or (
                brightness is not None
                and (type(brightness) is not int or not 0 <= brightness <= 255)
            ):
                observed[entity] = {"state": "unknown", "brightness": None}
                continue
            observed[entity] = {"state": state, "brightness": brightness}
            successful += 1
        if not successful:
            raise LightingCommandError(
                "lighting helper is outdated and individual status queries failed"
            )
        return observed

    def set_power(self, target, enabled):
        entities = self.targets.get(target)
        if entities is None:
            raise ValueError("unknown lighting target")
        if type(enabled) is not bool:
            raise ValueError("lighting power value must be boolean")
        with self.operation_lock:
            for entity in entities:
                self._run(
                    [TUYA_TOGGLE, entity, "on" if enabled else "off"],
                    expect_status=False,
                )
            return self.status()

    def set_brightness(self, entity, brightness):
        if entity not in self.entities:
            raise ValueError("unknown light entity")
        if type(brightness) is not int or not 1 <= brightness <= 100:
            raise ValueError("brightness must be from 1 to 100")
        raw_brightness = max(1, round(brightness * 255 / 100))
        with self.operation_lock:
            self._run(
                [TUYA_LIGHT, "set", entity, str(raw_brightness)],
                expect_status=False,
            )
            return self.status()

    def set_hue(self, entity, hue):
        if entity not in self.entities:
            raise ValueError("unknown light entity")
        if type(hue) is not int or not 0 <= hue <= 360:
            raise ValueError("hue must be from 0 to 360")
        with self.operation_lock:
            self._run(
                [TUYA_LIGHT, "hue", entity, str(hue)],
                expect_status=False,
            )
            return self.status()

    def set_color_temperature(self, entity, kelvin):
        if entity not in self.entities:
            raise ValueError("unknown light entity")
        if type(kelvin) is not int or not 2000 <= kelvin <= 7000:
            raise ValueError("color temperature must be from 2000 to 7000 kelvin")
        with self.operation_lock:
            self._run(
                [TUYA_LIGHT, "temperature", entity, str(kelvin)],
                expect_status=False,
            )
            return self.status()
