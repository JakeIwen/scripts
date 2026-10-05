"""Passive proc/sysfs probes and resource sampling."""

import collections
import math
import os
import re
import subprocess
import time

from .common import THROTTLE_FLAGS, iso_time, normalize_boot_id, utc_timestamp


def read_text(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return default


def read_number(path, divisor=1.0):
    raw = read_text(path)
    try:
        return float(raw) / divisor
    except (TypeError, ValueError):
        return None


def parse_cpu_list(value):
    """Expand Linux CPU-list syntax such as ``0-3,6`` into integer IDs."""
    cpus = []
    for part in re.split(r"[\s,]+", str(value or "")):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start, end = (int(item) for item in part.split("-", 1))
                if end < start:
                    continue
                cpus.extend(range(start, end + 1))
            else:
                cpus.append(int(part))
        except ValueError:
            continue
    return sorted(set(cpus))


def collect_thermal_sensors(sys_root="/sys"):
    """Return every readable Linux thermal zone without inventing per-core data."""
    thermal_root = os.path.join(sys_root, "class", "thermal")
    try:
        zones = sorted(
            (
                name
                for name in os.listdir(thermal_root)
                if re.fullmatch(r"thermal_zone\d+", name)
            ),
            key=lambda name: int(name.removeprefix("thermal_zone")),
        )
    except OSError:
        return []

    online_cpus = parse_cpu_list(
        read_text(os.path.join(sys_root, "devices", "system", "cpu", "online"), "")
    )
    sensors = []
    for zone in zones:
        zone_root = os.path.join(thermal_root, zone)
        temperature = read_number(os.path.join(zone_root, "temp"), divisor=1000)
        if temperature is None or not math.isfinite(temperature):
            continue
        sensor_type = read_text(os.path.join(zone_root, "type"), zone) or zone
        sensor = {
            "zone": zone,
            "type": sensor_type,
            "temperature_c": round(temperature, 2),
        }
        if "cpu" in sensor_type.lower() and online_cpus:
            # Raspberry Pi exposes one cpu-thermal package/SoC sensor shared by
            # all cores. Listing its scope is more accurate than duplicating the
            # same reading and calling those values per-core temperatures.
            sensor["cpu_ids"] = online_cpus
            sensor["shared"] = len(online_cpus) > 1
        sensors.append(sensor)
    return sensors


def collect_cpu_frequency_policies(sys_root="/sys"):
    """Return live cpufreq policy state useful when interpreting throttling."""
    cpu_root = os.path.join(sys_root, "devices", "system", "cpu", "cpufreq")
    try:
        policies = sorted(
            (
                name
                for name in os.listdir(cpu_root)
                if re.fullmatch(r"policy\d+", name)
            ),
            key=lambda name: int(name.removeprefix("policy")),
        )
    except OSError:
        return []

    result = []
    for policy in policies:
        policy_root = os.path.join(cpu_root, policy)

        def mhz(filename):
            value = read_number(os.path.join(policy_root, filename))
            return round(value / 1000, 2) if value is not None else None

        result.append(
            {
                "policy": policy,
                "cpu_ids": parse_cpu_list(
                    read_text(os.path.join(policy_root, "related_cpus"), "")
                ),
                "current_mhz": mhz("scaling_cur_freq"),
                "minimum_mhz": mhz("cpuinfo_min_freq"),
                "maximum_mhz": mhz("cpuinfo_max_freq"),
                "governor": read_text(os.path.join(policy_root, "scaling_governor")),
            }
        )
    return result


def run_text(args, timeout=3):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return result.stdout.strip()


def parse_throttled(value):
    """Decode Raspberry Pi ``vcgencmd get_throttled`` output or an integer."""
    if isinstance(value, str):
        match = re.search(r"(?:throttled=)?(0x[0-9a-fA-F]+|[0-9]+)", value)
        if not match:
            return None
        try:
            raw = int(match.group(1), 0)
        except ValueError:
            return None
    elif isinstance(value, int):
        raw = value
    else:
        return None

    current = []
    occurred = []
    for bit, key, _label in THROTTLE_FLAGS:
        if raw & (1 << bit):
            current.append(key)
        if raw & (1 << (bit + 16)):
            occurred.append(key)
    return {
        "raw": raw,
        "hex": f"0x{raw:x}",
        "current": current,
        "occurred": occurred,
    }


def parse_meminfo(proc_root="/proc"):
    values = {}
    for line in (read_text(os.path.join(proc_root, "meminfo"), "") or "").splitlines():
        match = re.match(r"([^:]+):\s+(\d+)(?:\s+kB)?", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "used_percent": round((total - available) * 100 / total, 2) if total else None,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "swap_used_percent": (
            round((swap_total - swap_free) * 100 / swap_total, 2) if swap_total else 0.0
        ),
    }


def parse_pressure(value):
    """Parse one Linux PSI file without depending on a psutil version."""
    result = {}
    for line in str(value or "").splitlines():
        fields = line.split()
        if not fields:
            continue
        entry = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, raw = field.split("=", 1)
            try:
                entry[key] = int(raw) if key == "total" else float(raw)
            except ValueError:
                continue
        result[fields[0]] = entry
    return result


def collect_pressure(proc_root="/proc"):
    return {
        name: parse_pressure(read_text(os.path.join(proc_root, "pressure", name), ""))
        for name in ("cpu", "memory", "io")
    }


def collect_vm_counters(proc_root="/proc"):
    wanted = {
        "allocstall_dma",
        "allocstall_dma32",
        "allocstall_movable",
        "allocstall_normal",
        "oom_kill",
        "pgmajfault",
        "pswpin",
        "pswpout",
    }
    counters = {}
    for line in (read_text(os.path.join(proc_root, "vmstat"), "") or "").splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] not in wanted:
            continue
        try:
            counters[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return counters


def collect_display_state(sys_root="/sys"):
    """Capture passive DRM/console state to correlate recurring VC4 warnings."""
    connectors = []
    base = os.path.join(sys_root, "class", "drm")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(base, name)
        status = read_text(os.path.join(path, "status"))
        if status is None:
            continue
        connectors.append(
            {
                "name": name,
                "status": status,
                "enabled": read_text(os.path.join(path, "enabled")),
                "dpms": read_text(os.path.join(path, "dpms")),
            }
        )
    return {
        "connectors": connectors,
        "framebuffer_blank": read_number(
            os.path.join(sys_root, "class", "graphics", "fb0", "blank")
        ),
        "framebuffer_virtual_size": read_text(
            os.path.join(sys_root, "class", "graphics", "fb0", "virtual_size")
        ),
    }


def parse_cpu_stat(proc_root="/proc"):
    line = (read_text(os.path.join(proc_root, "stat"), "") or "").splitlines()
    if not line or not line[0].startswith("cpu "):
        return None
    try:
        fields = [int(value) for value in line[0].split()[1:]]
    except ValueError:
        return None
    total = sum(fields)
    idle = (fields[3] if len(fields) > 3 else 0) + (fields[4] if len(fields) > 4 else 0)
    return total, idle


def parse_network_counters(value):
    """Parse /proc/net/dev into monotonically increasing interface counters."""
    counters = {}
    for line in str(value or "").splitlines():
        if ":" not in line:
            continue
        name, raw_fields = line.split(":", 1)
        fields = raw_fields.split()
        if len(fields) < 16:
            continue
        try:
            numbers = [int(field) for field in fields]
        except ValueError:
            continue
        counters[name.strip()] = {
            "rx_bytes": numbers[0],
            "rx_packets": numbers[1],
            "rx_errors": numbers[2],
            "rx_drops": numbers[3],
            "tx_bytes": numbers[8],
            "tx_packets": numbers[9],
            "tx_errors": numbers[10],
            "tx_drops": numbers[11],
        }
    return counters


def parse_disk_counters(value):
    """Parse /proc/diskstats into monotonically increasing block counters."""
    counters = {}
    for line in str(value or "").splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        try:
            numbers = [int(field) for field in fields[3:]]
        except ValueError:
            continue
        counters[fields[2]] = {
            "reads": numbers[0],
            "sectors_read": numbers[2],
            "read_milliseconds": numbers[3],
            "writes": numbers[4],
            "sectors_written": numbers[6],
            "write_milliseconds": numbers[7],
            "in_flight": numbers[8],
            "io_milliseconds": numbers[9],
        }
    return counters


def counter_rate(current, previous, elapsed):
    if previous is None or elapsed is None or elapsed <= 0 or current < previous:
        return None
    return (current - previous) / elapsed


def calculate_network_io(current, previous, elapsed, physical_names=()):
    physical_names = set(physical_names)
    interfaces = []
    for name, counters in current.items():
        if name == "lo":
            continue
        old = previous.get(name, {}) if previous else {}
        item = {
            "name": name,
            "physical": name in physical_names,
            "rx_bytes_per_second": counter_rate(
                counters["rx_bytes"], old.get("rx_bytes"), elapsed
            ),
            "tx_bytes_per_second": counter_rate(
                counters["tx_bytes"], old.get("tx_bytes"), elapsed
            ),
            "rx_packets_per_second": counter_rate(
                counters["rx_packets"], old.get("rx_packets"), elapsed
            ),
            "tx_packets_per_second": counter_rate(
                counters["tx_packets"], old.get("tx_packets"), elapsed
            ),
            "errors": max(0, counters["rx_errors"] + counters["tx_errors"]),
            "drops": max(0, counters["rx_drops"] + counters["tx_drops"]),
        }
        interfaces.append(item)
    interfaces.sort(
        key=lambda item: (
            not item["physical"],
            -((item["rx_bytes_per_second"] or 0) + (item["tx_bytes_per_second"] or 0)),
            item["name"],
        )
    )
    physical = [item for item in interfaces if item["physical"]]

    def total(key):
        values = [item[key] for item in physical if item[key] is not None]
        return round(sum(values), 2) if values else None

    return {
        "rx_bytes_per_second": total("rx_bytes_per_second"),
        "tx_bytes_per_second": total("tx_bytes_per_second"),
        "rx_packets_per_second": total("rx_packets_per_second"),
        "tx_packets_per_second": total("tx_packets_per_second"),
        "interfaces": interfaces,
    }


def calculate_disk_io(current, previous, elapsed):
    devices = []
    for name, counters in current.items():
        old = previous.get(name, {}) if previous else {}
        sector_size = counters.get("sector_size", 512)
        read_sectors = counter_rate(
            counters["sectors_read"], old.get("sectors_read"), elapsed
        )
        written_sectors = counter_rate(
            counters["sectors_written"], old.get("sectors_written"), elapsed
        )
        io_ms = counter_rate(
            counters["io_milliseconds"], old.get("io_milliseconds"), elapsed
        )
        item = {
            "name": name,
            "labels": list(counters.get("labels", ())),
            "read_bytes_per_second": (
                round(read_sectors * sector_size, 2) if read_sectors is not None else None
            ),
            "write_bytes_per_second": (
                round(written_sectors * sector_size, 2)
                if written_sectors is not None
                else None
            ),
            "read_iops": counter_rate(counters["reads"], old.get("reads"), elapsed),
            "write_iops": counter_rate(counters["writes"], old.get("writes"), elapsed),
            "busy_percent": min(100.0, round(io_ms / 10, 2)) if io_ms is not None else None,
            "in_flight": counters["in_flight"],
        }
        devices.append(item)
    devices.sort(
        key=lambda item: -(
            (item["read_bytes_per_second"] or 0) + (item["write_bytes_per_second"] or 0)
        )
    )

    def total(key):
        values = [item[key] for item in devices if item[key] is not None]
        return round(sum(values), 2) if values else None

    busy_devices = [item for item in devices if item["busy_percent"] is not None]
    busiest = max(busy_devices, key=lambda item: item["busy_percent"]) if busy_devices else None
    return {
        "read_bytes_per_second": total("read_bytes_per_second"),
        "write_bytes_per_second": total("write_bytes_per_second"),
        "read_iops": total("read_iops"),
        "write_iops": total("write_iops"),
        "busy_percent": busiest["busy_percent"] if busiest else None,
        "busiest_device": busiest["name"] if busiest else None,
        "devices": devices,
    }


def process_details(proc_root="/proc"):
    details = []
    page_size = os.sysconf("SC_PAGE_SIZE")
    try:
        names = os.listdir(proc_root)
    except OSError:
        return details
    for name in names:
        if not name.isdigit():
            continue
        stat = read_text(os.path.join(proc_root, name, "stat"))
        if not stat:
            continue
        end = stat.rfind(")")
        start = stat.find("(")
        if start < 0 or end < start:
            continue
        fields = stat[end + 2 :].split()
        if len(fields) < 22:
            continue
        try:
            ticks = int(fields[11]) + int(fields[12])
            start_ticks = int(fields[19])
            rss_bytes = max(0, int(fields[21])) * page_size
        except (ValueError, IndexError):
            continue
        # Never retain /proc/<pid>/cmdline: command arguments frequently contain
        # tokens, passwords, or private URLs.  The kernel comm name and PID are
        # sufficient to identify a peak without copying secrets into the DB/UI.
        command = stat[start + 1 : end]
        details.append(
            {
                "pid": int(name),
                "key": f"{name}:{start_ticks}",
                "name": stat[start + 1 : end],
                "command": command[:240],
                "ticks": ticks,
                "rss_bytes": rss_bytes,
            }
        )
    return details


def collect_network_counter_state(proc_root="/proc", sys_root="/sys"):
    all_counters = parse_network_counters(
        read_text(os.path.join(proc_root, "net", "dev"), "")
    )
    selected = {}
    physical = []
    for name, counters in all_counters.items():
        interface_type = read_text(
            os.path.join(sys_root, "class", "net", name, "type")
        )
        # ARPHRD_ETHER (1) covers Ethernet/Wi-Fi/bridges. Tailscale uses
        # ARPHRD_NONE (65534). Exclude CAN (280) and loopback from this view.
        if name == "lo" or interface_type not in (None, "1", "65534"):
            continue
        selected[name] = counters
        if interface_type == "1" and os.path.exists(
            os.path.join(sys_root, "class", "net", name, "device")
        ):
            physical.append(name)
    return selected, physical


def collect_block_labels(dev_root="/dev", sys_root="/sys"):
    labels_by_device = collections.defaultdict(list)
    label_dir = os.path.join(dev_root, "disk", "by-label")
    try:
        labels = os.listdir(label_dir)
    except OSError:
        return labels_by_device
    for label in labels:
        try:
            block_name = os.path.basename(os.path.realpath(os.path.join(label_dir, label)))
        except OSError:
            continue
        sys_path = os.path.join(sys_root, "class", "block", block_name)
        if os.path.exists(os.path.join(sys_path, "partition")):
            parent = os.path.basename(os.path.dirname(os.path.realpath(sys_path)))
        else:
            parent = block_name
        if parent:
            labels_by_device[parent].append(label)
    for values in labels_by_device.values():
        values.sort()
    return labels_by_device


def collect_disk_counter_state(proc_root="/proc", sys_root="/sys", dev_root="/dev"):
    all_counters = parse_disk_counters(read_text(os.path.join(proc_root, "diskstats"), ""))
    labels = collect_block_labels(dev_root, sys_root)
    try:
        whole_devices = os.listdir(os.path.join(sys_root, "block"))
    except OSError:
        whole_devices = []
    selected = {}
    for name in whole_devices:
        if not re.match(r"^(?:sd[a-z]+|mmcblk\d+|nvme\d+n\d+|vd[a-z]+|xvd[a-z]+)$", name):
            continue
        counters = all_counters.get(name)
        if counters is None:
            continue
        sector_size = read_number(
            os.path.join(sys_root, "block", name, "queue", "hw_sector_size")
        )
        selected[name] = {
            **counters,
            "sector_size": int(sector_size or 512),
            "labels": labels.get(name, []),
        }
    return selected


def collect_usb_state(sys_root="/sys"):
    base = os.path.join(sys_root, "bus", "usb", "devices")
    devices = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return devices
    for name in names:
        path = os.path.join(base, name)
        vendor = read_text(os.path.join(path, "idVendor"))
        product_id = read_text(os.path.join(path, "idProduct"))
        if vendor is None or product_id is None:
            continue
        devices.append(
            {
                "path": name,
                "id": f"{vendor}:{product_id}",
                "manufacturer": read_text(os.path.join(path, "manufacturer")),
                "product": read_text(os.path.join(path, "product")),
                "speed_mbps": read_number(os.path.join(path, "speed")),
            }
        )
    return devices


def collect_mount_state(proc_root="/proc"):
    mounts = []
    for line in (read_text(os.path.join(proc_root, "mounts"), "") or "").splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].startswith("/dev/"):
            continue
        mounts.append(
            {
                "device": fields[0],
                "mountpoint": fields[1].replace("\\040", " "),
                "filesystem": fields[2],
                "read_only": "ro" in fields[3].split(","),
            }
        )
    return mounts


class ResourceSampler:
    def __init__(
        self,
        proc_root="/proc",
        sys_root="/sys",
        dev_root="/dev",
        clock=utc_timestamp,
        monotonic=time.monotonic,
        command=run_text,
    ):
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.dev_root = dev_root
        self.clock = clock
        self.monotonic = monotonic
        self.command = command
        self.previous_cpu = None
        self.previous_processes = {}
        self.previous_process_at = None
        self.previous_network = {}
        self.previous_disks = {}
        self.previous_io_at = None
        try:
            self.ticks_per_second = os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError):
            self.ticks_per_second = 100
        self.cpu_count = max(1, os.cpu_count() or 1)

    def _root_usage(self):
        try:
            stat = os.statvfs("/")
        except OSError:
            return {"used_percent": None, "free_bytes": None, "total_bytes": None}
        total = stat.f_blocks * stat.f_frsize
        available = stat.f_bavail * stat.f_frsize
        used = total - stat.f_bfree * stat.f_frsize
        return {
            "used_percent": round(used * 100 / total, 2) if total else None,
            "free_bytes": available,
            "total_bytes": total,
        }

    def _process_usage(self, now_mono):
        processes = process_details(self.proc_root)
        elapsed = None if self.previous_process_at is None else now_mono - self.previous_process_at
        top_cpu = []
        if elapsed and elapsed > 0:
            for item in processes:
                previous_ticks = self.previous_processes.get(item["key"])
                if previous_ticks is None:
                    continue
                delta = max(0, item["ticks"] - previous_ticks)
                item["cpu_percent"] = round(
                    delta * 100 / self.ticks_per_second / elapsed, 2
                )
                top_cpu.append(item)
        top_cpu.sort(key=lambda item: item.get("cpu_percent", 0), reverse=True)
        top_memory = sorted(processes, key=lambda item: item["rss_bytes"], reverse=True)
        self.previous_processes = {item["key"]: item["ticks"] for item in processes}
        self.previous_process_at = now_mono

        def public(item, include_cpu=False):
            value = {
                "pid": item["pid"],
                "name": item["name"],
                "command": item["command"],
                "rss_bytes": item["rss_bytes"],
            }
            if include_cpu:
                value["cpu_percent"] = item.get("cpu_percent", 0.0)
            return value

        return (
            [public(item, True) for item in top_cpu[:5]],
            [public(item) for item in top_memory[:5]],
        )

    def sample(self):
        now = self.clock()
        now_mono = self.monotonic()
        cpu = parse_cpu_stat(self.proc_root)
        cpu_percent = None
        if cpu is not None and self.previous_cpu is not None:
            total_delta = cpu[0] - self.previous_cpu[0]
            idle_delta = cpu[1] - self.previous_cpu[1]
            if total_delta > 0:
                cpu_percent = round(
                    max(0.0, min(100.0, (total_delta - idle_delta) * 100 / total_delta)),
                    2,
                )
        self.previous_cpu = cpu
        top_cpu, top_memory = self._process_usage(now_mono)
        io_elapsed = None if self.previous_io_at is None else now_mono - self.previous_io_at
        network_counters, physical_networks = collect_network_counter_state(
            self.proc_root, self.sys_root
        )
        disk_counters = collect_disk_counter_state(
            self.proc_root, self.sys_root, self.dev_root
        )
        network_io = calculate_network_io(
            network_counters, self.previous_network, io_elapsed, physical_networks
        )
        disk_io = calculate_disk_io(disk_counters, self.previous_disks, io_elapsed)
        self.previous_network = network_counters
        self.previous_disks = disk_counters
        self.previous_io_at = now_mono
        memory = parse_meminfo(self.proc_root)
        try:
            load_values = [float(value) for value in os.getloadavg()]
        except (OSError, AttributeError):
            raw_load = (read_text(os.path.join(self.proc_root, "loadavg"), "0 0 0") or "0 0 0").split()
            load_values = [float(value) for value in raw_load[:3]]

        throttle_output = self.command(["/usr/bin/vcgencmd", "get_throttled"], timeout=3)
        throttle = parse_throttled(throttle_output)
        uptime_raw = (read_text(os.path.join(self.proc_root, "uptime"), "") or "").split()
        try:
            uptime = float(uptime_raw[0])
        except (IndexError, ValueError):
            uptime = None
        thermal_sensors = collect_thermal_sensors(self.sys_root)
        cpu_sensors = [
            sensor for sensor in thermal_sensors if "cpu" in sensor["type"].lower()
        ]
        primary_temperature = (cpu_sensors or thermal_sensors or [{}])[0].get(
            "temperature_c"
        )
        frequency_policies = collect_cpu_frequency_policies(self.sys_root)
        arm_khz = read_number(
            os.path.join(
                self.sys_root,
                "devices",
                "system",
                "cpu",
                "cpufreq",
                "policy0",
                "scaling_cur_freq",
            )
        )
        return {
            "timestamp": now,
            "timestamp_iso": iso_time(now),
            "boot_id": normalize_boot_id(
                read_text(os.path.join(self.proc_root, "sys", "kernel", "random", "boot_id"))
            ),
            "uptime_seconds": uptime,
            "cpu_percent": cpu_percent,
            "cpu_count": self.cpu_count,
            "load": {"1m": load_values[0], "5m": load_values[1], "15m": load_values[2]},
            "memory": {
                "used_percent": memory["used_percent"],
                "used_bytes": memory["used_bytes"],
                "available_bytes": memory["available_bytes"],
                "total_bytes": memory["total_bytes"],
            },
            "swap": {
                "used_percent": memory["swap_used_percent"],
                "used_bytes": memory["swap_used_bytes"],
                "total_bytes": memory["swap_total_bytes"],
            },
            # Keep the legacy primary value for thresholds and old dashboard
            # clients while also reporting every thermal zone with its scope.
            "temperature_c": primary_temperature,
            "thermal_sensors": thermal_sensors,
            "arm_mhz": round(arm_khz / 1000, 2) if arm_khz is not None else None,
            "cpu_frequency_policies": frequency_policies,
            "throttle": throttle,
            "root_filesystem": self._root_usage(),
            "top_cpu": top_cpu,
            "top_memory": top_memory,
            "network_io": network_io,
            "disk_io": disk_io,
            "pressure": collect_pressure(self.proc_root),
            "vm_counters": collect_vm_counters(self.proc_root),
            "display": collect_display_state(self.sys_root),
        }
