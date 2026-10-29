"""The historical Perl title pipeline, in its original substitution order."""

from __future__ import annotations

import os
import re

# Release metadata is removed only after an underscore and before a boundary;
# preserving order avoids stripping words which merely contain one of these tags.
TAG_PATTERNS = (
    "multi", "PROPER", "iP", "REQ", "FGT", "EAC3", "SANTi", "MutzNutz",
    "ViSiON", "POOTLED", "COLLECTiVE", "TELESYNC", "Hi10p", "ETRG", "YTM_AM",
    "SKGTV", "HDR10", "UNCENSORED", "HDR", "CaLLiOpeD", "ddpatmos", "CtrlHD",
    "Will1869", r"10_?Bit", "DTS", "DL", "SDC", "hdtv", "EVO", "WiKi", "HMAX",
    "IMAX", "MA", "VhsRip", "HDRip", "BDRip", "iNTERNAL", "True_HD",
    r"1080[pi](MAX)?", "720p", "XviD", "HD", "AC3", "REPACK", "REMUX", "PRiCK",
    "AVC", "HC", "AMZN", "HULU", "1080pWEBRip", r"Blu(R|r)ay", r"(BR|web|WEB)(Rip)?",
    "NF", r"(AAC|DDP?)_?(5_1|2_0)?", r"\d+mb", r"\d+kbps", r"_$",
)
# These release groups are credits, not title words (78ef6ae); keep their own
# pass after tags, since combining both passes changes adjacent-tag behavior.
RELEASE_GROUPS = (
    "d3g", "CiNEFiLE", "CTR", "PRoDJi", "regret", "deef", "POIASD", "Cinefeel",
    "NTG", "NTb", "monkee", "YELLOWBiRD", "Atmos", "EPSiLON", "cielos", "ION10",
    "MeGusta", "METCON", "x0r", "xlf", "S8RHiNO", "GOSSIP", "btx", "strife", "DBS",
    "TEPES", "pawe", "ggezl2006", "CAKES", "HiggsBoson", "Coo7",
)
# Only release-name punctuation collapses; existing underscores stay separate.
DELIMITERS = " .+-,"
# A terminal codec owns the remaining encoder suffix, including unknown groups.
CODEC_PATTERN = rb"_([xh]_?26[45]|hevc)(_\w+)?(?=(/|$))"
_FLAGS = re.IGNORECASE | re.MULTILINE
_TAGS = re.compile((r"_(" + "|".join(TAG_PATTERNS) + r")(?=(_|/|$))").encode(), _FLAGS)
_GROUPS = re.compile((r"_(" + "|".join(RELEASE_GROUPS) + r")(?=(_|/|$))").encode(), _FLAGS)
_DELIMITERS = re.compile(b"[" + re.escape(DELIMITERS.encode()) + b"]+")
_CODEC = re.compile(CODEC_PATTERN, _FLAGS)


def clean_title(path_relative_to_folder: str, ext: str) -> str:
    r"""Return the extensionless link name, byte-for-byte like the old pipeline.

    ``ext`` accepts ``mkv`` or ``.mkv``. Bytes regexes retain Perl's non-Unicode
    ``\w``/case semantics for UTF-8 filenames. Do not trim leftover underscores:
    they are part of the existing public links, not a cosmetic cleanup target.
    """
    value = os.fsencode(path_relative_to_folder).replace(b"'", b"")
    # The old extension substitution is a regex, even for dotless sidecars
    # whose Bash-derived "extension" includes a directory fragment.
    value = re.sub(rb"\." + os.fsencode(ext.removeprefix(".")), b"", value)
    value = value.replace(b"&", b"and").translate(None, b"[]()")
    value = _DELIMITERS.sub(b"_", value)
    value = _CODEC.sub(b"", value)
    value = _TAGS.sub(b"", value)
    value = _GROUPS.sub(b"", value)
    return os.fsdecode(value.rstrip(b"/\n").rsplit(b"/", 1)[-1])
