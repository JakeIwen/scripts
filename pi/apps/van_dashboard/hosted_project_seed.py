"""One-time migration of the former React project catalog."""

from .hosted_project_report import Host, HostedProject, LinkKind, Source, Status


def pi_links(port, path="/"):
    return [
        {"kind": LinkKind.LAN, "label": "LAN", "url": f"http://vanpi.lan:{port}{path}"},
        {"kind": LinkKind.TS, "label": "Tailscale", "url": f"http://100.82.91.76:{port}{path}"},
    ]


def local_link(port):
    return {"kind": LinkKind.LOCAL, "label": "This Mac", "url": f"http://127.0.0.1:{port}/"}


# This is migration data, not a second directory: later loads use StateStore only.
CATALOG = (
    ("always-remote", "Always Remote", Host.PI,
     "Remote session management on vanpi.", pi_links(8777)),
    ("health-calendar", "Health Calendar", Host.MAC,
     "Local health calendar.", [local_link(4328)]),
    ("lionfeather-dev", "LionFeather Dev Page", Host.MAC,
     "Local LionFeather development site.",
     [local_link(5174), {"kind": LinkKind.WEB, "label": "Web*", "url": "https://lionfeather.dev/"}]),
    ("promaster-library", "ProMaster Service Library", Host.MAC,
     "Search the 2022 ProMaster service documentation and diagrams.", [local_link(8766)]),
    ("fieldwork", "Fieldwork", Host.MAC, "Job-search workspace.", [local_link(4317)]),
    ("visual-guides", "Visual Guides", Host.PI,
     "Wiring, setup and reference guides.", [local_link(8792), *pi_links(8791)]),
    ("telemetry", "Vehicle Telemetry", Host.PI,
     "Vehicle readings, drive data, and diagnostics.", pi_links(8765)),
    ("network-history", "Network History", Host.PI,
     "Network flight recorder: connectivity history, incidents, and evidence.",
     pi_links(8788, "/#network-history?hours=6")),
    ("audiobooks", "Audiobooks", Host.PI,
     "Browse the audiobook library and play through Sonos.", pi_links(8787)),
    ("movies-tv", "Movies & TV", Host.PI,
     "Browse, play, and resume movies and shows.", pi_links(8789)),
    ("home-assistant", "Home Assistant", Host.PI,
     "Smart-home devices, automations, and settings.", pi_links(8123)),
    ("soundbytes", "Sound Library", Host.PI,
     "Browse the sound files hosted on the Pi.", pi_links(8000)),
)


def seed_projects(existing_names, timestamp) -> list[HostedProject]:
    return [
        {
            "id": f"seed-{catalog_id}", "key": None, "name": name,
            "host": host, "description": description,
            "links": [dict(link) for link in links],
            "status": Status.LISTED, "source": Source.MANUAL, "live": True,
            "first_seen": None, "last_seen": None, "updated_at": timestamp,
        }
        for catalog_id, name, host, description, links in CATALOG
        if name.casefold() not in existing_names
    ]
