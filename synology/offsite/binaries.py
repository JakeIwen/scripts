"""Pinned upstream native binaries; install is explicit and architecture-specific."""
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request

VERSION = '0.14.0'
BASE = 'https://github.com/restic/rest-server/releases/download/v' + VERSION + '/'
# Official v0.14.0/SHA256SUMS, retrieved 2026-11-03. No generated guesses.
ARCHIVES = {
    'linux_amd64': '4c9c95bc079a0334e81fad379b19dc5c3353c71c2c88d652cafce2081c2b1c66',
    'linux_arm64': 'cef139cbe8b27b16bda731d17f093b0aa466b8c60b136c12d78b6f2bff3daf22',
    'linux_386': 'de12ba817a8153be2ba4cbe6ae51ad2ca4c26d8210084659c1f421839bdb49c7',
    'linux_armv7': '1b01b6a98b59d68ae8cf472189a92c2c17d0a49f8a3542fc986ea7a921e55742',
    'darwin_arm64': '057004ce24ca9edfbe0e391eae87981ab03f2abb6b65cf8fdfcddf847ded8656',
}
MACHINES = {'x86_64': 'amd64', 'aarch64': 'arm64', 'i686': '386', 'armv7l': 'armv7'}


def target_architecture():
    if platform.system() != 'Linux' or platform.machine() not in MACHINES:
        raise ValueError('Unsupported DSM OS/CPU; no binary installed')
    return 'linux_' + MACHINES[platform.machine()]


def fetch_verified(architecture, directory):
    name = 'rest-server_{}_{}.tar.gz'.format(VERSION, architecture)
    if architecture not in ARCHIVES:
        raise ValueError('Unsupported rest-server architecture')
    # A new empty directory is required; never extract paths from the archive.
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    with urllib.request.urlopen(BASE + name, timeout=60) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != ARCHIVES[architecture]:
        raise ValueError('rest-server archive SHA-256 mismatch')
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        matches = [member for member in archive.getmembers() if Path(member.name).name == 'rest-server']
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 64 * 1024 * 1024:
            raise ValueError('Unexpected rest-server release layout')
        binary = archive.extractfile(matches[0]).read()
    path = directory / 'rest-server'
    path.write_bytes(binary)
    path.chmod(0o700)
    return {'version': VERSION, 'architecture': architecture, 'archive_sha256': ARCHIVES[architecture],
            'binary_sha256': hashlib.sha256(binary).hexdigest()}


def verify(root):
    from .configuration import read
    record = read(root / 'binary.json')
    if (not isinstance(record, dict) or record.get('version') != VERSION
            or record.get('architecture') != target_architecture()
            or record.get('archive_sha256') != ARCHIVES.get(record.get('architecture'))):
        raise ValueError('Missing or wrong rest-server installation provenance')
    path = root / 'rest-server'
    if path.resolve() != path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record.get('binary_sha256'):
        raise ValueError('Installed rest-server binary differs from verified release')
    return path
