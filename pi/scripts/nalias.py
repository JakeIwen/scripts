#!/usr/bin/env python3
"""Create or update an alias in the Pi's ~/.bashrc and in the scripts repo.

The Mac's broad sync installs the scripts repo's pi/.bashrc over ~/.bashrc, so
an alias added only here would be lost on the next sync. This commits the same
edit to pi/.bashrc on GitHub first, through a compare-and-swap on the file's
blob SHA, and installs it only once that commit exists. Anything that leaves
the two copies in doubt aborts before either one changes.

Usage: nalias NAME COMMAND        (the .bashrc function reloads the shell)
       nalias.py --dry-run NAME COMMAND
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "JakeIwen/scripts"
REPO_PATH = "pi/.bashrc"
BRANCH = "master"
SECTION_HEADER = "### NALIAS ### (added by nalias; defined last, so no function above expands them)"
NAME_PATTERN = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.:+-]*")
GH_TIMEOUT_SECONDS = 30
BASH = "/bin/bash"
COMPARE_HINT = (f"compare: gh api -H 'Accept: application/vnd.github.raw' "
                f"repos/{REPO}/contents/{REPO_PATH}?ref={BRANCH} | diff - ~/.bashrc")


class Abort(Exception):
    """A reason to stop with ~/.bashrc and GitHub both unchanged."""


def blob_sha(data):
    """Git's object id for these bytes, as GitHub reports a file's sha."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def gh_api(path, method="GET", body=None):
    arguments = ["gh", "api", path]
    if body is not None:
        arguments += ["-X", method, "--input", "-"]
    try:
        result = subprocess.run(
            arguments, input=None if body is None else json.dumps(body).encode(),
            capture_output=True, timeout=GH_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise Abort("gh is not installed")
    except subprocess.TimeoutExpired:
        raise Abort(f"GitHub didn't answer within {GH_TIMEOUT_SECONDS} s")
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip() or "no error output"
        raise Abort(f"gh api {method} {path} failed: {detail}")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise Abort(f"gh api {method} {path} returned something other than JSON")


def remote_sha(branch=BRANCH):
    return gh_api(f"repos/{REPO}/contents/{REPO_PATH}?ref={branch}")["sha"]


def commit_file(old_sha, data, message, branch=BRANCH):
    """Commit data as pi/.bashrc only if the file is still old_sha there."""
    body = {"message": message, "content": base64.b64encode(data).decode(),
            "sha": old_sha, "branch": branch}
    response = gh_api(f"repos/{REPO}/contents/{REPO_PATH}", "PUT", body)
    return response.get("content", {}).get("sha"), response.get("commit", {})


def quote(command):
    return "'" + command.replace("'", "'\\''") + "'"


def with_alias(text, name, command):
    """Return the new rc text and what was done, as the Mac's nalias reports it."""
    new_line = f"alias {name}={quote(command)}"
    lines = text.split("\n")
    existing = [index for index, line in enumerate(lines) if line.startswith(f"alias {name}=")]
    if existing:
        report = [f"found alias, replaced: {lines[index]}" for index in existing]
        for index in existing:
            lines[index] = new_line
        return "\n".join(lines), report + [f"with: {new_line}"]
    # Appended aliases stay at the end of the file, after every function, so
    # none of them expands inside a function body read earlier.
    headers = [line.rstrip() for line in lines if line.startswith("### ")]
    tail = "" if not text or text.endswith("\n") else "\n"
    if not headers or headers[-1] != SECTION_HEADER:
        tail += f"\n{SECTION_HEADER}\n"
    return text + tail + new_line + "\n", [f"appending to rc: {new_line}"]


def check_syntax(data):
    with tempfile.NamedTemporaryFile(prefix="nalias-", suffix=".bashrc") as candidate:
        candidate.write(data)
        candidate.flush()
        result = subprocess.run([BASH, "-n", candidate.name], capture_output=True, text=True)
    if result.returncode:
        raise Abort(f"the edited file doesn't parse: {result.stderr.strip()}")


def read_live(path):
    if path.is_symlink() or not path.is_file():
        raise Abort(f"{path} isn't a regular file")
    return path.read_bytes()


def install(path, expected, data):
    """Replace path with data atomically, unless it changed since it was read."""
    if path.read_bytes() != expected:
        raise RuntimeError(f"{path} changed while nalias ran")
    descriptor, temporary = tempfile.mkstemp(prefix=".bashrc.nalias-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def parse_arguments(argv):
    parser = argparse.ArgumentParser(
        prog="nalias", description="Create or update an alias in ~/.bashrc and in "
        f"{REPO_PATH} on GitHub ({REPO}, public).")
    parser.add_argument("--dry-run", action="store_true",
                        help="check both copies and show the edit, changing nothing")
    parser.add_argument("name")
    parser.add_argument("command")
    arguments = parser.parse_args(argv)
    if not NAME_PATTERN.fullmatch(arguments.name):
        parser.error(f"not an alias name: {arguments.name!r}")
    if not arguments.command or "\n" in arguments.command or "\0" in arguments.command:
        parser.error("the command must be one non-empty line")
    return arguments


def run(arguments):
    live_path = Path.home() / ".bashrc"
    live = read_live(live_path)
    try:
        text, report = with_alias(live.decode(), arguments.name, arguments.command)
    except UnicodeDecodeError:
        raise Abort(f"{live_path} isn't UTF-8")
    data = text.encode()
    if data == live:
        print(f"alias {arguments.name} is already {quote(arguments.command)}; nothing changed")
        return 0
    check_syntax(data)
    base = remote_sha()
    if base != blob_sha(live):
        raise Abort(f"~/.bashrc differs from {REPO_PATH} on GitHub {BRANCH} (a commit not yet "
                    f"synced here, or an edit made on the Pi); {COMPARE_HINT}")
    if arguments.dry_run:
        print("\n".join(report))
        print(f"dry run: ~/.bashrc matches {REPO} {BRANCH}; would commit the edit there")
        return 0
    message = f"Pi bashrc: nalias {arguments.name}\n\nalias {arguments.name}={quote(arguments.command)}\n"
    committed_sha, commit = commit_file(base, data, message)
    location = commit.get("html_url") or commit.get("sha", "")
    if committed_sha != blob_sha(data):
        print(f"nalias: GitHub stored different bytes than sent ({location}); "
              f"~/.bashrc was left as it was. {COMPARE_HINT}", file=sys.stderr)
        return 2
    try:
        install(live_path, live, data)
    except (OSError, RuntimeError) as error:
        print(f"nalias: committed {location}, but couldn't install it: {error}. "
              f"~/.bashrc is now behind GitHub; {COMPARE_HINT}", file=sys.stderr)
        return 2
    print("\n".join(report))
    print(f"committed to {REPO} {BRANCH} (public): {location}")
    return 0


def main(argv=None):
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        return run(arguments)
    except Abort as reason:
        print(f"nalias aborted, nothing changed: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
