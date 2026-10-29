"""Media organizer: sorts finished torrents out of ``torrent/New``.

Scan lists what sits in New and which qBittorrent torrent owns it, propose
asks a classifier (Codex on the Pi, or an offline rule) where each item
belongs, apply moves accepted rows through qBittorrent's set-location call
(plain renames for untracked items), journals every move so a run can be
undone, mirrors the renames into the backup copy, then rebuilds the cleaned
``links/`` tree.  ``python3 -P -m pi.apps.media_organizer --help`` lists the
commands; the dashboard's Movies & TV tile and qBittorrent's completion hook
call the same CLI.
"""
