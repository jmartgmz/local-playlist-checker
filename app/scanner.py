from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from app.config import AUDIO_EXTENSIONS
from app.models import Track
from app.utils import parse_artists


def probe_audio_duration_ms(path: Path) -> Optional[int]:
    try:
        mutagen_file = __import__("mutagen", fromlist=["File"]).File
    except Exception:
        return None

    try:
        metadata = mutagen_file(path)
    except Exception:
        return None

    if metadata is None or not getattr(metadata, "info", None):
        return None

    length_seconds = getattr(metadata.info, "length", None)
    if length_seconds is None:
        return None

    try:
        return int(float(length_seconds) * 1000)
    except (TypeError, ValueError):
        return None


def parse_local_filename(path: Path) -> Track:
    stem = path.stem
    stem = re.sub(r"^\d{1,3}[\s._-]+", "", stem)
    stem = stem.replace("_", " ")

    split_match = re.split(r"\s+-\s+", stem, maxsplit=1)
    if len(split_match) == 2:
        left, right = split_match[0].strip(), split_match[1].strip()
        artists = parse_artists(left)
        title = right
    else:
        title = stem.strip()
        artists = []

    return Track(
        title=title,
        artists=artists,
        source=path.name,
        duration_ms=probe_audio_duration_ms(path),
        file_path=str(path),
    )


def scan_local_tracks(folder: Path) -> List[Track]:
    tracks: List[Track] = []
    if not folder.exists():
        return tracks

    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            tracks.append(parse_local_filename(path))
    return tracks
