"""Metric extraction and RollupAccumulator state."""

import math
import statistics

if __package__:
    from .common import DEFAULT_ROLLUP_INTERVAL
else:
    from common import DEFAULT_ROLLUP_INTERVAL


def metric_value(sample, path):
    value = sample
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


class RollupAccumulator:
    METRICS = {
        "cpu": ("cpu_percent",),
        "memory": ("memory", "used_percent"),
        "swap": ("swap", "used_percent"),
        "load1": ("load", "1m"),
        "temperature": ("temperature_c",),
        "root_used": ("root_filesystem", "used_percent"),
        "arm_mhz": ("arm_mhz",),
        "network_rx": ("network_io", "rx_bytes_per_second"),
        "network_tx": ("network_io", "tx_bytes_per_second"),
        "disk_read": ("disk_io", "read_bytes_per_second"),
        "disk_write": ("disk_io", "write_bytes_per_second"),
        "disk_busy": ("disk_io", "busy_percent"),
    }

    def __init__(self, interval=DEFAULT_ROLLUP_INTERVAL):
        self.interval = interval
        self.samples = []
        self.period_start = None

    def add(self, sample):
        if self.period_start is None:
            self.period_start = sample["timestamp"]
        self.samples.append(sample)

    def ready(self, now):
        return self.period_start is not None and now - self.period_start >= self.interval

    def flush(self):
        if not self.samples:
            return None
        metrics = {}
        for name, path in self.METRICS.items():
            candidates = [
                (metric_value(sample, path), sample)
                for sample in self.samples
                if metric_value(sample, path) is not None
            ]
            if not candidates:
                metrics[name] = {"peak": None, "average": None, "at": None}
                continue
            if name == "arm_mhz":
                peak_value, peak_sample = min(candidates, key=lambda pair: pair[0])
            else:
                peak_value, peak_sample = max(candidates, key=lambda pair: pair[0])
            entry = {
                "peak": round(peak_value, 2),
                "average": round(statistics.fmean(value for value, _sample in candidates), 2),
                "at": peak_sample["timestamp"],
            }
            if name == "cpu":
                entry["top_process"] = (peak_sample.get("top_cpu") or [None])[0]
            if name == "memory":
                entry["top_process"] = (peak_sample.get("top_memory") or [None])[0]
                entry["available_min_bytes"] = min(
                    sample["memory"]["available_bytes"] for _value, sample in candidates
                )
            if name in ("network_rx", "network_tx"):
                interface_key = (
                    "rx_bytes_per_second" if name == "network_rx" else "tx_bytes_per_second"
                )
                interfaces = peak_sample.get("network_io", {}).get("interfaces", ())
                eligible = [
                    item
                    for item in interfaces
                    if item.get("physical") and item.get(interface_key) is not None
                ]
                entry["top_interface"] = (
                    max(eligible, key=lambda item: item[interface_key]) if eligible else None
                )
            if name in ("disk_read", "disk_write", "disk_busy"):
                device_key = {
                    "disk_read": "read_bytes_per_second",
                    "disk_write": "write_bytes_per_second",
                    "disk_busy": "busy_percent",
                }[name]
                devices = peak_sample.get("disk_io", {}).get("devices", ())
                eligible = [item for item in devices if item.get(device_key) is not None]
                entry["top_device"] = (
                    max(eligible, key=lambda item: item[device_key]) if eligible else None
                )
            metrics[name] = entry
        thermal_metrics = []
        sensor_keys = sorted(
            {
                (sensor.get("zone"), sensor.get("type"))
                for sample in self.samples
                for sensor in sample.get("thermal_sensors", ())
                if sensor.get("zone") and sensor.get("type")
            }
        )
        for zone, sensor_type in sensor_keys:
            candidates = []
            cpu_ids = []
            shared = False
            for sample in self.samples:
                sensor = next(
                    (
                        item
                        for item in sample.get("thermal_sensors", ())
                        if item.get("zone") == zone and item.get("type") == sensor_type
                    ),
                    None,
                )
                value = sensor.get("temperature_c") if sensor else None
                if isinstance(value, (int, float)) and math.isfinite(value):
                    candidates.append((value, sample["timestamp"]))
                    cpu_ids = sensor.get("cpu_ids") or cpu_ids
                    shared = bool(sensor.get("shared", shared))
            if not candidates:
                continue
            peak, at = max(candidates, key=lambda pair: pair[0])
            thermal_metrics.append(
                {
                    "zone": zone,
                    "type": sensor_type,
                    "cpu_ids": cpu_ids,
                    "shared": shared,
                    "peak": round(peak, 2),
                    "average": round(
                        statistics.fmean(value for value, _timestamp in candidates), 2
                    ),
                    "at": at,
                }
            )
        metrics["thermal_sensors"] = thermal_metrics
        last = self.samples[-1]
        rollup = {
            "period_start": self.period_start,
            "period_end": last["timestamp"],
            "boot_id": last.get("boot_id"),
            "sample_count": len(self.samples),
            "cpu_peak": metrics["cpu"]["peak"],
            "memory_peak": metrics["memory"]["peak"],
            "swap_peak": metrics["swap"]["peak"],
            "load1_peak": metrics["load1"]["peak"],
            "temperature_peak": metrics["temperature"]["peak"],
            "root_used_peak": metrics["root_used"]["peak"],
            "arm_mhz_min": metrics["arm_mhz"]["peak"],
            "metrics": metrics,
        }
        self.samples = []
        self.period_start = None
        return rollup
