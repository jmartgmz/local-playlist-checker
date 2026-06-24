from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import AUDIO_EXTENSIONS
from app.models import Track
from app.utils import parse_artists


def probe_audio_metadata(path: Path) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[List[str]], Optional[str], bool, Optional[List[str]]]:
    duration_ms = None
    album = None
    metadata_title = None
    metadata_artists: Optional[List[str]] = None
    metadata_artists_raw: Optional[str] = None
    has_navidrome_artists: bool = False
    navidrome_artists_values: Optional[List[str]] = None
    try:
        mutagen_file = __import__("mutagen", fromlist=["File"]).File
    except Exception:
        return None, None, None, None, None, False, None

    try:
        metadata = mutagen_file(path)
    except Exception:
        return None, None, None, None, None, False, None

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
            try:
                if "album" in tags:
                    album = str(tags["album"][0]) if isinstance(tags["album"], list) else str(tags["album"])
                elif "TALB" in tags:
                    album = str(tags["TALB"].text[0]) if hasattr(tags["TALB"], "text") and tags["TALB"].text else str(tags["TALB"])
                elif "\xa9alb" in tags:
                    album = str(tags["\xa9alb"][0]) if isinstance(tags["\xa9alb"], list) else str(tags["\xa9alb"])
            except (ValueError, KeyError):
                pass

            try:
                if "title" in tags:
                    metadata_title = str(tags["title"][0]) if isinstance(tags["title"], list) else str(tags["title"])
                elif "TIT2" in tags:
                    metadata_title = str(tags["TIT2"].text[0]) if hasattr(tags["TIT2"], "text") and tags["TIT2"].text else str(tags["TIT2"])
                elif "\xa9nam" in tags:
                    metadata_title = str(tags["\xa9nam"][0]) if isinstance(tags["\xa9nam"], list) else str(tags["\xa9nam"])
            except (ValueError, KeyError):
                pass

            # Read artist metadata
            artist_raw = None
            try:
                if "artist" in tags:
                    artist_raw = str(tags["artist"][0]) if isinstance(tags["artist"], list) else str(tags["artist"])
                elif "TPE1" in tags:
                    artist_raw = str(tags["TPE1"].text[0]) if hasattr(tags["TPE1"], "text") and tags["TPE1"].text else str(tags["TPE1"])
                elif "\xa9ART" in tags:
                    artist_raw = str(tags["\xa9ART"][0]) if isinstance(tags["\xa9ART"], list) else str(tags["\xa9ART"])
            except (ValueError, KeyError):
                pass
            if artist_raw:
                metadata_artists = parse_artists(artist_raw)
                metadata_artists_raw = artist_raw

            # Check for Navidrome multi-artist tags and read their values
            has_navidrome = False
            suffix = path.suffix.lower()
            try:
                if suffix == ".flac" or (hasattr(metadata, "tags") and isinstance(metadata.tags, dict)):
                    has_navidrome = "artists" in tags
                    if has_navidrome:
                        raw_vals = tags["artists"]
                        navidrome_artists_values = [str(v) for v in raw_vals] if isinstance(raw_vals, list) else [str(raw_vals)]
                elif suffix == ".mp3":
                    has_navidrome = "TXXX:ARTISTS" in tags
                    if has_navidrome:
                        frame = tags["TXXX:ARTISTS"]
                        navidrome_artists_values = [str(t) for t in frame.text] if hasattr(frame, "text") else [str(frame)]
                else:
                    has_navidrome = True
            except (ValueError, KeyError):
                pass

    return duration_ms, album, metadata_title, metadata_artists, metadata_artists_raw, has_navidrome, navidrome_artists_values


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

    duration_ms, album, metadata_title, metadata_artists, metadata_artists_raw, has_navidrome, nav_artists_vals = probe_audio_metadata(path)

    return Track(
        title=title,
        artists=artists,
        source=path.name,
        duration_ms=duration_ms,
        album=album,
        metadata_title=metadata_title,
        metadata_artists=metadata_artists,
        metadata_artists_raw=metadata_artists_raw,
        has_navidrome_artists=has_navidrome,
        navidrome_artists_values=nav_artists_vals,
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
