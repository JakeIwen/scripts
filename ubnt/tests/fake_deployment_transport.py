#!/usr/bin/env python3
"""Fake ssh/scp transport for the UBNT deployment integration test."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def append_event(*parts: object) -> None:
    event_log = Path(required_env("FAKE_EVENT_LOG"))
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write("\t".join(str(part).replace("\n", "\\n") for part in parts))
        handle.write("\n")


def device_root() -> Path:
    return Path(required_env("FAKE_DEVICE_ROOT")).resolve()


def remote_bin() -> Path:
    return Path(required_env("FAKE_REMOTE_BIN")).resolve()


def map_remote_path(path: str) -> Path:
    if not path.startswith("/"):
        raise SystemExit(f"remote path is not absolute: {path}")
    mapped = (device_root() / path.lstrip("/")).resolve()
    root = device_root()
    if mapped != root and root not in mapped.parents:
        raise SystemExit(f"remote path escaped fake device: {path}")
    return mapped


def rewrite_remote_script(script: str) -> str:
    fake_bin = remote_bin()
    command_paths = {
        "/sbin/cfgmtd": fake_bin / "cfgmtd",
        "/bin/md5sum": fake_bin / "md5sum",
        "/sbin/md5sum": fake_bin / "md5sum",
        "/usr/bin/md5sum": fake_bin / "md5sum",
        "/bin/sh": fake_bin / "sh",
        "/bin/cp": fake_bin / "cp",
        "/bin/mv": fake_bin / "mv",
        "/bin/rm": fake_bin / "rm",
        "/bin/tar": fake_bin / "tar",
        "/usr/bin/tar": fake_bin / "tar",
        "/bin/gzip": fake_bin / "gzip",
        "/usr/bin/gzip": fake_bin / "gzip",
        "/usr/bin/pkill": fake_bin / "pkill",
        "/usr/sbin/pkill": fake_bin / "pkill",
        "/bin/pkill": fake_bin / "pkill",
        "/sbin/pkill": fake_bin / "pkill",
        "/usr/bin/crond": fake_bin / "crond",
        "/usr/sbin/crond": fake_bin / "crond",
        "/bin/crond": fake_bin / "crond",
        "/sbin/crond": fake_bin / "crond",
    }
    boundary = r"(?<![A-Za-z0-9_./-])"
    for original, replacement in sorted(command_paths.items(), key=lambda item: -len(item[0])):
        script = re.sub(boundary + re.escape(original) + r"(?=\s|[;|&<>\"']|$)", str(replacement), script)
    root = device_root()
    for prefix in ("/etc", "/tmp", "/var", "/proc"):
        script = re.sub(
            boundary + re.escape(prefix) + r"(?=/|\b)",
            str(root / prefix.lstrip("/")),
            script,
        )
    script = re.sub(boundary + re.escape("/dev/null") + r"(?=\s|[;|&<>\"']|$)", str(root / "dev/null"), script)
    # A deployed script can invoke another .sh file directly. Route those child
    # scripts back through this mapper so their shebang cannot escape to macOS
    # /bin/sh and every remote shell body still executes under dash.
    direct_script = re.compile(r'(?m)^([ \t]*)("[^"\n]+\.sh")([ \t]*(?:[^\n]*))$')
    script = direct_script.sub(
        lambda match: f"{match.group(1)}{shlex.quote(str(fake_bin / 'sh'))} {match.group(2)}{match.group(3)}",
        script,
    )
    return script


def remote_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{remote_bin()}:{environment.get('PATH', '')}"
    environment["UBNT_SSH_KEY_SOURCE"] = str(device_root() / "etc/persistent/config/raspi_rsa_id.pub")
    environment["UBNT_AUTHORIZED_KEYS"] = str(device_root() / "etc/dropbear/authorized_keys")
    return environment


def run_dash_script(script: str, arguments: list[str], label: str) -> int:
    rewritten = rewrite_remote_script(script)
    temporary_dir = device_root() / "tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    descriptor, script_path = tempfile.mkstemp(prefix="fake-remote-", suffix=".sh", dir=temporary_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rewritten)
        syntax = subprocess.run(["/bin/dash", "-n", script_path], env=remote_environment(), check=False)
        append_event("dash-n", label, syntax.returncode)
        if syntax.returncode != 0:
            return syntax.returncode
        completed = subprocess.run(["/bin/dash", script_path, *arguments], env=remote_environment(), check=False)
        return completed.returncode
    finally:
        Path(script_path).unlink(missing_ok=True)


def strip_ssh_options(arguments: list[str]) -> tuple[str, list[str]]:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-o":
            index += 2
        elif argument.startswith("-"):
            index += 1
        else:
            break
    if index >= len(arguments):
        raise SystemExit("fake ssh missing target")
    return arguments[index], arguments[index + 1 :]


def fake_ssh(arguments: list[str]) -> int:
    target, command = strip_ssh_options(arguments)
    append_event("ssh", target, " ".join(command))
    if not command:
        raise SystemExit("interactive fake ssh is forbidden")
    if len(command) == 1:
        status = run_dash_script(command[0], [], "ssh-command")
        if status == 0 and os.environ.get("FAKE_MUTATE_AFTER_PREPARE") == "1" and "'prepare'" in command[0]:
            changed = device_root() / "etc/persistent/profile"
            with changed.open("a", encoding="utf-8") as handle:
                handle.write("\n# changed after preflight\n")
            append_event("test-mutation", changed)
        return status

    shell_name = command[0]
    if shell_name not in ("sh", "/bin/sh") or "-s" not in command:
        return run_dash_script(" ".join(shlex.quote(item) for item in command), [], "ssh-argv")
    dash_arguments = command[command.index("-s") + 1 :]
    if dash_arguments and dash_arguments[0] == "--":
        dash_arguments = dash_arguments[1:]
    return run_dash_script(sys.stdin.read(), dash_arguments, "ssh-stdin")


def scp_operands(arguments: list[str]) -> list[str]:
    operands: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-o":
            index += 2
        elif argument.startswith("-"):
            index += 1
        else:
            operands.append(argument)
            index += 1
    return operands


def split_remote(operand: str) -> tuple[str, str] | None:
    if ":" not in operand:
        return None
    target, path = operand.split(":", 1)
    if not path.startswith("/"):
        return None
    return target, path


def copy_item(source: Path, destination: Path) -> None:
    if source.is_dir():
        target = destination / source.name if destination.is_dir() else destination
        shutil.copytree(source, target, copy_function=shutil.copy2)
    else:
        target = destination / source.name if destination.is_dir() else destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def fake_scp(arguments: list[str]) -> int:
    operands = scp_operands(arguments)
    if len(operands) < 2:
        raise SystemExit("fake scp needs source and destination")
    sources = operands[:-1]
    destination = operands[-1]
    all_text = " ".join(operands)
    failure_match = os.environ.get("FAKE_SCP_FAIL_MATCH")
    if failure_match and failure_match in all_text:
        append_event("scp-fail", failure_match, all_text)
        return 73

    remote_sources = [split_remote(source) for source in sources]
    remote_destination = split_remote(destination)
    if remote_destination and not any(remote_sources):
        destination_path = map_remote_path(remote_destination[1])
        destination_path.mkdir(parents=True, exist_ok=True)
        for source in sources:
            copy_item(Path(source), destination_path)
        append_event("scp-upload", destination, *sources)
        return 0
    if not remote_destination and all(remote_sources):
        destination_path = Path(destination)
        if len(sources) > 1:
            destination_path.mkdir(parents=True, exist_ok=True)
        for source in remote_sources:
            assert source is not None
            copy_item(map_remote_path(source[1]), destination_path)
        if os.environ.get("FAKE_SCP_CORRUPT") == "1":
            archive = destination_path / "pruned-rollbacks.tar.gz"
            if archive.exists():
                with archive.open("ab") as handle:
                    handle.write(b"corrupt-test-transfer")
        append_event("scp-download", destination, *sources)
        return 0
    raise SystemExit(f"unsupported fake scp direction: {operands}")


def fake_date(arguments: list[str]) -> int:
    if arguments != ["-u", "+%Y%m%dT%H%M%SZ"]:
        completed = subprocess.run(["/bin/date", *arguments], check=False)
        return completed.returncode
    counter_path = Path(required_env("FAKE_CASE_ROOT")) / "date-counter"
    try:
        count = int(counter_path.read_text(encoding="ascii")) + 1
    except FileNotFoundError:
        count = 1
    counter_path.write_text(str(count), encoding="ascii")
    timestamp = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=count)
    print(timestamp.strftime("%Y%m%dT%H%M%SZ"))
    return 0


def fake_mktemp(arguments: list[str]) -> int:
    make_directory = "-d" in arguments
    template = next((argument for argument in reversed(arguments) if "X" in argument), None)
    default_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
    base = default_base
    prefix = "tmp."
    if template:
        template_path = Path(template)
        template_name = template_path.name
        prefix = template_name.split("X", 1)[0]
        if template_path.parent != Path("/tmp"):
            base = template_path.parent.resolve()
    base.mkdir(parents=True, exist_ok=True)
    if make_directory:
        print(tempfile.mkdtemp(prefix=prefix, dir=base))
    else:
        descriptor, path = tempfile.mkstemp(prefix=prefix, dir=base)
        os.close(descriptor)
        print(path)
    return 0


def tree_digest(path_text: str) -> int:
    root = Path(path_text)
    digest = hashlib.sha256()
    if not root.exists():
        print("missing")
        return 0
    for path in sorted([root, *root.rglob("*")], key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        stat_result = path.lstat()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(oct(stat_result.st_mode & 0o7777).encode("ascii") + b"\0")
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F" + path.read_bytes())
        elif path.is_dir():
            digest.update(b"D")
        else:
            digest.update(b"O")
        digest.update(b"\0")
    print(digest.hexdigest())
    return 0


def remote_shell(arguments: list[str]) -> int:
    if not arguments:
        return run_dash_script(sys.stdin.read(), [], "nested-stdin")
    if arguments[0] == "-n":
        completed = subprocess.run(["/bin/dash", *arguments], env=remote_environment(), check=False)
        append_event("dash-n", "nested-file", completed.returncode)
        return completed.returncode
    if arguments[0] == "-c":
        shell_arguments = arguments[2:] if len(arguments) > 2 else []
        return run_dash_script(arguments[1], shell_arguments, "nested-command")
    if arguments[0] == "-s":
        shell_arguments = arguments[1:]
        if shell_arguments and shell_arguments[0] == "--":
            shell_arguments = shell_arguments[1:]
        return run_dash_script(sys.stdin.read(), shell_arguments, "nested-stdin")
    script_path = Path(arguments[0])
    append_event("sh-exec", script_path)
    return run_dash_script(script_path.read_text(encoding="utf-8"), arguments[1:], str(script_path))


def main() -> int:
    invoked_as = Path(sys.argv[0]).name
    arguments = sys.argv[1:]
    if invoked_as == "ssh":
        return fake_ssh(arguments)
    if invoked_as == "scp":
        return fake_scp(arguments)
    if invoked_as == "date":
        return fake_date(arguments)
    if invoked_as == "mktemp":
        return fake_mktemp(arguments)
    if arguments and arguments[0] == "tree-digest":
        return tree_digest(arguments[1])
    if arguments and arguments[0] == "remote-sh":
        return remote_shell(arguments[1:])
    raise SystemExit(f"unknown fake transport invocation: {invoked_as} {arguments}")


if __name__ == "__main__":
    raise SystemExit(main())
