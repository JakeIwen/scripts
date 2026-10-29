#!/bin/bash
# qBittorrent "run on completion" entry: sort the finished download out of
# torrent/New (Codex proposal, auto-apply of confident rows) and rebuild the
# links tree.  The organizer serializes on /run/lock/alias-media.lock, so a
# batch of completions queues; the work is detached so qBittorrent never
# waits on the classifier, and the hook never fails the torrent.
export PYTHONDONTWRITEBYTECODE=1
package_root=/home/pi/scripts/python-packages/current
case ":${PYTHONPATH:-}:" in
  *":$package_root:"*) ;;
  *) export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$package_root" ;;
esac
mkdir -p /home/pi/log
(
  cd /home/pi || exit 1
  echo "$(date '+%F %T') post-download start"
  python3 -P -m pi.apps.media_organizer post-download
  echo "$(date '+%F %T') post-download exit=$?"
) </dev/null >>/home/pi/log/media_organizer_hook.log 2>&1 &
disown "$!" 2>/dev/null || true
echo "media organizer post-download scheduled"
