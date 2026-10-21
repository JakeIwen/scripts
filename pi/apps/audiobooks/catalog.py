"""Audiobook records, filesystem discovery helpers, and fuzzy matching rules."""
import os
import re
from difflib import SequenceMatcher
from hashlib import sha1

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".wav", ".aiff"}
DISC_RE = re.compile(r"^(dis[ck]|cd|dvd|part|pt|side|session)[\s._-]*\d+$", re.I)
NUMWORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "i": "1", "ii": "2", "iii": "3", "iv": "4",
}


def norm_tokens(text):
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[’']", "", text)
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    return [NUMWORDS.get(token, token) for token in tokens]


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


class Book:
    def __init__(self, relative_path, tracks):
        self.rel = relative_path
        self.bid = sha1(relative_path.encode()).hexdigest()[:12]
        self.tracks = tracks
        self.name = re.sub(r"\.[^.]+$", "", relative_path.replace("/", " - "))
        self.tokens = norm_tokens(self.name)

    def as_dict(self, progress=None):
        value = {"id": self.bid, "name": self.name, "rel": self.rel,
                 "tracks": len(self.tracks)}
        if progress:
            value["progress"] = progress
        return value


def audio_files(directory):
    found = []
    for root, directories, files in os.walk(directory):
        directories.sort(key=natural_key)
        directories[:] = [name for name in directories if not name.startswith(".")]
        for name in sorted(files, key=natural_key):
            if not name.startswith(".") and os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                found.append(os.path.join(root, name))
    return found


def token_similarity(query, candidate):
    if query == candidate:
        return 1.0
    if len(query) >= 3 and (candidate.startswith(query) or query.startswith(candidate)):
        return 0.92
    return SequenceMatcher(None, query, candidate).ratio()


def match_score(query_tokens, candidate_tokens):
    if not query_tokens or not candidate_tokens:
        return 0.0
    best = [max(token_similarity(query, candidate) for candidate in candidate_tokens)
            for query in query_tokens]
    matched_candidates = {
        candidate for query in query_tokens for candidate in candidate_tokens
        if token_similarity(query, candidate) >= 0.8
    }
    return 0.8 * (sum(best) / len(best)) + 0.2 * (len(matched_candidates) / len(candidate_tokens))
