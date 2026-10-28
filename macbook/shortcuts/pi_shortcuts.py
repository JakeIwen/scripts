"""Names, commands and installation-specific settings for the Pi Shortcuts."""
from dataclasses import dataclass
from pathlib import Path


HELPER_NAME = "Pi"
HELPER_IDENTIFIER = "C9BA39E0-8849-4AAC-AF2A-831A0BE408E7"
LAN_PREFIX = "192.168.6."
LAN_HOST = "192.168.6.103"
TAILSCALE_HOST = "100.82.91.76"
SSH_USER = "pi"
CLIENT_VERSION = "4711"
MINIMUM_CLIENT_VERSION = 900
COMMAND_COLOR = 2846468607
HELPER_COLOR = 4282601983
COMMAND_GLYPH = 59771
BROWN_NOISE_GLYPH = 59860
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "build" / "shortcuts"


@dataclass(frozen=True)
class CommandShortcut:
    name: str
    command: str
    glyph: int = COMMAND_GLYPH
    show_result: bool = False


COMMAND_SHORTCUTS = (
    CommandShortcut("Brown Noise", "sh ~/sns.sh brown_noise", glyph=BROWN_NOISE_GLYPH),
    CommandShortcut("Pink Noise", "sh ~/sns.sh pink_noise"),
    CommandShortcut("Discover Wkly", "sh ~/sns.sh discover_weekly"),
    CommandShortcut("PartyMode", "sh ~/sns.sh partymode"),
    CommandShortcut("Unjoin All", "sh ~/sns.sh unjoin_all"),
    CommandShortcut("Sonos Tasks", "sh ~/sns.sh --help", show_result=True),
    CommandShortcut("Disks On", "~/scripts/policyctl disks on"),
    CommandShortcut("Disks Off", "~/scripts/policyctl disks off"),
    CommandShortcut("Torrents On", "~/scripts/policyctl torrents on"),
    CommandShortcut("Torrents Off", "~/scripts/policyctl torrents off"),
    CommandShortcut("slink on", "s toggle_starlink on"),
    CommandShortcut("slink off", "s toggle_starlink off"),
    CommandShortcut("toggle flood", "s tuya_toggle ext_flood"),
    CommandShortcut("rball", "s rball"),
)
