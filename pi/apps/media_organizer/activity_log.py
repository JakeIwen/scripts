"""One-line activity entries in the organizer log (no payload paths or secrets)."""
from . import config
from .storage import now


def log(step):
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.LOG_PATH.open('a', encoding='utf-8') as stream:
        stream.write(f'{now()} {step}\n')
