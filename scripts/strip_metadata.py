#!/usr/bin/env python
"""strip_metadata.py — Strip unwanted metadata tags from audio files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except ImportError:
    print("Please install mutagen first: pip install mutagen")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "playlist-checker-config.json"

SONGS_DIR = Path(r"Z:\music")
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            if cfg.get("music_root"):
                SONGS_DIR = Path(cfg["music_root"])
    except Exception:
        pass

ID3_FIELDS = {
    "TRCK", "TPOS",  # track / disc number
    "TDRC", "TYER", "TDAT", "TDOR",  # year / date variants
    "TCON",  # genre
    "COMM",  # comments
    "USLT", "SYLT",  # lyrics
}

VORBIS_FIELDS = {
    "tracknumber", "discnumber",
    "date", "year",
    "genre",
    "comment",
    "lyrics", "unsyncedlyrics",
}

MP4_FIELDS = {
    "trkn", "disk",  # track / disc number
    "\xa9day",   # year/date
    "\xa9gen", "gnre",  # genre
    "\xa9cmt",   # comments
    "\xa9lyr",   # embedded lyrics
}


def strip_id3(audio: mutagen.File, file_path: Path) -> bool:
    if audio.tags is None:
        return False
    keys_to_delete = [k for k in audio.tags.keys() if k[:4] in ID3_FIELDS or k in ID3_FIELDS]
    if not keys_to_delete:
        return False
    print(f"[{file_path.name}] Removing ID3 fields: {keys_to_delete}")
    for k in keys_to_delete:
        del audio.tags[k]
    audio.save()
    return True


def strip_vorbis(audio: mutagen.File, file_path: Path) -> bool:
    target = {f.lower() for f in VORBIS_FIELDS}
    keys_to_delete = [k for k in audio.keys() if k.lower() in target]
    if not keys_to_delete:
        return False
    for k in keys_to_delete:
        del audio[k]
    print(f"[{file_path.name}] Removing Vorbis fields: {keys_to_delete}")
    audio.save()
    return True


def strip_mp4(audio: mutagen.File, file_path: Path) -> bool:
    if audio.tags is None:
        return False
    removed = []
    for field in MP4_FIELDS:
        if field in audio.tags:
            del audio.tags[field]
            removed.append(field)
    if not removed:
        return False
    print(f"[{file_path.name}] Removing MP4 fields: {removed}")
    audio.save()
    return True


def strip_directory(directory: Path) -> None:
    extensions = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wma", ".aiff"}
    all_files = [
        Path(e.path) for e in os.scandir(directory)
        if e.is_file() and os.path.splitext(e.name)[1].lower() in extensions
    ]

    if not all_files:
        print(f"No media files found in {directory}")
        return

    changed = 0
    for file_path in all_files:
        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                continue

            if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
                if strip_id3(audio, file_path):
                    changed += 1
            elif isinstance(audio, MP4):
                if strip_mp4(audio, file_path):
                    changed += 1
            elif isinstance(audio, FLAC) or hasattr(audio, "keys"):
                if strip_vorbis(audio, file_path):
                    changed += 1

        except Exception as e:
            print(f"Error processing {file_path.name}: {e}")

    print(f"\nDone. {changed}/{len(all_files)} files modified.")


def pick_folder() -> list[Path]:
    if not SONGS_DIR.exists():
        print(f"Music directory not found: {SONGS_DIR}")
        sys.exit(1)

    subfolders = sorted(
        [Path(f.path) for f in os.scandir(SONGS_DIR) if f.is_dir()],
        key=lambda f: f.name
    )
    if not subfolders:
        print("No subfolders found in music directory.")
        sys.exit(1)

    print("Available folders:")
    for i, entry in enumerate(subfolders, 1):
        print(f"  {i}. {entry.name}")
    print("  0. All folders")

    choice = input("\nSelect folder number: ").strip()
    if choice == "0":
        return subfolders
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(subfolders):
            return [subfolders[idx]]
    except ValueError:
        pass
    print("Invalid selection.")
    sys.exit(1)


if __name__ == "__main__":
    selected = pick_folder()

    for folder in selected:
        print(f"\n=== {folder.name} ===")
        strip_directory(folder)
    print("\nAll done.")
