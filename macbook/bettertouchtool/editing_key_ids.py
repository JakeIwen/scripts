"""Stable Cmd-V identities shared by the editing-key and Finder installers."""
import uuid

NAMESPACE = uuid.UUID('5d0f6a3e-2b8c-4f71-9e2a-7c4b1d9e6f30')
FINDER_PASTE_ID = 'EDA649C3-026B-4402-9BF9-CE339233EFB2'
PASTE_LINE_ID = str(uuid.uuid5(NAMESPACE, 'paste line')).upper()
# Finder file focus and writable text focus are disjoint; no other pair is exempt.
DISJOINT_TRIGGER_PAIRS = (frozenset({FINDER_PASTE_ID, PASTE_LINE_ID}),)
