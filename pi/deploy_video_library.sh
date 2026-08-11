#!/usr/bin/env bash
set -euo pipefail

# Deliberately scoped deployment for media development clones.  The repository-
# wide sync also publishes ignored secrets, Samba configuration, and compute
# assets, so it must continue to run only from the primary trusted checkout.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
target="${VAN_VIDEO_DEPLOY_TARGET:-pi@vanpi.lan}"
mode=deploy
rollback_ref="${VAN_VIDEO_ROLLBACK_REF:-5168ce5}"

case "${1:-}" in
  "") ;;
  --rollback)
    [[ $# -eq 1 ]] || { echo "usage: $0 [--rollback | --target user@host]" >&2; exit 2; }
    mode=rollback
    ;;
  --target)
    [[ $# -eq 2 && -n "$2" ]] || { echo "usage: $0 [--rollback | --target user@host]" >&2; exit 2; }
    target=$2
    ;;
  *) echo "usage: $0 [--rollback | --target user@host]" >&2; exit 2 ;;
esac

baseline_files=(
  pi/apps/video_library/video_library_server.py
  pi/apps/video_library/templates/video_library.html
  pi/apps/video_library/static/video_library.js
  pi/apps/video_library/static/video_library.css
  pi/services/video-library.service
  shared/python/sonos_tasks.py
  pi/.bashrc
)
v2_files=(
  "${baseline_files[@]}"
  pi/scripts/alias_media.sh
  pi/apps/video_library/video_asset_catalog.py
  pi/apps/video_library/video_qbittorrent.py
)

if [[ "$mode" == rollback ]]; then
  git -C "$repo_root" cat-file -e "$rollback_ref^{commit}"
  for relative in "${baseline_files[@]}"; do
    git -C "$repo_root" cat-file -e "$rollback_ref:$relative" || {
      echo "rollback ref lacks required media file: $relative" >&2
      exit 1
    }
  done
else
  for relative in "${v2_files[@]}"; do
    [[ -r "$repo_root/$relative" ]] || {
      echo "required media deployment file is missing: $relative" >&2
      exit 1
    }
  done
fi

local_stage="$(mktemp -d "${TMPDIR:-/tmp}/van-video-deploy.XXXXXX")"
remote_stage="/tmp/systemd-tmp.$$"
mux="-o ControlMaster=auto -o ControlPath=$local_stage/ssh-%C -o ControlPersist=120"

cleanup() {
  rm -rf -- "$local_stage"
}
trap cleanup EXIT

mkdir -p \
  "$local_stage/services" \
  "$local_stage/scripts/python-automation/templates" \
  "$local_stage/scripts/python-automation/static"

stage_file() {
  local relative=$1 destination=$2
  if [[ "$mode" == rollback ]]; then
    git -C "$repo_root" show "$rollback_ref:$relative" > "$destination"
  else
    cp "$repo_root/$relative" "$destination"
  fi
}

stage_file pi/services/video-library.service "$local_stage/services/video-library.service"
# Keep the data-safe, detached alias builder in both modes.  Its v2 loopback
# notification is best-effort and inert against the rollback server; restoring
# the pinned version would also restore its destructive RAR payload cleanup.
cp "$repo_root/pi/scripts/alias_media.sh" "$local_stage/scripts/alias_media.sh"
stage_file pi/apps/video_library/video_library_server.py \
  "$local_stage/scripts/python-automation/video_library_server.py"
stage_file shared/python/sonos_tasks.py \
  "$local_stage/scripts/python-automation/sonos_tasks.py"
if [[ "$mode" == deploy ]]; then
  stage_file pi/apps/video_library/video_asset_catalog.py \
    "$local_stage/scripts/python-automation/video_asset_catalog.py"
  stage_file pi/apps/video_library/video_qbittorrent.py \
    "$local_stage/scripts/python-automation/video_qbittorrent.py"
fi
stage_file pi/apps/video_library/templates/video_library.html \
  "$local_stage/scripts/python-automation/templates/video_library.html"
stage_file pi/apps/video_library/static/video_library.js \
  "$local_stage/scripts/python-automation/static/video_library.js"
stage_file pi/apps/video_library/static/video_library.css \
  "$local_stage/scripts/python-automation/static/video_library.css"
stage_file pi/.bashrc "$local_stage/home-bashrc"

ssh $mux "$target" true

# The online backup is idempotent and contains the untouched v1 tables.  It is
# created before any v2 module is installed or the service is restarted.
if [[ "$mode" == deploy ]]; then
ssh $mux "$target" /usr/bin/python3 - <<'PY'
import hashlib
import hmac
import os
import re
import sqlite3
import tempfile
from pathlib import Path

source = Path("~/.local/share/van-video-library/progress.sqlite3").expanduser().resolve()
suffix = source.suffix or ".sqlite3"
stem = source.name[: -len(source.suffix)] if source.suffix else source.name
# Keep this derivation identical to video_asset_catalog._backup_path().
backup = source.with_name(f"{stem}.pre-v2{suffix}")
checksum = backup.with_name(backup.name + ".sha256")


def quick_check(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    finally:
        connection.close()
    if len(rows) != 1 or str(rows[0][0]).casefold() != "ok":
        details = "; ".join(str(row[0]) for row in rows) or "no result"
        raise RuntimeError(f"SQLite quick_check failed for {path}: {details}")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fsync_path(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_existing_pair():
    if backup.exists() != checksum.exists():
        raise RuntimeError(
            f"incomplete pre-v2 backup pair; refusing deployment: {backup}, {checksum}"
        )
    if not backup.exists():
        return False
    expected = checksum.read_text(encoding="ascii").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise RuntimeError(f"invalid pre-v2 checksum file: {checksum}")
    actual = sha256(backup)
    if not hmac.compare_digest(expected, actual):
        raise RuntimeError(f"pre-v2 backup checksum mismatch: {backup}")
    quick_check(backup)
    os.chmod(backup, 0o600)
    os.chmod(checksum, 0o600)
    fsync_path(backup)
    fsync_path(checksum)
    fsync_directory(backup.parent)
    return True


if not source.is_file():
    raise FileNotFoundError(f"legacy video progress database is missing: {source}")
quick_check(source)
if verify_existing_pair():
    raise SystemExit(0)

snapshot_fd, snapshot_name = tempfile.mkstemp(
    prefix=f".{backup.name}.", suffix=".tmp", dir=str(backup.parent)
)
os.close(snapshot_fd)
checksum_fd, checksum_name = tempfile.mkstemp(
    prefix=f".{checksum.name}.", suffix=".tmp", dir=str(checksum.parent)
)
os.close(checksum_fd)
snapshot_temporary = Path(snapshot_name)
checksum_temporary = Path(checksum_name)
try:
    source_db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    target_db = sqlite3.connect(str(snapshot_temporary))
    try:
        source_db.backup(target_db)
        target_db.commit()
    finally:
        target_db.close()
        source_db.close()
    quick_check(snapshot_temporary)
    os.chmod(snapshot_temporary, 0o600)
    fsync_path(snapshot_temporary)

    checksum_temporary.write_text(
        sha256(snapshot_temporary) + "\n", encoding="ascii"
    )
    os.chmod(checksum_temporary, 0o600)
    fsync_path(checksum_temporary)

    os.replace(snapshot_temporary, backup)
    os.replace(checksum_temporary, checksum)
    fsync_directory(backup.parent)
finally:
    for temporary in (snapshot_temporary, checksum_temporary):
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

if not verify_existing_pair():
    raise RuntimeError("pre-v2 backup was not installed")
PY
fi

ssh $mux "$target" "mkdir -p '$remote_stage'"
scp $mux -r \
  "$local_stage/services" \
  "$local_stage/scripts" \
  "$local_stage/home-bashrc" \
  "$target:$remote_stage/"

# Install only the explicit media manifest.  In particular, do not invoke the
# repository-wide updater here: it also owns unrelated scripts and cleanup.
ssh $mux "$target" bash -s -- "$remote_stage" <<'INSTALL'
set -eu
stage=$1
case "$stage" in
  /tmp/systemd-tmp.[0-9]*) ;;
  *) echo "refusing unsafe media staging path: $stage" >&2; exit 1 ;;
esac
cleanup() {
  rm -rf -- "$stage"
}
trap cleanup EXIT

python_live=/home/pi/scripts/python-automation
/usr/bin/install -d -m 0755 \
  "$python_live/templates" \
  "$python_live/static"
/usr/bin/install -m 0755 "$stage/scripts/alias_media.sh" \
  /home/pi/scripts/alias_media.sh
/usr/bin/install -m 0644 "$stage/scripts/python-automation/video_library_server.py" \
  "$python_live/video_library_server.py"
/usr/bin/install -m 0644 "$stage/scripts/python-automation/sonos_tasks.py" \
  "$python_live/sonos_tasks.py"
for module in video_asset_catalog.py video_qbittorrent.py; do
  if [ -f "$stage/scripts/python-automation/$module" ]; then
    /usr/bin/install -m 0644 "$stage/scripts/python-automation/$module" \
      "$python_live/$module"
  fi
done
/usr/bin/install -m 0644 \
  "$stage/scripts/python-automation/templates/video_library.html" \
  "$python_live/templates/video_library.html"
/usr/bin/install -m 0644 \
  "$stage/scripts/python-automation/static/video_library.js" \
  "$python_live/static/video_library.js"
/usr/bin/install -m 0644 \
  "$stage/scripts/python-automation/static/video_library.css" \
  "$python_live/static/video_library.css"
/usr/bin/cmp -s "$stage/home-bashrc" /home/pi/.bashrc || \
  /usr/bin/install -m 0644 "$stage/home-bashrc" /home/pi/.bashrc
sudo /usr/bin/install -m 0644 "$stage/services/video-library.service" \
  /etc/systemd/system/video-library.service
sudo /usr/bin/systemctl daemon-reload
sudo /usr/bin/systemctl reset-failed video-library.service
sudo /usr/bin/systemctl restart video-library.service
INSTALL

ssh $mux "$target" bash -s <<'HEALTH'
set -eu
/usr/bin/systemctl --no-pager --full is-active video-library.service

attempt=1
while [ "$attempt" -le 15 ]; do
  if /usr/bin/curl -fsS --connect-timeout 1 --max-time 2 \
      http://127.0.0.1:8789/api/status >/dev/null; then
    exit 0
  fi
  if [ "$attempt" -eq 15 ]; then
    echo "video-library API did not become healthy after 15 attempts" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 1
done
HEALTH

echo "Media-only $mode completed on $target"
