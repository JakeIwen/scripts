"""USB inventory, hub discovery, and guarded port-control controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = ["UsbDeviceMonitor", "UsbPortController", "parse_uhubctl_status"]

if __package__:
    from .van_dashboard_common import (
        SUDO,
        TEE,
        UHUBCTL,
        USB2_RECOVERY_TIMEOUT,
        USB2_RECOVERY_TOOL,
        USB_PORT_SNAPSHOT_TTL,
        USB_PORT_TIMEOUT,
        USB_WATCH_TIMEOUT,
        USB_WATCH_TOOL,
        copy,
        glob,
        json,
        os,
        re,
        read_text_file,
        run_command,
        subprocess,
        sys,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        SUDO,
        TEE,
        UHUBCTL,
        USB2_RECOVERY_TIMEOUT,
        USB2_RECOVERY_TOOL,
        USB_PORT_SNAPSHOT_TTL,
        USB_PORT_TIMEOUT,
        USB_WATCH_TIMEOUT,
        USB_WATCH_TOOL,
        copy,
        glob,
        json,
        os,
        re,
        read_text_file,
        run_command,
        subprocess,
        sys,
        threading,
        time,
    )


class UsbDeviceMonitor:
    """Track live USB devices using usb_watch.py's one-shot collector."""

    DEVICE_FIELDS = {
        "bus",
        "device_id",
        "description",
        "present_count",
        "labels",
        "root_hub",
    }
    INSTANCE_FIELDS = {
        "device_number",
        "location",
        "parent_location",
        "port",
        "labels",
    }

    def __init__(
        self,
        tool=USB_WATCH_TOOL,
        command=run_command,
        timeout=USB_WATCH_TIMEOUT,
        wall_clock=time.time,
    ):
        self.tool = tool
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock
        self.operation_lock = threading.Lock()
        self.lock = threading.Lock()
        self.seen = {}
        self.baseline = True
        self.checked_at = None
        self.last_success_at = None
        self.last_error = None

    @classmethod
    def parse_current(cls, output):
        try:
            payload = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"usb_watch returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "devices"}:
            raise ValueError("usb_watch returned an unexpected schema")
        if payload["version"] not in (1, 2) or type(payload["version"]) is not int:
            raise ValueError("usb_watch returned an unsupported version")
        if not isinstance(payload["devices"], list):
            raise ValueError("usb_watch devices were not a list")
        parsed = {}
        for device in payload["devices"]:
            expected_fields = cls.DEVICE_FIELDS | ({"instances"} if payload["version"] == 2 else set())
            if not isinstance(device, dict) or set(device) != expected_fields:
                raise ValueError("usb_watch returned an invalid device")
            bus = device["bus"]
            device_id = device["device_id"]
            description = device["description"]
            count = device["present_count"]
            labels = device["labels"]
            root_hub = device["root_hub"]
            instances = device.get("instances", [])
            if not isinstance(bus, str) or not re.fullmatch(r"\d{3}", bus):
                raise ValueError("usb_watch returned an invalid bus")
            if not isinstance(device_id, str) or not re.fullmatch(
                r"(?:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}|unknown)", device_id
            ):
                raise ValueError("usb_watch returned an invalid device ID")
            if not isinstance(description, str) or not description or len(description) > 500:
                raise ValueError("usb_watch returned an invalid description")
            if type(count) is not int or count < 1:
                raise ValueError("usb_watch returned an invalid device count")
            if (
                not isinstance(labels, list)
                or any(not isinstance(label, str) or not label for label in labels)
                or len(labels) != len(set(labels))
            ):
                raise ValueError("usb_watch returned invalid filesystem labels")
            if type(root_hub) is not bool:
                raise ValueError("usb_watch returned an invalid root-hub flag")
            if not isinstance(instances, list):
                raise ValueError("usb_watch returned invalid topology instances")
            parsed_instances = []
            for instance in instances:
                if not isinstance(instance, dict) or set(instance) != cls.INSTANCE_FIELDS:
                    raise ValueError("usb_watch returned an invalid topology instance")
                if (
                    type(instance["device_number"]) is not int
                    or instance["device_number"] < 1
                    or not isinstance(instance["location"], str)
                    or not re.fullmatch(r"\d+-\d+(?:\.\d+)*", instance["location"])
                    or not isinstance(instance["parent_location"], str)
                    or not re.fullmatch(r"\d+(?:-\d+(?:\.\d+)*)?", instance["parent_location"])
                    or type(instance["port"]) is not int
                    or instance["port"] < 1
                    or not isinstance(instance["labels"], list)
                    or any(not isinstance(label, str) or not label for label in instance["labels"])
                ):
                    raise ValueError("usb_watch returned an invalid topology instance")
                parsed_instances.append(copy.deepcopy(instance))
            key = (bus, device_id, description)
            if key in parsed:
                raise ValueError("usb_watch returned a duplicate device")
            parsed[key] = {
                "bus": bus,
                "device_id": device_id.lower(),
                "description": description,
                "present_count": count,
                "labels": list(labels),
                "root_hub": root_hub,
                "instances": parsed_instances,
            }
        return parsed

    def refresh(self):
        with self.operation_lock:
            now = int(self.wall_clock())
            try:
                result = self.command(
                    [sys.executable, self.tool, "--json"], timeout=self.timeout
                )
                if result.returncode:
                    detail = (result.stderr or result.stdout or "usb_watch failed").strip()
                    raise RuntimeError(detail[-500:])
                current = self.parse_current(result.stdout)
            except subprocess.TimeoutExpired:
                error = f"usb_watch timed out after {self.timeout:g} seconds"
            except (OSError, RuntimeError, ValueError) as exc:
                error = str(exc)
            else:
                error = None

            with self.lock:
                self.checked_at = now
                if error:
                    self.last_error = error
                    return self._snapshot_unlocked()

                for key, device in current.items():
                    count = device["present_count"]
                    if key not in self.seen:
                        event = None if self.baseline else {"kind": "plugged", "at": now}
                        self.seen[key] = {
                            **device,
                            "max_count": count,
                            "known_instances": copy.deepcopy(device["instances"]),
                            "event": event,
                        }
                        continue
                    seen = self.seen[key]
                    if seen["present_count"] == 0:
                        seen["event"] = {"kind": "replugged", "at": now}
                    seen["present_count"] = count
                    seen["max_count"] = max(seen["max_count"], count)
                    if device["labels"]:
                        seen["labels"] = device["labels"]
                    current_instances = copy.deepcopy(device["instances"])
                    seen["instances"] = current_instances
                    if count >= seen["max_count"]:
                        seen["known_instances"] = current_instances
                    else:
                        remembered = {
                            item["location"]: copy.deepcopy(item)
                            for item in seen.get("known_instances", ())
                        }
                        remembered.update(
                            {item["location"]: item for item in current_instances}
                        )
                        current_locations = {
                            item["location"] for item in current_instances
                        }
                        retained = current_instances + [
                            item
                            for location, item in remembered.items()
                            if location not in current_locations
                        ]
                        seen["known_instances"] = retained[: seen["max_count"]]

                for key, seen in self.seen.items():
                    if key not in current and seen["present_count"] > 0:
                        seen["present_count"] = 0
                        seen["instances"] = []
                        seen["event"] = {"kind": "unplugged", "at": now}

                self.baseline = False
                self.last_success_at = now
                self.last_error = None
                return self._snapshot_unlocked()

    def snapshot(self):
        with self.lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self):
        devices = []
        for seen in sorted(
            self.seen.values(),
            key=lambda item: (
                item["bus"],
                0 if item["root_hub"] else 1,
                item["description"].lower(),
            ),
        ):
            if seen["present_count"] == 0:
                status = "unplugged"
            elif seen["present_count"] < seen["max_count"]:
                status = "partial"
            elif seen["root_hub"]:
                status = "root"
            else:
                status = "present"
            devices.append({**seen, "status": status})

        physical = [device for device in devices if not device["root_hub"]]
        present = [device for device in physical if device["present_count"] > 0]
        labels = sorted(
            {
                label
                for device in present
                for label in device["labels"]
            }
        )
        return {
            "checked_at": self.checked_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "present_device_count": sum(device["present_count"] for device in present),
            "unplugged_device_count": sum(
                device["max_count"] for device in physical if device["present_count"] == 0
            ),
            "storage_labels": labels,
            "devices": devices,
        }


UHUB_HUB_RE = re.compile(r"^Current status for hub (\S+) \[(.+)\]$")
UHUB_PORT_RE = re.compile(r"^\s+Port (\d+):\s+(.+?)(?:\s+\[([0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})\s+(.+)\])?$")
USB_PORT_KEY_RE = re.compile(r"^(\d+(?:-\d+(?:\.\d+)*)?):([1-9]\d*)$")


def parse_uhubctl_status(output):
    """Parse the stable human-readable status emitted by uhubctl 2.x."""
    hubs = {}
    current = None
    for raw_line in str(output or "").splitlines():
        hub_match = UHUB_HUB_RE.match(raw_line.strip())
        if hub_match:
            location, details = hub_match.groups()
            port_match = re.search(r",\s*(\d+) ports?,\s*([^,\]]+)\s*$", details)
            identity = details.split(", USB ", 1)[0]
            parts = identity.split(None, 1)
            current = {
                "location": location,
                "device_id": parts[0].lower() if parts else "unknown",
                "description": parts[1] if len(parts) > 1 else "USB hub",
                "port_count": int(port_match.group(1)) if port_match else 0,
                "switching": port_match.group(2).strip() if port_match else "unknown",
                "ports": {},
            }
            hubs[location] = current
            continue
        port_match = UHUB_PORT_RE.match(raw_line)
        if current is None or not port_match:
            continue
        port, status, device_id, description = port_match.groups()
        current["ports"][int(port)] = {
            "powered": "power" in status.split(),
            "status": status,
            "device_id": device_id.lower() if device_id else None,
            "description": description if description else None,
        }
    return hubs


class UsbPortController:
    """Explicitly discover USB hub ports and run fixed, serialized actions.

    Passive dashboard status must use :meth:`snapshot`; only an explicit control
    request may call :meth:`discover`.  ``command_lock`` keeps every uhubctl
    status/action and the USB 2 recovery helper single-flight across Flask
    request threads.
    """

    ACTIONS = {"off", "on", "cycle"}

    def __init__(
        self,
        device_monitor,
        command=run_command,
        recovery_tool=USB2_RECOVERY_TOOL,
        sys_root="/sys",
        dev_root="/dev",
        mounts_path="/proc/self/mounts",
        timeout=USB_PORT_TIMEOUT,
        snapshot_ttl=USB_PORT_SNAPSHOT_TTL,
        recovery_timeout=USB2_RECOVERY_TIMEOUT,
        wall_clock=time.time,
        sleeper=time.sleep,
    ):
        self.device_monitor = device_monitor
        self.command = command
        self.recovery_tool = recovery_tool
        self.sys_root = sys_root
        self.dev_root = dev_root
        self.mounts_path = mounts_path
        self.timeout = timeout
        self.snapshot_ttl = snapshot_ttl
        self.recovery_timeout = recovery_timeout
        self.wall_clock = wall_clock
        self.sleeper = sleeper
        self.lock = threading.RLock()
        self.command_lock = threading.Lock()
        self.targets = {}
        self.data = {
            "loaded": False,
            "checked_at": None,
            "expires_at": None,
            "last_error": None,
            "hubs": [],
            "operation": {"status": "idle"},
        }

    def _kernel_ports(self):
        ports = {}
        pattern = os.path.join(
            self.sys_root, "bus", "usb", "devices", "*", "*-port*", "disable"
        )
        for path in glob.glob(pattern):
            port_dir = os.path.basename(os.path.dirname(path))
            interface = os.path.basename(os.path.dirname(os.path.dirname(path)))
            match = re.fullmatch(r"(.+)-port(\d+)", port_dir)
            if not match or not interface.endswith(":1.0"):
                continue
            interface_location = interface.removesuffix(":1.0")
            location = (
                interface_location[:-2]
                if interface_location.endswith("-0")
                else interface_location
            )
            if not re.fullmatch(r"\d+(?:-\d+(?:\.\d+)*)?", location):
                continue
            port = int(match.group(2))
            try:
                disabled = read_text_file(path) == "1"
            except OSError:
                continue
            ports[(location, port)] = {
                "disable_path": path,
                "enabled": not disabled,
            }
        return ports

    def _mounted_sources(self):
        sources = set()
        try:
            with open(self.mounts_path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            return sources
        for line in lines:
            fields = line.split()
            if fields and (
                fields[0].startswith("/dev/")
                or fields[0].startswith(self.dev_root.rstrip(os.sep) + os.sep)
            ):
                sources.add(os.path.realpath(fields[0].replace("\\040", " ")))
        return sources

    def _mounted_labels(self, labels):
        sources = self._mounted_sources()
        mounted = []
        for label in labels:
            path = os.path.join(self.dev_root, "disk", "by-label", label)
            if os.path.exists(path) and os.path.realpath(path) in sources:
                mounted.append(label)
        return sorted(mounted)

    @staticmethod
    def _instances(usb_state):
        result = []
        for device in usb_state.get("devices", ()):
            if device.get("present_count", 0) < 1:
                continue
            for instance in device.get("instances", ()):
                result.append(
                    {
                        **instance,
                        "description": device.get("description") or "USB device",
                        "device_id": device.get("device_id"),
                        "root_hub": bool(device.get("root_hub")),
                    }
                )
        return result

    @staticmethod
    def _child_location(location, port):
        return f"{location}-{port}" if location.isdigit() else f"{location}.{port}"

    @staticmethod
    def _companion_route(location):
        """Return the Pi 4 physical-port route for USB 2/USB 3 hub companions."""
        usb2 = re.fullmatch(r"1-1\.(\d+(?:\.\d+)*)", location)
        if usb2:
            return "usb2", usb2.group(1)
        usb3 = re.fullmatch(r"2-(\d+(?:\.\d+)*)", location)
        if usb3:
            return "usb3", usb3.group(1)
        return None

    @classmethod
    def _presentation_hubs(cls, hubs, targets):
        """Merge dual USB 2/USB 3 hub trees into user-facing physical ports.

        A Raspberry Pi 4 inserts its VIA USB 2 hub at ``1-1`` while USB 3 is
        rooted directly at bus 2. Matching routes below those points are the
        two logical sides of the same physical hub. Each root external-hub
        route becomes its own pane. Chained controller chips beneath that root
        are flattened into the same pane in route order.
        """
        companions = {"usb2": {}, "usb3": {}}
        for hub in hubs:
            route = cls._companion_route(hub["location"])
            if route and hub["method"] == "power":
                side, path = route
                companions[side][path] = hub

        paired_routes = set(companions["usb2"]) & set(companions["usb3"])
        paired_routes = {
            route
            for route in paired_routes
            if {
                port["port"] for port in companions["usb2"][route]["ports"]
            }
            == {
                port["port"] for port in companions["usb3"][route]["ports"]
            }
        }
        consumed_locations = {
            companions[side][route]["location"]
            for route in paired_routes
            for side in ("usb2", "usb3")
        }
        roots = sorted(
            (
                route
                for route in paired_routes
                if "." not in route or route.rsplit(".", 1)[0] not in paired_routes
            ),
            key=lambda route: [int(part) for part in route.split(".")],
        )

        presented = []
        for root in roots:
            physical_ports = []

            def append_route(route):
                usb2_hub = companions["usb2"][route]
                usb3_hub = companions["usb3"][route]
                usb2_ports = {port["port"]: port for port in usb2_hub["ports"]}
                usb3_ports = {port["port"]: port for port in usb3_hub["ports"]}
                for topology_port in sorted(usb2_ports):
                    child_route = f"{route}.{topology_port}"
                    if child_route in paired_routes:
                        append_route(child_route)
                        continue
                    halves = [usb2_ports[topology_port], usb3_ports[topology_port]]
                    primary = usb3_ports[topology_port]
                    enabled_values = [
                        port["enabled"] for port in halves if port["enabled"] is not None
                    ]
                    descriptions = sorted(
                        {
                            description
                            for port in halves
                            for description in port["device_descriptions"]
                        }
                    )
                    storage_labels = sorted(
                        {
                            label
                            for port in halves
                            for label in port["storage_labels"]
                        }
                    )
                    mounted_labels = sorted(
                        {
                            label
                            for port in halves
                            for label in port["mounted_labels"]
                        }
                    )
                    physical_port = len(physical_ports) + 1
                    merged = {
                        **primary,
                        "port": physical_port,
                        "enabled": all(enabled_values) if enabled_values else None,
                        "device_descriptions": descriptions,
                        "downstream_device_count": sum(
                            port["downstream_device_count"] for port in halves
                        ),
                        "storage_labels": storage_labels,
                        "mounted_labels": mounted_labels,
                        "topology_locations": [
                            f"{port['location']}:{topology_port}" for port in halves
                        ],
                    }
                    physical_ports.append(merged)

                    # Keep the actual USB 3 location/port for uhubctl. Its
                    # default duality handling also switches the USB 2 side.
                    target = targets[primary["key"]]
                    target.update(
                        {
                            "enabled": merged["enabled"],
                            "device_descriptions": descriptions,
                            "downstream_device_count": merged[
                                "downstream_device_count"
                            ],
                            "storage_labels": storage_labels,
                            "mounted_labels": mounted_labels,
                            "topology_locations": merged["topology_locations"],
                        }
                    )

            append_route(root)
            root_hub = companions["usb3"][root]
            identity = root_hub.get("description") or "Unknown USB hub"
            device_id = root_hub.get("device_id")
            identity_detail = identity
            if device_id:
                identity_detail = f"{identity} · ID {device_id}"
            presented.append(
                {
                    "location": root_hub["location"],
                    "description": "External USB hub",
                    "detail": (
                        f"{identity_detail} · {len(physical_ports)} physical ports"
                        f" · paired USB 2/USB 3 · route {root_hub['location']}"
                    ),
                    "device_id": device_id,
                    "method": "power",
                    "physical": True,
                    "advanced": False,
                    "ports": physical_ports,
                }
            )

        for hub in hubs:
            if hub["location"] in consumed_locations:
                continue
            presented.append(
                {
                    **hub,
                    "physical": False,
                    "advanced": hub["location"] in {"1", "2", "1-1"},
                }
            )
        return presented

    def _discover_unlocked(self, usb_state=None):
        usb_state = usb_state or self.device_monitor.refresh()
        error = None
        smart_hubs = {}
        try:
            result = self.command([SUDO, "-n", UHUBCTL], timeout=self.timeout)
            if result.returncode:
                detail = (result.stderr or result.stdout or "uhubctl failed").strip()
                raise RuntimeError(detail[-500:])
            smart_hubs = parse_uhubctl_status(result.stdout)
        except subprocess.TimeoutExpired:
            error = f"uhubctl status timed out after {self.timeout:g} seconds"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)

        kernel_ports = self._kernel_ports()
        instances = self._instances(usb_state)
        keys = set(kernel_ports)
        for location, hub in smart_hubs.items():
            keys.update((location, port) for port in hub["ports"])
        hubs = {}
        targets = {}
        for location, port in sorted(
            keys,
            key=lambda item: (
                [int(value) for value in re.split(r"[-.]", item[0])],
                item[1],
            ),
        ):
            smart_hub = smart_hubs.get(location)
            smart_port = smart_hub.get("ports", {}).get(port) if smart_hub else None
            kernel_port = kernel_ports.get((location, port))
            hub_device = next(
                (item for item in instances if item["location"] == location), None
            )
            if smart_hub:
                method = "power"
                enabled = (
                    kernel_port["enabled"]
                    if kernel_port is not None
                    else bool(smart_port and smart_port["powered"])
                )
                hub_description = (
                    hub_device["description"]
                    if hub_device
                    else smart_hub["description"]
                )
                hub_device_id = (
                    hub_device["device_id"]
                    if hub_device
                    else smart_hub["device_id"]
                )
            else:
                method = "disable"
                enabled = kernel_port["enabled"] if kernel_port is not None else None
                hub_description = (
                    hub_device["description"]
                    if hub_device
                    else f"USB {location} hub"
                )
                hub_device_id = hub_device["device_id"] if hub_device else None
            child = self._child_location(location, port)
            direct = [item for item in instances if item["location"] == child]
            downstream = [
                item
                for item in instances
                if item["location"] == child or item["location"].startswith(child + ".")
            ]
            descriptions = sorted({item["description"] for item in direct})
            labels = sorted(
                {label for item in downstream for label in item.get("labels", ())}
            )
            mounted_labels = self._mounted_labels(labels)
            key = f"{location}:{port}"
            public = {
                "key": key,
                "location": location,
                "port": port,
                "method": method,
                "enabled": enabled,
                "device_descriptions": descriptions,
                "downstream_device_count": len(downstream),
                "storage_labels": labels,
                "mounted_labels": mounted_labels,
                "topology_locations": [f"{location}:{port}"],
            }
            hubs.setdefault(
                location,
                {
                    "location": location,
                    "description": hub_description,
                    "device_id": hub_device_id,
                    "method": method,
                    "ports": [],
                },
            )["ports"].append(public)
            targets[key] = {
                **public,
                "disable_path": kernel_port.get("disable_path") if kernel_port else None,
            }

        presented_hubs = self._presentation_hubs(list(hubs.values()), targets)
        now = int(self.wall_clock())
        with self.lock:
            self.targets = targets
            self.data.update(
                {
                    "loaded": True,
                    "checked_at": now,
                    "expires_at": now + int(self.snapshot_ttl),
                    "last_error": error,
                    "hubs": presented_hubs,
                }
            )
            return copy.deepcopy(self.data)

    def discover(self):
        """Run one explicit uhubctl status request and cache its fixed targets."""
        with self.command_lock:
            with self.lock:
                if self.data["operation"].get("status") == "running":
                    raise RuntimeError("another USB port action is already running")
            return self._discover_unlocked()

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
        data["expired"] = bool(
            data.get("loaded")
            and data.get("expires_at") is not None
            and self.wall_clock() >= data["expires_at"]
        )
        return data

    def _live_mounted_labels(self, target, usb_state):
        instances = self._instances(usb_state)
        labels = set()
        topology_locations = target.get("topology_locations") or [
            f"{target['location']}:{target['port']}"
        ]
        for topology_location in topology_locations:
            location, raw_port = topology_location.rsplit(":", 1)
            child = self._child_location(location, int(raw_port))
            labels.update(
                label
                for instance in instances
                if instance["location"] == child
                or instance["location"].startswith(child + ".")
                for label in instance.get("labels", ())
            )
        return self._mounted_labels(labels)

    def _invalidate_controls_locked(self):
        self.targets = {}
        self.data.update(
            {
                "loaded": False,
                "checked_at": None,
                "expires_at": None,
                "last_error": None,
                "hubs": [],
            }
        )

    def start_action(self, key, action):
        if not isinstance(key, str) or not USB_PORT_KEY_RE.fullmatch(key):
            raise ValueError("unknown USB port")
        if action not in self.ACTIONS:
            raise ValueError("USB port action must be on, off, or cycle")
        with self.command_lock:
            with self.lock:
                if self.data["operation"].get("status") == "running":
                    raise RuntimeError("another USB port action is already running")
                if not self.data.get("loaded"):
                    raise RuntimeError("load USB port controls before requesting an action")
                if self.data.get("last_error"):
                    raise RuntimeError("USB port discovery was incomplete; load it again")
                if self.wall_clock() >= (self.data.get("expires_at") or 0):
                    raise RuntimeError("USB port controls expired; load them again")
                target = copy.deepcopy(self.targets.get(key))
            if target is None:
                raise ValueError("unknown USB port")
            if action in ("off", "cycle"):
                usb_state = self.device_monitor.refresh()
                if usb_state.get("last_error"):
                    raise RuntimeError(
                        "cannot verify current USB storage; refusing to disconnect the port"
                    )
                target["mounted_labels"] = self._live_mounted_labels(target, usb_state)
                if target["mounted_labels"]:
                    labels = ", ".join(target["mounted_labels"])
                    raise RuntimeError(
                        f"refusing to disconnect mounted storage ({labels}); unmount it first"
                    )
            with self.lock:
                started_at = int(self.wall_clock())
                self.data["operation"] = {
                    "status": "running",
                    "key": key,
                    "action": action,
                    "started_at": started_at,
                    "completed_at": None,
                    "error": None,
                }
                thread = threading.Thread(
                    target=self._run_action,
                    args=(target, action, started_at),
                    name="usb-port-action",
                    daemon=True,
                )
                thread.start()
                return copy.deepcopy(self.data)

    def start_recovery(self):
        """Start the fixed, guarded Raspberry Pi internal USB 2 hub recovery."""
        with self.command_lock:
            with self.lock:
                if self.data["operation"].get("status") == "running":
                    raise RuntimeError("another USB port action is already running")
                started_at = int(self.wall_clock())
                self.data["operation"] = {
                    "status": "running",
                    "key": "Pi internal USB 2 hub",
                    "action": "restore",
                    "started_at": started_at,
                    "completed_at": None,
                    "message": None,
                    "error": None,
                }
                thread = threading.Thread(
                    target=self._run_recovery,
                    args=(started_at,),
                    name="usb2-recovery",
                    daemon=True,
                )
                thread.start()
                return copy.deepcopy(self.data)

    def _command_ok(self, args, input_text=None):
        result = self.command(args, timeout=self.timeout, input_text=input_text)
        if result.returncode:
            detail = (result.stderr or result.stdout or "USB port command failed").strip()
            raise RuntimeError(detail[-500:])

    def _run_action(self, target, action, started_at):
        error = None
        try:
            with self.command_lock:
                if target["method"] == "power":
                    self._command_ok(
                        [
                            SUDO,
                            "-n",
                            UHUBCTL,
                            "-l",
                            target["location"],
                            "-p",
                            str(target["port"]),
                            "-a",
                            action,
                        ]
                    )
                else:
                    path = target.get("disable_path")
                    expected_root = os.path.join(self.sys_root, "bus", "usb", "devices")
                    if not path or not path.startswith(expected_root + os.sep):
                        raise RuntimeError("kernel USB port control disappeared")
                    values = (
                        ("1\n", "0\n")
                        if action == "cycle"
                        else (("1\n",) if action == "off" else ("0\n",))
                    )
                    for index, value in enumerate(values):
                        self._command_ok(
                            [SUDO, "-n", TEE, path],
                            input_text=value,
                        )
                        if action == "cycle" and index == 0:
                            self.sleeper(2)
        except subprocess.TimeoutExpired:
            error = f"USB port action timed out after {self.timeout:g} seconds"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        with self.lock:
            self._invalidate_controls_locked()
            self.data["operation"] = {
                "status": "error" if error else "complete",
                "key": target["key"],
                "action": action,
                "started_at": started_at,
                "completed_at": int(self.wall_clock()),
                "error": error,
            }

    def _run_recovery(self, started_at):
        error = None
        message = None
        try:
            with self.command_lock:
                result = self.command(
                    [self.recovery_tool], timeout=self.recovery_timeout
                )
                if result.returncode:
                    detail = (
                        result.stderr or result.stdout or "USB 2 recovery failed"
                    ).strip()
                    raise RuntimeError(detail[-500:])
                lines = (result.stdout or "").strip().splitlines()
                message = lines[-1][-500:] if lines else "USB 2 hub restored"
        except subprocess.TimeoutExpired:
            error = f"USB 2 recovery timed out after {self.recovery_timeout:g} seconds"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        with self.lock:
            self._invalidate_controls_locked()
            self.data["operation"] = {
                "status": "error" if error else "complete",
                "key": "Pi internal USB 2 hub",
                "action": "restore",
                "started_at": started_at,
                "completed_at": int(self.wall_clock()),
                "message": message,
                "error": error,
            }
