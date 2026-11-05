"""Read pins.env, the lane's one record of its image, tool versions and VM size."""
from dataclasses import dataclass
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PINS_FILE = HERE / 'pins.env'
# provision.sh sources the same file, so values may hold only shell-inert characters.
PIN_LINE = re.compile(r'([A-Z][A-Z0-9_]*)=([A-Za-z0-9._:/@%+,=-]+)')
REQUIRED = ('DEBIAN_IMAGE_URL', 'DEBIAN_IMAGE_DIGEST', 'LANE_USER', 'LANE_UID',
            'VM_CPUS', 'VM_MEMORY', 'VM_DISK')


@dataclass(frozen=True)
class LanePins:
    image_url: str
    image_digest: str
    user: str
    uid: int
    cpus: int
    memory: str
    disk: str

    @property
    def home(self):
        return '/home/' + self.user


def parse_pins(text, source='pins.env'):
    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = PIN_LINE.fullmatch(line)
        if not match:
            raise ValueError(f'{source}:{number}: expected KEY=value of shell-inert characters')
        if match[1] in values:
            raise ValueError(f'{source}:{number}: {match[1]} is pinned twice')
        values[match[1]] = match[2]
    missing = [key for key in REQUIRED if key not in values]
    if missing:
        raise ValueError(f'{source}: missing {", ".join(missing)}')
    if not values['DEBIAN_IMAGE_URL'].startswith('https://'):
        raise ValueError(f'{source}: DEBIAN_IMAGE_URL must be https')
    if not re.fullmatch(r'sha(256|512):[0-9a-f]+', values['DEBIAN_IMAGE_DIGEST']):
        raise ValueError(f'{source}: DEBIAN_IMAGE_DIGEST must be sha256:/sha512: hex')
    return LanePins(
        image_url=values['DEBIAN_IMAGE_URL'], image_digest=values['DEBIAN_IMAGE_DIGEST'],
        user=values['LANE_USER'], uid=int(values['LANE_UID']), cpus=int(values['VM_CPUS']),
        memory=values['VM_MEMORY'], disk=values['VM_DISK'])


def read_pins(path=PINS_FILE):
    return parse_pins(path.read_text(), str(path))
