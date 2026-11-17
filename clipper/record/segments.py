"""Segment naming and time mapping (wall-clock UTC epoch <-> file + offset)."""

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# ffmpeg -strftime pattern; ffmpeg runs with TZ=UTC so this is UTC.
SEGMENT_PATTERN = "%Y%m%dT%H%M%SZ.ts"
_NAME_RE = re.compile(r"^(\d{8}T\d{6})Z\.ts$")

AD_RE = re.compile(r"advertisement break of (\d+(?:\.\d+)?) seconds?", re.IGNORECASE)
AD_HINT_RE = re.compile(r"\b(ads?|advertisement|pre-roll|midroll)\b", re.IGNORECASE)


def epoch_from_name(path: Path) -> float | None:
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp()


def segment_start_epoch(path: Path) -> float:
    """Best available wall-clock start: APFS birth time (sub-second), else filename (1 s)."""
    try:
        birth = os.stat(path).st_birthtime  # macOS / BSD
    except (AttributeError, FileNotFoundError):
        birth = None
    named = epoch_from_name(path)
    if birth is not None and (named is None or abs(birth - named) < 5):
        return birth
    if named is not None:
        return named
    return os.stat(path).st_mtime


@dataclass(frozen=True)
class SegmentSpan:
    path: str
    start_epoch: float
    duration: float


def locate(spans: list[SegmentSpan], epoch: float) -> tuple[str, float] | None:
    """Map a wall-clock epoch to (segment path, offset inside it); None if not recorded."""
    for s in spans:
        if s.start_epoch <= epoch < s.start_epoch + s.duration:
            return s.path, epoch - s.start_epoch
    return None


def parse_ad_line(line: str) -> dict | None:
    """Extract an ad-break event from a streamlink log line."""
    m = AD_RE.search(line)
    if m:
        return {"duration": float(m.group(1)), "message": line.strip()}
    if "Will skip ad segments" in line:  # startup notice, not an actual break
        return None
    if AD_HINT_RE.search(line) and "[plugins.twitch]" in line:
        return {"duration": None, "message": line.strip()}
    return None
