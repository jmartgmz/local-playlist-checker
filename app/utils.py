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


# Map of Windows-illegal characters to visually similar Unicode replacements.
# These are the same substitutions the user already applies by hand.
FILENAME_CHAR_REPLACEMENTS: Dict[str, str] = {
    "/": " \u2044 ",  # ⁄  fraction slash (padded with spaces)
    ":": "\ua789",   # ꞉  modifier letter colon
    "?": "\uff1f",   # ？ fullwidth question mark
    ">": "\u02c3",   # ˃  modifier letter right arrowhead
    "<": "\u02c2",   # ˂  modifier letter left arrowhead
    '"': "\u201c",   # "  left double quotation mark
    "|": "\u2502",   # │  box drawings light vertical
    "*": "\u2217",   # ∗  asterisk operator
    "\\": "\u29f5",  # ⧵  reverse solidus operator
}


def sanitize_filename_for_os(name: str) -> str:
    """Replace Windows-illegal characters with Unicode look-alike substitutes."""
    for illegal, safe in FILENAME_CHAR_REPLACEMENTS.items():
        name = name.replace(illegal, safe)
    return name.strip()


def build_expected_filename(artist_display: str, title: str) -> str:
    """Build the expected filename stem: 'Artist - Title' with OS-safe characters."""
    raw = f"{artist_display} - {title}"
    return sanitize_filename_for_os(raw)


KNOWN_COMMA_ARTISTS = [
    "Tyler, The Creator",
    "Earth, Wind & Fire",
    "Crosby, Stills, Nash & Young",
    "Emerson, Lake & Palmer",
    "Blood, Sweat & Tears",
]

def parse_artists(value: str) -> List[str]:
    if not value:
        return []
        
    # Temporarily hide commas in known artists so they don't get split
    placeholder = "___COMMA___"
    for known in KNOWN_COMMA_ARTISTS:
        if known.lower() in value.lower():
            safe_known = known.replace(",", placeholder)
            value = re.sub(re.escape(known), safe_known, value, flags=re.IGNORECASE)

    parts = re.split(r";|,|\band\b|\bwith\b|\bx\b", value, flags=re.IGNORECASE)
    
    return [part.strip().replace(placeholder, ",") for part in parts if part.strip()]


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
