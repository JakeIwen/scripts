"""In-memory systemd boundary for package deployment transaction tests."""
from pi import deploy_python as deployment


def package_unit(unit, description="fixture package unit"):
    spec = deployment.SERVICES[unit]
    return (
        "[Service]\n"
        f"ExecStart={spec['interpreter']} -P -m pi.apps.{spec['app']}\n"
        f"Description={description}\n"
    ).encode()


def flat_unit(unit, description="fixture flat unit"):
    spec = deployment.SERVICES[unit]
    return (
        "[Service]\n"
        f"ExecStart={spec['interpreter']} /home/pi/scripts/python-automation/{spec['flat']}\n"
        f"Description={description}\n"
    ).encode()


class FakeSystemServices:
    """An in-memory service manager for receiver-side transaction tests."""

    def __init__(self, units=None, active=None, restart_failures=None):
        self.units = {
            unit: flat_unit(unit) for unit, spec in deployment.SERVICES.items() if spec['flat'] is not None
        }
        if units:
            self.units.update(units)
        self.active = set(active or ())
        self.restart_failures = dict(restart_failures or {})
        self.install_calls = []
        self.restart_calls = []
        self.saved_legacy = []
        self.dropins_by_unit = {unit: {} for unit in deployment.SERVICES}
        self.release_root = None
        self.next_pid = 4100
        self.invocation_number = 0
        self.running_records = {
            unit: None for unit in (*deployment.SERVICES, *deployment.EXTERNAL_CONSUMERS)
        }
        self.state_overrides = {}
        self.loaded_files = {}
        self.state_errors = {}
        self.running_record_errors = {}

    def bind_release_root(self, root):
        self.release_root = root.resolve()

    def set_running_release(self, unit, release):
        self.next_pid += 1
        self.invocation_number += 1
        invocation_id = f"{self.invocation_number:032x}"
        self.running_records[unit] = {
            "package_path": str(release.resolve() / "pi"),
            "pid": self.next_pid,
            "invocation_id": invocation_id,
        }

    def read_unit(self, unit):
        if unit not in self.units:
            raise FileNotFoundError(unit)
        return self.units[unit]

    def disk_files(self, unit):
        base = f"/etc/systemd/system/{unit}"
        files = {base: self.units[unit]} if unit in self.units else {}
        files.update({f"{base}.d/{name}": raw for name, raw in self.dropins_by_unit.get(unit, {}).items()})
        return files

    def consumer_state(self, unit):
        """Mimic `systemctl show`: Exec* come from what is loaded (files unless
        loaded_files says the on-disk ones were not reloaded), like systemd."""
        disk = self.disk_files(unit)
        loaded = self.loaded_files.get(unit, disk)
        base = f"/etc/systemd/system/{unit}"
        lines = [f"LoadState={'loaded' if loaded else 'not-found'}",
                 f"NeedDaemonReload={'no' if loaded == disk else 'yes'}",
                 f"FragmentPath={base if base in loaded else ''}",
                 "DropInPaths=" + " ".join(path for path in loaded if path != base)]
        for path, raw in loaded.items():
            for key, value in deployment.directives(raw):
                if key.startswith("Exec") and value:
                    lines.append(f"{key}={{ path={value.split()[0]} ; argv[]={value} ; ignore_errors=no ; "
                                 "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }")
        return deployment.parse_show("\n".join(lines)), list(disk.items())

    def require_absent(self, unit):
        if unit in self.units or self.dropins_by_unit[unit]:
            raise ValueError('unit already exists')

    def dropins(self, unit):
        return dict(self.dropins_by_unit[unit])

    def save_legacy(self, unit, destination, dropins):
        self.saved_legacy.append((unit, destination, dict(dropins)))
        if destination.exists():
            if not (destination / unit).is_file():
                raise ValueError("incomplete fake backup")
            return
        destination.mkdir(parents=True, mode=0o700)
        (destination / unit).write_bytes(self.units[unit])
        if dropins:
            directory = destination / (unit + ".d")
            directory.mkdir()
            for filename, data in dropins.items():
                (directory / filename).write_bytes(data)

    def install_unit(self, unit, source):
        self.install_calls.append((unit, source))
        self.units[unit] = source.read_bytes()

    def is_active(self, unit):
        return unit in self.active

    def service_state(self, unit):
        if unit in self.state_errors:
            raise self.state_errors[unit]
        if unit in self.state_overrides:
            return dict(self.state_overrides[unit])
        if unit not in self.active:
            return {"ActiveState": "inactive", "MainPID": "0", "InvocationID": ""}
        record = self.running_records[unit]
        if record is None:
            return {
                "ActiveState": "active",
                "MainPID": str(self.next_pid),
                "InvocationID": f"{self.invocation_number:032x}",
            }
        return {
            "ActiveState": "active",
            "MainPID": str(record["pid"]),
            "InvocationID": record["invocation_id"],
        }

    def read_running_record(self, unit):
        if unit in self.running_record_errors:
            raise self.running_record_errors[unit]
        record = self.running_records[unit]
        if record is None:
            raise FileNotFoundError(f"{unit} running record is absent")
        return dict(record)

    def restart(self, unit):
        self.restart_calls.append(unit)
        if self.restart_failures.get(unit, 0):
            self.restart_failures[unit] -= 1
            self.active.discard(unit)
            raise RuntimeError(f"simulated {unit} restart failure")
        self.active.add(unit)
        if self.release_root is not None:
            current = deployment.current_release(self.release_root)
            if current is None:
                raise RuntimeError("package restart requires a current release")
            self.set_running_release(unit, current)
