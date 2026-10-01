#!/usr/bin/env python3
"""Exercise a root loopback receiver and a separate pi/adm reader on Linux.

Run with sudo on the Pi. No live receiver, configuration, or service is changed;
the daemon, UDP listener, raw files, directory policy, and installer repair use
a private directory under /run. A tiny helper uses /tmp because /run is noexec.
--compare-modes proves both the old failure and the fix.
"""

import argparse
import grp
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time


READER = r"""
import json, os, pathlib, stat, sys
rows = []
for value in json.loads(sys.argv[1]):
    path = pathlib.Path(value)
    info = path.stat()
    row = dict(name=path.name, uid=info.st_uid, gid=info.st_gid,
               mode=oct(stat.S_IMODE(info.st_mode)), readable=False)
    try:
        with path.open('rb') as stream:
            stream.read(1)
        row['readable'] = True
    except OSError as error:
        row['error_type'] = type(error).__name__
        row['errno'] = error.errno
    rows.append(row)
print(json.dumps(dict(uid=os.getuid(), gid=os.getgid(), groups=os.getgroups(), files=rows)))
"""

# The live Pi main config supplies these defaults around the included receiver
# fragment. Reopen falls back to the global create mode; standalone rsyslog's
# 0644 default would hide the root:root readability failure under test.
PI_FILE_DEFAULTS = "$FileOwner root\n$FileGroup adm\n$FileCreateMode 0640\n$DirCreateMode 0755\n$Umask 0022\n"


def reader_snapshot(paths, reader, adm_gid):
    """Do actual opens as the collector identity, never as the root checker."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", READER, json.dumps([str(p) for p in paths])],
        user=reader.pw_uid, group=reader.pw_gid, extra_groups=[adm_gid],
        cwd="/", capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def receiver_config(source, root, port, helper_path=None):
    """Change only fixture destinations/listener/budget; preserve root:adm."""
    helper_path = helper_path or root / "rotate.py"
    rendered = (source.replace("192.168.6.1", "127.0.0.1")
                .replace("/run/vanpi-network/spool", str(root / "spool"))
                .replace('port="514"', f'address="127.0.0.1" port="{port}"')
                .replace('rotation.sizeLimit="1048576"', 'rotation.sizeLimit="4096"')
                .replace("/usr/local/libexec/vanpi-rotate-network-log", str(helper_path)))
    outputs = re.findall(r'^\s*file="([^"]+)"', rendered, re.MULTILINE)
    if set(outputs) != {str(root / "spool" / name) for name in ("network.jsonl", "dendelion.log")}:
        raise ValueError("Receiver file outputs were not completely isolated")
    listener = rf'^input\(type="imudp" address="127\.0\.0\.1" port="{port}"(?: ruleset="OpenWrtDendelion")?\)$'
    if rendered.count('input(') != 1 or not re.search(listener, rendered, re.MULTILINE):
        raise ValueError("Receiver listener was not completely isolated")
    commands = re.findall(r'rotation\.sizeLimitCommand="([^"]+)"', rendered)
    if set(commands) != {f"{helper_path} legacy", f"{helper_path} json"}:
        raise ValueError("Receiver rotation commands were not completely isolated")
    return PI_FILE_DEFAULTS + rendered


def isolated_rotation_helper(source, spool_dir):
    """Refuse launch if a future helper change defeats path isolation."""
    rendered, replacements = re.subn(
        r'''(?m)^RAM_SPOOL\s*=\s*Path\((['"])/run/vanpi-network/spool\1\)[ \t]*$''',
        lambda _match: f"RAM_SPOOL = Path({str(spool_dir)!r})", source,
    )
    if replacements != 1 or "/run/vanpi-network/spool" in rendered:
        raise ValueError("Rotation helper was not completely isolated")
    return rendered


def has_tail(path, marker):
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 4096))
            return marker in stream.read(4096)
    except FileNotFoundError:
        return False


def load_repair_installer(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[2] / "deploy_network_storage.py"
    spec = importlib.util.spec_from_file_location("spool_repair_installer", path)
    module = importlib.util.module_from_spec(spec)
    previous = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous
    return module


def repair_existing_files(template, root, files, reader, adm_gid, installer_path=None):
    """Exercise the real installer repair after applying its directory policy."""
    before = {path.name: path.read_bytes() for path in files}
    for path in files:
        os.chown(path, 0, 0)
        path.chmod(0o640)
    assert not any(item["readable"] for item in reader_snapshot(files, reader, adm_gid)["files"])
    untouched = [root / "spool" / "network.jsonl.4", root / "spool" / "dendelion.log.4"]
    for path in untouched:
        path.write_text("outside the bounded repair fixture\n")
        os.chown(path, 0, 0)
        path.chmod(0o600)
    rendered = Path(template).read_text().replace("/run/vanpi-network", str(root))
    for line in rendered.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        assert fields[0] == "d", fields
        assert fields[1] == str(root) or fields[1].startswith(str(root) + "/"), fields
        assert ".." not in Path(fields[1]).parts, fields
    config = root / "tmpfiles.conf"
    config.write_text(rendered)
    repaired = subprocess.run(
        ["/usr/bin/systemd-tmpfiles", "--create", str(config)],
        capture_output=True, text=True, timeout=10,
    )
    assert repaired.returncode == 0, repaired.stderr
    assert stat.S_IMODE((root / "spool").stat().st_mode) == 0o2750
    installer = load_repair_installer(installer_path)
    repair_result = installer.repair_spool_permissions({"runtime_root": str(root)})
    snapshot = reader_snapshot(files, reader, adm_gid)
    assert all(item["readable"] and item["gid"] == adm_gid and item["mode"] == "0o640"
               for item in snapshot["files"]), snapshot
    assert before == {path.name: path.read_bytes() for path in files}
    assert all(path.stat().st_gid == 0 and stat.S_IMODE(path.stat().st_mode) == 0o600
               for path in untouched)
    return dict(repaired_generations=len(files), contents_unchanged=True,
                generations_outside_ring_untouched=True, reader=snapshot,
                installer_result=repair_result)


def check(config_path, rotation_path, *, spool_mode=0o2750, expect_readable=True,
          tmpfiles_path=None, repair_installer_path=None, reader_user="pi", reader_group="adm"):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("Run this isolated Linux check with sudo; receiver and reader must have different identities")
    reader = pwd.getpwnam(reader_user)
    adm_gid = grp.getgrnam(reader_group).gr_gid
    if reader.pw_uid == 0 or reader.pw_gid == 0 or adm_gid == 0:
        raise RuntimeError("The collector fixture must be non-root without primary/root group membership")
    with tempfile.TemporaryDirectory(prefix="network-rsyslog-check-", dir="/run") as directory, \
            tempfile.TemporaryDirectory(prefix="network-rsyslog-helper-", dir="/tmp") as helper_directory:
        root = Path(directory)
        os.chown(root, 0, adm_gid)
        root.chmod(0o750)
        spool_dir = root / "spool"
        spool_dir.mkdir()
        os.chown(spool_dir, 0, adm_gid)
        spool_dir.chmod(spool_mode)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        helper = Path(helper_directory) / "rotate.py"
        helper.write_text(isolated_rotation_helper(Path(rotation_path).read_text(), spool_dir))
        helper.chmod(0o755)
        target = root / "receiver.conf"
        target.write_text(receiver_config(Path(config_path).read_text(), root, port, helper_path=helper))
        validation = subprocess.run(["/usr/sbin/rsyslogd", "-N1", "-f", str(target)],
                                    capture_output=True, text=True, timeout=10)
        assert validation.returncode == 0, validation.stderr
        daemon = subprocess.Popen(
            ["/usr/sbin/rsyslogd", "-n", "-i", str(root / "pid"), "-f", str(target)],
            user=0, group=0, extra_groups=[], umask=0o022,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.4)
            assert daemon.poll() is None, daemon.communicate()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                messages = [
                    '<30>Sep 29 22:10:01 fixture uplink-https: interface=wan state=offline google=000/6 cloudflare=000/6',
                    '<30>Sep 29 22:10:02 fixture hostapd: quoted="line\\value"',
                    '<30>Sep 29 22:10:03 fixture dnsmasq[1]: query[A] ordinary-browsing.example from 192.0.2.1',
                    '<30>Sep 29 22:10:04 fixture dnsmasq[1]: no servers found, will retry',
                    '<30>Sep 29 22:10:05 fixture dropbear[1]: irrelevant login',
                ]
                for message in messages:
                    sender.sendto(message.encode(), ("127.0.0.1", port))
                spool = spool_dir / "network.jsonl"
                for _ in range(100):
                    if spool.exists() and len(spool.read_text().splitlines()) >= 3:
                        break
                    time.sleep(0.02)
                rows = [json.loads(line) for line in spool.read_text().splitlines()]
                assert len(rows) == 3, rows
                assert rows[0]["reported_at"][11:19] == "22:10:01", rows[0]
                assert rows[0]["received_at"] != rows[0]["reported_at"], rows[0]
                assert rows[0]["protocol_version"] == "0", rows[0]
                assert 'quoted="line\\value"' in rows[1]["message"], rows[1]
                initial = reader_snapshot([spool, spool_dir / "dendelion.log"], reader, adm_gid)
                assert all(item["readable"] and item["gid"] == adm_gid for item in initial["files"]), initial
                for number in range(120):
                    message = f"<30>Sep 29 22:10:06 fixture hostapd: burst={number} " + "x" * 400
                    sender.sendto(message.encode(), ("127.0.0.1", port))
                    time.sleep(0.004)
                marker = f"rotation-check-complete={os.getpid()}".encode()
                sender.sendto(b"<30>Sep 29 22:10:07 fixture hostapd: " + marker, ("127.0.0.1", port))
                deadline = time.monotonic() + 30
                while not all(has_tail(spool_dir / name, marker) for name in ("network.jsonl", "dendelion.log")):
                    if time.monotonic() >= deadline:
                        raise AssertionError("Isolated receiver did not drain the final marker to both active streams")
                    time.sleep(0.05)
        finally:
            daemon.terminate()
            daemon_stdout, daemon_stderr = daemon.communicate(timeout=5)

        files = sorted(spool_dir.glob("network.jsonl*"))
        legacy_files = sorted(spool_dir.glob("dendelion.log*"))
        diagnostics = dict(files=[path.name for path in files],
                           bytes=sum(path.stat().st_size for path in files),
                           stdout=daemon_stdout.decode("utf-8", "replace")[-2000:],
                           stderr=daemon_stderr.decode("utf-8", "replace")[-2000:])
        assert len(files) == 4, diagnostics
        assert len(legacy_files) == 4, diagnostics
        all_rows = [json.loads(line) for path in files for line in path.read_text().splitlines()]
        assert all("ordinary-browsing" not in row["message"] for row in all_rows)
        total = sum(path.stat().st_size for path in files)
        legacy_total = sum(path.stat().st_size for path in legacy_files)
        assert total < 4 * (4096 + 2048), total
        assert legacy_total < 4 * (4096 + 2048), legacy_total
        snapshot = reader_snapshot(files + legacy_files, reader, adm_gid)
        readable = all(item["readable"] for item in snapshot["files"])
        assert readable == expect_readable, snapshot
        if expect_readable:
            assert all(item["uid"] == 0 and item["gid"] == adm_gid for item in snapshot["files"]), snapshot
        else:
            assert any(item.get("error_type") == "PermissionError" and item["gid"] == 0
                       for item in snapshot["files"]), snapshot
        result = dict(valid_json_rows_retained=len(all_rows), generations=len(files),
                      bytes_retained=total, isolated_size_limit=4096,
                      legacy_generations=len(legacy_files), legacy_bytes_retained=legacy_total,
                      dual_clocks=True, ordinary_dns_and_login_excluded=True,
                      initial_reader_access=True, burst_messages=120,
                      receiver_uid=0, receiver_gid=0, spool_mode=oct(spool_mode),
                      receiver_umask="0022", global_file_create_mode="0640",
                      readable_after_rotation=readable, reader=snapshot)
        if tmpfiles_path:
            result["existing_file_repair"] = repair_existing_files(
                tmpfiles_path, root, files + legacy_files, reader, adm_gid, repair_installer_path)
        assert root.resolve() == root and not os.path.ismount(root)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receiver_config")
    parser.add_argument("rotation_helper")
    parser.add_argument("--tmpfiles")
    parser.add_argument("--repair-installer", help="defaults to deploy_network_storage.py adjacent to this checkout's tests")
    parser.add_argument("--spool-mode", type=lambda value: int(value, 8), default=0o2750)
    parser.add_argument("--expect-unreadable", action="store_true")
    parser.add_argument("--compare-modes", action="store_true")
    args = parser.parse_args()
    if args.compare_modes:
        result = {
            "old_without_setgid": check(args.receiver_config, args.rotation_helper,
                                       spool_mode=0o750, expect_readable=False, tmpfiles_path=args.tmpfiles,
                                       repair_installer_path=args.repair_installer),
            "new_with_setgid": check(args.receiver_config, args.rotation_helper,
                                    spool_mode=0o2750, expect_readable=True),
        }
    else:
        result = check(args.receiver_config, args.rotation_helper,
                       spool_mode=args.spool_mode, expect_readable=not args.expect_unreadable,
                       tmpfiles_path=args.tmpfiles, repair_installer_path=args.repair_installer)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
