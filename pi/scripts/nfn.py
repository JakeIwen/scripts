#!/usr/bin/env python3
"""Create or update a function in the Pi's ~/.bashrc and in the scripts repo.

The Mac's broad sync installs the scripts repo's pi/.bashrc over ~/.bashrc, so
a function added only here would be lost on the next sync. This commits the same
edit to pi/.bashrc on GitHub first, through a compare-and-swap on the file's
blob SHA, and installs it only once that commit exists. Anything that leaves
the two copies in doubt aborts before either one changes.

Usage: nfn NAME COMMAND        (the .bashrc function reloads the shell)
       nfn.py --dry-run NAME COMMAND
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "JakeIwen/scripts"
REPO_PATH = "pi/.bashrc"
BRANCH = "master"
SECTION_HEADER = "### NFN ### (added by nfn)"
BOUNDARY_COMMENT = "# Codex command shells need helpers, not secrets, DISPLAY or command shadows."
BOUNDARY_GUARD = '[[ -n ${CODEX_THREAD_ID-} && $- != *i* ]] && return'
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


def clean_shell_environment():
    # BASH_ENV and exported functions must not influence command classification.
    return {"PATH": os.environ.get("PATH", os.defpath)}


def command_kind(name):
    result = subprocess.run(
        [BASH, "--norc", "--noprofile", "-c", 'type -t -- "$1"', "_", name],
        env=clean_shell_environment(), capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise Abort(f"couldn't classify {name}: {result.stderr.strip()}")
    return result.stdout.strip()


def function_line(name, command, kind):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        first = lexer.get_token()
    except ValueError as error:
        raise Abort(f"invalid command: {error}")
    if first == name:
        command = ("builtin " if kind == "builtin" else "command ") + command
    return f'{name}() {{ {command} "$@"; }}'


def boundary_index(lines):
    matches = [index for index, line in enumerate(lines) if line == BOUNDARY_COMMENT]
    if len(matches) != 1 or lines[matches[0] + 1:matches[0] + 2] != [BOUNDARY_GUARD]:
        raise Abort("couldn't find the unique Codex boundary; edit the file by hand")
    return matches[0]


def existing_definition(lines, name):
    escaped = re.escape(name)
    declaration = re.compile(
        rf"^\s*(?:{escaped}\s*\(\s*\)|function\s+{escaped}(?:\s|\(|$)|alias\s+{escaped}=)")
    existing = [index for index, line in enumerate(lines) if declaration.match(line)]
    if not existing:
        return None
    if len(existing) != 1:
        raise Abort(f"more than one definition of {name}; edit the file by hand")
    index = existing[0]
    single_line = re.fullmatch(rf"{escaped}\(\)\s*\{{.*\}}\s*(?:#.*)?", lines[index])
    if not single_line:
        raise Abort(f"{name} isn't a single-line NAME() {{ ... }} function; edit the file by hand")
    check_syntax((lines[index] + "\n").encode())
    # A single line may contain several definitions or trailing commands. Only
    # replace it when its first complete function consumes the whole line.
    for closing in re.finditer(r"\}", lines[index]):
        tail = lines[index][closing.end():]
        if not tail.strip():
            break
        prefix = lines[index][:closing.end()] + "\n"
        result = subprocess.run([BASH, "--norc", "--noprofile", "-n"], input=prefix,
                                capture_output=True, text=True, env=clean_shell_environment())
        if result.returncode == 0:
            if tail.lstrip().startswith("#"):
                break
            raise Abort(f"{name} shares its line with other definitions or commands; edit the file by hand")
    return index


def shadow_block_end(lines, boundary):
    starts = [index for index in range(boundary + 2, len(lines))
              if lines[index].startswith("alias sudo='sudo '")]
    if len(starts) != 1:
        raise Abort("couldn't find the shadowing-function block; edit the file by hand")
    index = end = starts[0] + 1
    while index < len(lines):
        if not lines[index].strip() or lines[index].lstrip().startswith("#"):
            index += 1
            continue
        if not re.match(r"^[A-Za-z0-9_.:+-]+\(\)\s*\{", lines[index]):
            break
        start = index
        # Let Bash find the closing brace: braces in quoted strings or nested
        # compound commands are not reliable textual delimiters.
        while index < len(lines):
            index += 1
            result = subprocess.run(
                [BASH, "--norc", "--noprofile", "-n"],
                input="\n".join(lines[start:index]) + "\n", capture_output=True,
                text=True, env=clean_shell_environment())
            if result.returncode == 0:
                end = index
                break
        else:
            raise Abort("the shadowing-function block doesn't parse; edit the file by hand")
    return end


def insert_function(lines, new_line, shadowing):
    boundary = boundary_index(lines)
    if shadowing:
        lines.insert(shadow_block_end(lines, boundary), new_line)
        return
    headers = [index for index, line in enumerate(lines) if line == SECTION_HEADER]
    if len(headers) > 1 or (headers and headers[0] >= boundary):
        raise Abort("the NFN section is ambiguous; edit the file by hand")
    if not headers:
        if boundary and lines[boundary - 1].strip():
            lines.insert(boundary, "")
            boundary += 1
        lines.insert(boundary, SECTION_HEADER)
        boundary += 1
    lines.insert(boundary, new_line)


def with_function(text, name, command):
    kind = command_kind(name)
    shadowing = kind in ("builtin", "keyword", "file")
    new_line = function_line(name, command, kind)
    lines = text.split("\n")
    boundary = boundary_index(lines)
    existing = existing_definition(lines, name)
    side = "below" if shadowing else "above"
    report = [f"placement: {side} the Codex boundary"]
    if existing is not None:
        old_line = lines[existing]
        report += [f"found function, replaced: {old_line}", f"with: {new_line}"]
        if (existing > boundary) == shadowing:
            lines[existing] = new_line
            return "\n".join(lines), report, new_line
        del lines[existing]
    else:
        report += [f"appending to rc: {new_line}"]
    insert_function(lines, new_line, shadowing)
    return "\n".join(lines), report, new_line


def check_syntax(data):
    with tempfile.NamedTemporaryFile(prefix="nfn-", suffix=".bashrc") as candidate:
        candidate.write(data)
        candidate.flush()
        result = subprocess.run([BASH, "-n", candidate.name], capture_output=True, text=True,
                                env=clean_shell_environment())
    if result.returncode:
        raise Abort(f"the edited file doesn't parse: {result.stderr.strip()}")


def read_live(path):
    if path.is_symlink() or not path.is_file():
        raise Abort(f"{path} isn't a regular file")
    return path.read_bytes()


def install(path, expected, data):
    """Replace path with data atomically, unless it changed since it was read."""
    if path.read_bytes() != expected:
        raise RuntimeError(f"{path} changed while nfn ran")
    descriptor, temporary = tempfile.mkstemp(prefix=".bashrc.nfn-", dir=path.parent)
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
        prog="nfn", description="Create or update a function in ~/.bashrc and in "
        f"{REPO_PATH} on GitHub ({REPO}, public).")
    parser.add_argument("--dry-run", action="store_true",
                        help="check both copies and show the edit, changing nothing")
    parser.add_argument("name")
    parser.add_argument("command")
    arguments = parser.parse_args(argv)
    if not NAME_PATTERN.fullmatch(arguments.name):
        parser.error(f"not a function name: {arguments.name!r}")
    if not arguments.command or "\n" in arguments.command or "\0" in arguments.command:
        parser.error("the command must be one non-empty line")
    return arguments


def run(arguments):
    live_path = Path.home() / ".bashrc"
    live = read_live(live_path)
    try:
        text, report, new_line = with_function(live.decode(), arguments.name, arguments.command)
    except UnicodeDecodeError:
        raise Abort(f"{live_path} isn't UTF-8")
    data = text.encode()
    if data == live:
        print("nothing changed")
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
    message = f"Pi bashrc: nfn {arguments.name}\n\n{new_line}\n"
    committed_sha, commit = commit_file(base, data, message)
    location = commit.get("html_url") or commit.get("sha", "")
    if committed_sha != blob_sha(data):
        print(f"nfn: GitHub stored different bytes than sent ({location}); "
              f"~/.bashrc was left as it was. {COMPARE_HINT}", file=sys.stderr)
        return 2
    try:
        install(live_path, live, data)
    except (OSError, RuntimeError) as error:
        print(f"nfn: committed {location}, but couldn't install it: {error}. "
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
        print(f"nfn aborted, nothing changed: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
