"""IPTorrents watcher: new 1080p x264 episodes for the shows Jacob is watching.

The want list comes from the video library (shows played recently or with
episodes left) plus a manual list; a timed check searches the site with the
stored login cookies and keeps candidates; one click fetches the .torrent into
qBittorrent's watch folder, where the download then flows through the media
organizer.  ``python3 -P -m pi.apps.iptorrents_watch --help`` lists commands.
"""
