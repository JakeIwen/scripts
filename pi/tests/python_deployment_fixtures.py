"""Minimal package trees for deployment transaction tests."""

FIXTURE_MODULES = {
    "pi/apps/iptorrents_watch": ("__init__.py", "__main__.py", "cli.py", "config.py", "records.py"),
    "pi/apps/media_organizer": ("__init__.py", "__main__.py", "cli.py", "config.py", "records.py"),
    "pi/apps/audiobooks": ("__main__.py", "audiobook_server.py"),
    "pi/apps/bme280": ("__main__.py", "bme280_mqtt.py", "bme280_testread.py"),
    "pi/apps/van_dashboard": (
        "__main__.py",
        "van_dashboard.py",
        "van_dashboard_projects.py",
    ),
    "pi/apps/van_dashboard/routes": ("__init__.py", "common.py", "projects.py"),
    "pi/apps/video_library": ("__main__.py", "video_library_server.py"),
    "pi/apps/video_library/players": ("vlc_player.py", "sonos_volume.py"),
    "pi/scripts/python": ("ip_info.py", "vlc_property.py"),
    "shared/python": ("__init__.py", "shared_tool.py", "sonos_tasks.py"),
}
