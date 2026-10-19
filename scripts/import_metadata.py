#!/usr/bin/env python
"""import_metadata.py — Import year and genre metadata from CSV exports into audio tags."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import sys

# Force UTF-8 stdout
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, TCON, TDRC
    from mutagen.mp4 import MP4
except ImportError:
    print("Please install mutagen: pip install mutagen")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "playlist-checker-config.json"
EXPORTS_DIR = PROJECT_ROOT / "exports"

SONGS_DIR = Path(r"Z:\music")
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            if cfg.get("music_root"):
                SONGS_DIR = Path(cfg["music_root"])
    except Exception:
        pass


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\(feat\..*?\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_csv(path: Path) -> dict:
    tracks = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("Track Name") or "").strip()
            if not name:
                continue
            key = normalize(name)
            release = (row.get("Release Date") or "").strip()
            genres = (row.get("Genres") or "").strip()
            tracks[key] = {
                "name": name,
                "year": release[:4] if release else "",
                "genres": genres,
            }
    return tracks


def get_title_from_tags(file_path: Path) -> str | None:
    try:
        audio = mutagen.File(str(file_path))
        if audio and audio.tags:
            for key in ("TIT2", "title", "\xa9nam"):
                val = audio.tags.get(key)
                if val is not None:
                    if hasattr(val, "text"):
                        return str(val.text[0])
                    if isinstance(val, list):
                        return str(val[0])
                    return str(val)
    except Exception:
        pass
    return None


def title_from_filename(file_path: Path) -> str:
    base = file_path.stem
    if " - " in base:
        return base.split(" - ", 1)[1]
    return base


def write_id3(audio: mutagen.File, year: str, genres: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    if year:
        audio.tags["TDRC"] = TDRC(encoding=3, text=year)
    if genres:
        audio.tags["TCON"] = TCON(encoding=3, text=genres)
    audio.save()


def write_vorbis(audio: mutagen.File, year: str, genres: str) -> None:
    if year:
        audio["date"] = year
    if genres:
        audio["genre"] = genres
    audio.save()


def write_mp4(audio: mutagen.File, year: str, genres: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    if year:
        audio.tags["\xa9day"] = [year]
    if genres:
        audio.tags["\xa9gen"] = [genres]
    audio.save()


def import_metadata(music_dir: Path, csv_path: Path) -> None:
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        return
    if not music_dir.exists():
        print(f"Music directory not found: {music_dir}")
        return

    tracks = load_csv(csv_path)

    extensions = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".aiff"}
    all_files = [
        Path(e.path)
        for e in os.scandir(music_dir)
        if e.is_file() and os.path.splitext(e.name)[1].lower() in extensions
    ]

    if not all_files:
        print(f"No media files found in {music_dir}")
        return

    matched = 0
    unmatched: list[str] = []

    for file_path in all_files:
        fname = file_path.name
        raw_title = get_title_from_tags(file_path) or title_from_filename(file_path)
        key = normalize(raw_title)

        data = tracks.get(key)
        if data is None:
            unmatched.append(fname)
            continue

        year = data["year"]
        genres = data["genres"]

        if not year and not genres:
            print(f"[{fname}] Matched '{data['name']}' — no data to write, skipping.")
            matched += 1
            continue

        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                print(f"[{fname}] mutagen couldn't read file, skipping.")
                continue

            if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
                write_id3(audio, year, genres)
            elif isinstance(audio, MP4):
                write_mp4(audio, year, genres)
            elif isinstance(audio, FLAC) or hasattr(audio, "keys"):
                write_vorbis(audio, year, genres)
            else:
                print(f"[{fname}] Unsupported format, skipping.")
                continue

            fields = []
            if year:
                fields.append(f"year={year}")
            if genres:
                fields.append(f"genre='{genres}'")
            print(f"[{fname}] Matched '{data['name']}' — wrote {', '.join(fields)}")
            matched += 1

        except Exception as e:
            print(f"[{fname}] Error: {e}")

    print(f"\nDone. {matched}/{len(all_files)} matched.")
    if unmatched:
        print(f"\nNo CSV match found for ({len(unmatched)} files):")
        for f in sorted(unmatched):
            print(f"  {f}")


def pick_folder() -> list[Path]:
    if not SONGS_DIR.exists():
        print(f"Music directory not found: {SONGS_DIR}")
        sys.exit(1)

    subfolders = sorted(
        [Path(f.path) for f in os.scandir(SONGS_DIR) if f.is_dir()],
        key=lambda f: f.name,
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

    for entry in selected:
        csv_name = entry.name.replace(" ", "_") + ".csv"
        csv_path = EXPORTS_DIR / csv_name

        if not csv_path.exists():
            print(f"\n=== {entry.name} === (no CSV found at {csv_path}, skipping)")
            continue

        print(f"\n=== {entry.name} ===")
        import_metadata(entry, csv_path)
