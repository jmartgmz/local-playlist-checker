from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.config import EXCLUDED_DISCOVERED_FOLDERS


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\(.*?\)|\[.*?\]", "", value)
    value = value.replace("&", " and ")
    value = value.replace("feat.", "")
    value = value.replace("ft.", "")
    # Keep Unicode letters/numbers so non-Latin titles (e.g. Japanese) still match.
    value = re.sub(r"[^\w\s]", " ", value)
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip().replace(" ", "_")


def parse_artists(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r";|,|\band\b|\bwith\b|\bx\b", value, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def parse_duration_to_ms(value: str) -> Optional[int]:
    raw = value.strip()
    if not raw:
        return None

    if raw.isdigit():
        return int(raw)

    # Supports formats like mm:ss and hh:mm:ss from CSV exports.
    parts = raw.split(":")
    if not all(part.isdigit() for part in parts):
        return None

    if len(parts) == 2:
        minutes, seconds = int(parts[0]), int(parts[1])
        return (minutes * 60 + seconds) * 1000

    if len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
        return (hours * 3600 + minutes * 60 + seconds) * 1000

    return None


def format_duration_ms(duration_ms: Optional[int]) -> str:
    if duration_ms is None:
        return "Unknown"
    total_seconds = duration_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def parse_overrides(raw: str) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        folder, playlist = line.split("=", 1)
        folder = folder.strip()
        playlist = playlist.strip()
        if folder and playlist:
            overrides[folder] = playlist
    return overrides


def parse_selected_folders(raw: str) -> List[str]:
    if not raw.strip():
        return []
    values = [part.strip() for part in raw.split(",")]
    return [value for value in values if value]


def build_mapping(folders: Sequence[str], overrides: Dict[str, str]) -> List[Tuple[str, str]]:
    return [(folder, overrides.get(folder, folder)) for folder in folders]


def collect_discovered_folders(music_root: Optional[Path]) -> List[str]:
    if not music_root or not music_root.exists() or not music_root.is_dir():
        return []
    folders = []
    for entry in music_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.strip().lower() in EXCLUDED_DISCOVERED_FOLDERS:
            continue
        folders.append(entry.name)
    return sorted(folders)


def track_to_row(track, folder: str = "") -> Dict[str, str]:
    """Convert a Track to a plain dict suitable for template rendering."""
    spotify_url = ""
    if track.spotify_uri and track.spotify_uri.startswith("spotify:track:"):
        track_id = track.spotify_uri.split(":")[-1]
        spotify_url = f"https://open.spotify.com/track/{track_id}"
    return {
        "title": track.title,
        "artists": ", ".join(track.artists) if track.artists else "Unknown",
        "source": track.source,
        "spotify_url": spotify_url,
        "folder": folder,
        "file_path": track.file_path or "",
    }
