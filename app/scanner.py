from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import AUDIO_EXTENSIONS
from app.models import Track
from app.utils import parse_artists


def probe_audio_metadata(path: Path) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    duration_ms = None
    album = None
    metadata_title = None
    try:
        mutagen_file = __import__("mutagen", fromlist=["File"]).File
    except Exception:
        return None, None

    try:
        metadata = mutagen_file(path)
    except Exception:
        return None, None

    if metadata is not None and getattr(metadata, "info", None):
        length_seconds = getattr(metadata.info, "length", None)
        if length_seconds is not None:
            try:
                duration_ms = int(float(length_seconds) * 1000)
            except (TypeError, ValueError):
                pass

    if metadata is not None and getattr(metadata, "tags", None):
        tags = metadata.tags
        if tags is not None:
            if "album" in tags:
                album = str(tags["album"][0]) if isinstance(tags["album"], list) else str(tags["album"])
            elif "TALB" in tags:
                album = str(tags["TALB"].text[0]) if hasattr(tags["TALB"], "text") and tags["TALB"].text else str(tags["TALB"])
            elif "\xa9alb" in tags:
                album = str(tags["\xa9alb"][0]) if isinstance(tags["\xa9alb"], list) else str(tags["\xa9alb"])

            if "title" in tags:
                metadata_title = str(tags["title"][0]) if isinstance(tags["title"], list) else str(tags["title"])
            elif "TIT2" in tags:
                metadata_title = str(tags["TIT2"].text[0]) if hasattr(tags["TIT2"], "text") and tags["TIT2"].text else str(tags["TIT2"])
            elif "\xa9nam" in tags:
                metadata_title = str(tags["\xa9nam"][0]) if isinstance(tags["\xa9nam"], list) else str(tags["\xa9nam"])


    return duration_ms, album, metadata_title


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

    duration_ms, album, metadata_title = probe_audio_metadata(path)

    return Track(
        title=title,
        artists=artists,
        source=path.name,
        duration_ms=duration_ms,
        album=album,
        metadata_title=metadata_title,
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
