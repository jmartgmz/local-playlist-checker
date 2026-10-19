from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TCON, TDRC
from mutagen.mp4 import MP4

from app.config import (
    DEFAULT_EXPORT_DIR,
    DEFAULT_PROFILE_DIR,
    load_saved_config,
)
from app.exportify import download_exportify_csvs, find_playlist_csv
from app.utils import build_mapping, parse_overrides

# ==============================================================================
# Strip Metadata Definitions & Logic
# ==============================================================================

ID3_STRIP_FIELDS: Set[str] = {
    "TRCK", "TPOS",              # track / disc number
    "TDRC", "TYER", "TDAT", "TDOR",  # year / date variants
    "TCON",                      # genre
    "COMM",                      # comments
    "USLT", "SYLT",              # unsynchronized / synchronized lyrics
}

VORBIS_STRIP_FIELDS: Set[str] = {
    "tracknumber", "discnumber",
    "date", "year",
    "genre",
    "comment",
    "lyrics", "unsyncedlyrics",
}

MP4_STRIP_FIELDS: Set[str] = {
    "trkn", "disk",  # track / disc number
    "\xa9day",       # year/date
    "\xa9gen", "gnre",  # genre
    "\xa9cmt",       # comments
    "\xa9lyr",       # embedded lyrics
}


def strip_id3(audio: Any, file_path: Path, log_fn: Callable[[str], None]) -> bool:
    if audio.tags is None:
        return False
    keys_to_delete = [k for k in audio.tags.keys() if k[:4] in ID3_STRIP_FIELDS or k in ID3_STRIP_FIELDS]
    if not keys_to_delete:
        return False
    log_fn(f"  [{file_path.name}] Removing ID3 fields: {', '.join(keys_to_delete)}")
    for k in keys_to_delete:
        del audio.tags[k]
    audio.save()
    return True


def strip_vorbis(audio: Any, file_path: Path, log_fn: Callable[[str], None]) -> bool:
    target = {f.lower() for f in VORBIS_STRIP_FIELDS}
    keys_to_delete = [k for k in audio.keys() if k.lower() in target]
    if not keys_to_delete:
        return False
    for k in keys_to_delete:
        del audio[k]
    log_fn(f"  [{file_path.name}] Removing Vorbis fields: {', '.join(keys_to_delete)}")
    audio.save()
    return True


def strip_mp4(audio: Any, file_path: Path, log_fn: Callable[[str], None]) -> bool:
    if audio.tags is None:
        return False
    removed = []
    for field in MP4_STRIP_FIELDS:
        if field in audio.tags:
            del audio.tags[field]
            removed.append(field)
    if not removed:
        return False
    log_fn(f"  [{file_path.name}] Removing MP4 fields: {', '.join(removed)}")
    audio.save()
    return True


def strip_metadata_dir(directory: Path, log_fn: Callable[[str], None]) -> Dict[str, int]:
    extensions = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wma", ".aiff"}
    all_files = [
        Path(e.path)
        for e in os.scandir(directory)
        if e.is_file() and os.path.splitext(e.name)[1].lower() in extensions
    ]

    if not all_files:
        log_fn(f"No media files found in {directory.name}")
        return {"scanned": 0, "modified": 0, "errors": 0}

    changed = 0
    errors = 0
    for file_path in all_files:
        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                continue

            if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
                if strip_id3(audio, file_path, log_fn):
                    changed += 1
            elif isinstance(audio, MP4):
                if strip_mp4(audio, file_path, log_fn):
                    changed += 1
            elif isinstance(audio, FLAC) or hasattr(audio, "keys"):
                if strip_vorbis(audio, file_path, log_fn):
                    changed += 1
        except Exception as exc:
            log_fn(f"  [{file_path.name}] Error: {exc}")
            errors += 1

    log_fn(f"Done stripping {directory.name}: {changed}/{len(all_files)} file(s) modified.")
    return {"scanned": len(all_files), "modified": changed, "errors": errors}


# ==============================================================================
# Import Metadata (Year & Genre) Logic
# ==============================================================================

def normalize_title(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\(feat\..*?\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_csv_tracks(csv_path: Path) -> Dict[str, Dict[str, str]]:
    tracks: Dict[str, Dict[str, str]] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Track Name") or "").strip()
            if not name:
                continue
            key = normalize_title(name)
            release = (row.get("Release Date") or "").strip()
            genres = (row.get("Genres") or "").strip()
            tracks[key] = {
                "name": name,
                "year": release[:4] if release else "",
                "genres": genres,
            }
    return tracks


def get_track_title(file_path: Path) -> Optional[str]:
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
    base = file_path.stem
    if " - " in base:
        return base.split(" - ", 1)[1]
    return base


def write_id3_metadata(audio: Any, year: str, genres: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    if year:
        audio.tags["TDRC"] = TDRC(encoding=3, text=year)
    if genres:
        audio.tags["TCON"] = TCON(encoding=3, text=genres)
    audio.save()


def write_vorbis_metadata(audio: Any, year: str, genres: str) -> None:
    if year:
        audio["date"] = year
    if genres:
        audio["genre"] = genres
    audio.save()


def write_mp4_metadata(audio: Any, year: str, genres: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    if year:
        audio.tags["\xa9day"] = [year]
    if genres:
        audio.tags["\xa9gen"] = [genres]
    audio.save()


def import_metadata_dir(music_dir: Path, csv_path: Path, log_fn: Callable[[str], None]) -> Dict[str, int]:
    if not csv_path.exists():
        log_fn(f"CSV not found: {csv_path.name}")
        return {"scanned": 0, "matched": 0, "unmatched": 0, "errors": 0}
    if not music_dir.exists():
        log_fn(f"Directory not found: {music_dir}")
        return {"scanned": 0, "matched": 0, "unmatched": 0, "errors": 0}

    tracks = load_csv_tracks(csv_path)
    extensions = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".aiff"}
    all_files = [
        Path(e.path)
        for e in os.scandir(music_dir)
        if e.is_file() and os.path.splitext(e.name)[1].lower() in extensions
    ]

    if not all_files:
        log_fn(f"No media files found in {music_dir.name}")
        return {"scanned": 0, "matched": 0, "unmatched": 0, "errors": 0}

    matched = 0
    unmatched: List[str] = []
    errors = 0

    for file_path in all_files:
        fname = file_path.name
        raw_title = get_track_title(file_path) or file_path.stem
        key = normalize_title(raw_title)

        data = tracks.get(key)
        if data is None:
            unmatched.append(fname)
            continue

        year = data["year"]
        genres = data["genres"]

        if not year and not genres:
            log_fn(f"  [{fname}] Matched '{data['name']}' — no year/genre data in CSV, skipping.")
            matched += 1
            continue

        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                log_fn(f"  [{fname}] mutagen couldn't read file, skipping.")
                errors += 1
                continue

            if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
                write_id3_metadata(audio, year, genres)
            elif isinstance(audio, MP4):
                write_mp4_metadata(audio, year, genres)
            elif isinstance(audio, FLAC) or hasattr(audio, "keys"):
                write_vorbis_metadata(audio, year, genres)
            else:
                log_fn(f"  [{fname}] Unsupported audio format, skipping.")
                continue

            fields = []
            if year:
                fields.append(f"year={year}")
            if genres:
                fields.append(f"genre='{genres}'")
            log_fn(f"  [{fname}] Matched '{data['name']}' — wrote {', '.join(fields)}")
            matched += 1
        except Exception as exc:
            log_fn(f"  [{fname}] Error: {exc}")
            errors += 1

    log_fn(f"Done importing for {music_dir.name}: {matched}/{len(all_files)} matched.")
    if unmatched:
        log_fn(f"  No CSV match found for {len(unmatched)} file(s).")
    return {"scanned": len(all_files), "matched": matched, "unmatched": len(unmatched), "errors": errors}


# ==============================================================================
# Standardize Covers Logic
# ==============================================================================

def get_album_key(flac_obj: FLAC) -> Optional[str]:
    album = flac_obj.get("album")
    if album:
        name = album[0].strip()
        return name if name else None
    return None


def get_cover_info(flac_obj: FLAC) -> Optional[Dict[str, Any]]:
    for p in flac_obj.pictures:
        if p.type == 3:  # Front cover
            return {
                "picture": p,
                "hash": hashlib.md5(p.data).hexdigest(),
                "resolution": p.width * p.height,
                "data_size": len(p.data),
                "width": p.width,
                "height": p.height,
                "pic_type": p.type,
            }
    if flac_obj.pictures:
        p = flac_obj.pictures[0]
        return {
            "picture": p,
            "hash": hashlib.md5(p.data).hexdigest(),
            "resolution": p.width * p.height,
            "data_size": len(p.data),
            "width": p.width,
            "height": p.height,
            "pic_type": p.type,
        }
    return None


def pick_best_cover(cover_infos: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(
        cover_infos,
        key=lambda c: (
            c["resolution"] if c["resolution"] > 0 else -1,
            c["data_size"],
        ),
    )


def make_front_cover(picture: Picture) -> Picture:
    p = Picture()
    p.type = 3  # Front cover
    p.mime = picture.mime
    p.desc = ""  # Keep description clean
    p.width = picture.width
    p.height = picture.height
    p.depth = picture.depth
    p.colors = picture.colors
    p.data = picture.data
    return p


def apply_cover(
    flac_path: Path,
    best_picture: Picture,
    dry_run: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
) -> bool:
    try:
        f = FLAC(str(flac_path))
        front_cover = make_front_cover(best_picture)
        f.clear_pictures()
        f.add_picture(front_cover)
        if not dry_run:
            f.save()
        return True
    except PermissionError:
        if log_fn:
            log_fn(f"    ERROR: Permission denied saving {flac_path.name} (file may be locked)")
        return False
    except Exception as exc:
        if log_fn:
            log_fn(f"    ERROR saving {flac_path.name}: {exc}")
        return False


def standardize_covers_dir(
    folder: Path,
    dry_run: bool = False,
    recursive: bool = True,
    excludes: Optional[List[str]] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    if not log_fn:
        log_fn = print
    excludes = [e.lower() for e in (excludes or [])]
    if dry_run:
        log_fn("=== DRY RUN MODE (no changes will be saved) ===")

    if recursive:
        flac_files = sorted(folder.rglob("*.flac"))
    else:
        flac_files = sorted(folder.glob("*.flac"))

    if excludes:
        orig = len(flac_files)
        flac_files = [f for f in flac_files if not any(exc in str(f.parent).lower() for exc in excludes)]
        log_fn(f"Found {len(flac_files)} FLAC file(s) in: {folder.name} (excluded {orig - len(flac_files)})")
    else:
        log_fn(f"Found {len(flac_files)} FLAC file(s) in: {folder.name}")

    if not flac_files:
        return {"flac_count": 0, "descriptions_cleared": 0, "albums_fixed": 0, "files_updated": 0}

    # Phase 1: Clear descriptions
    log_fn("\n--- Phase 1: Clearing picture descriptions ---")
    desc_cleared = 0
    for fpath in flac_files:
        try:
            f = FLAC(str(fpath))
            pics = f.pictures
            if not pics:
                continue
            changed = False
            for p in pics:
                if p.desc:
                    log_fn(f"  CLEAR: {fpath.name} — desc \"{p.desc}\" -> \"\"")
                    if not dry_run:
                        p.desc = ""
                    changed = True
            if changed:
                if not dry_run:
                    f.clear_pictures()
                    for p in pics:
                        f.add_picture(p)
                    f.save()
                desc_cleared += 1
        except Exception as exc:
            log_fn(f"  ERROR reading/saving {fpath.name}: {exc}")

    log_fn(f"  Descriptions cleared: {desc_cleared} file(s)")

    # Phase 2: Scan and group by album
    log_fn("\n--- Phase 2: Scanning albums ---")
    albums: Dict[str, List[Tuple[Path, Optional[Dict[str, Any]]]]] = {}
    no_album: List[Path] = []
    no_cover: List[Path] = []

    for fpath in flac_files:
        try:
            f = FLAC(str(fpath))
            album = get_album_key(f)
            if not album:
                no_album.append(fpath)
                continue
            cover = get_cover_info(f)
            if album not in albums:
                albums[album] = []
            if not cover:
                no_cover.append(fpath)
                albums[album].append((fpath, None))
            else:
                albums[album].append((fpath, cover))
        except Exception as exc:
            log_fn(f"  ERROR reading {fpath.name}: {exc}")

    log_fn(f"  Albums found: {len(albums)}")
    log_fn(f"  Files with no album tag: {len(no_album)}")
    log_fn(f"  Files with no cover art: {len(no_cover)}")

    # Phase 3: Check consistency and unify
    log_fn("\n--- Phase 3: Checking album cover consistency ---")
    albums_fixed = 0
    files_updated = 0

    for album_name in sorted(albums.keys(), key=lambda x: x.lower()):
        tracks = albums[album_name]
        tracks_with_covers = [(p, c) for p, c in tracks if c is not None]
        tracks_without_covers = [(p, c) for p, c in tracks if c is None]

        if not tracks_with_covers:
            log_fn(f"  [{album_name}] — {len(tracks)} track(s), NO covers found. Skipping.")
            continue

        unique_hashes = set(c["hash"] for _, c in tracks_with_covers)
        wrong_type_tracks = [(p, c) for p, c in tracks_with_covers if c["pic_type"] != 3]

        if len(unique_hashes) == 1 and len(tracks_without_covers) == 0 and len(wrong_type_tracks) == 0:
            continue

        all_covers = [c for _, c in tracks_with_covers]
        best = pick_best_cover(all_covers)
        best_pic = best["picture"]

        needs_update = []
        for fpath, cover in tracks:
            if cover is None or cover["hash"] != best["hash"] or cover["pic_type"] != 3:
                needs_update.append((fpath, cover))

        if not needs_update:
            continue

        albums_fixed += 1
        log_fn(f"  [{album_name}] — {len(tracks)} track(s), updating {len(needs_update)} with best cover ({best['width']}x{best['height']})")
        action_word = "WOULD UPDATE" if dry_run else "UPDATE"

        for fpath, cover in needs_update:
            reason = "missing" if cover is None else ("diff cover" if cover["hash"] != best["hash"] else "wrong type")
            if apply_cover(fpath, best_pic, dry_run=dry_run, log_fn=log_fn):
                log_fn(f"    {action_word}: {fpath.name} ({reason})")
                files_updated += 1

    log_fn("\n--- Summary ---")
    log_fn(f"  Albums scanned: {len(albums)}, Inconsistent: {albums_fixed}, Files updated: {files_updated}")
    return {
        "flac_count": len(flac_files),
        "descriptions_cleared": desc_cleared,
        "albums_fixed": albums_fixed,
        "files_updated": files_updated,
    }


# ==============================================================================
# ReplayGain Scanner (rsgain) Logic
# ==============================================================================

def find_rsgain_binary() -> Optional[Path]:
    candidate = Path(__file__).parent.parent / "vendor" / "rsgain-3.7-win64" / "rsgain.exe"
    if candidate.exists():
        return candidate
    system_which = shutil.which("rsgain")
    if system_which:
        return Path(system_which)
    music_tools_bin = Path(r"C:\Users\Koyomi\Desktop\Projects\music-tools\vendor\rsgain-3.7-win64\rsgain.exe")
    if music_tools_bin.exists():
        return music_tools_bin
    return None


def run_rsgain_dir(folder: Path, log_fn: Callable[[str], None]) -> bool:
    rsgain_exe = find_rsgain_binary()
    if not rsgain_exe:
        log_fn("ERROR: rsgain executable not found in vendor/ or system PATH.")
        return False

    cmd = [
        str(rsgain_exe),
        "easy",
        "-p", "no_album",
        "-m", "MAX",
        "-S",
        "-O",
        str(folder),
    ]
    log_fn(f"Running rsgain on {folder}...")
    log_fn(f"Command: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        if proc.stdout:
            for line in proc.stdout:
                line_str = line.strip()
                if line_str:
                    log_fn(line_str)
        proc.wait()
        if proc.returncode == 0:
            log_fn("rsgain completed successfully!")
            return True
        else:
            log_fn(f"rsgain exited with code {proc.returncode}")
            return False
    except Exception as exc:
        log_fn(f"Error executing rsgain: {exc}")
        return False


# ==============================================================================
# Exportify Sync Logic
# ==============================================================================

def sync_exportify_tool(playlist_names: List[str], headless: bool, log_fn: Callable[[str], None]) -> bool:
    log_fn(f"Starting Exportify CSV sync for {len(playlist_names)} playlist(s)...")
    log_fn(f"Mode: {'Headless' if headless else 'Visible browser'}")
    try:
        downloaded, skipped = download_exportify_csvs(
            playlist_names=playlist_names,
            export_dir=DEFAULT_EXPORT_DIR,
            profile_dir=DEFAULT_PROFILE_DIR,
            headless=headless,
        )
        for name in downloaded:
            log_fn(f"  ✓ Downloaded: {name}")
        for name in skipped:
            log_fn(f"  ✗ Skipped: {name}")
        log_fn(f"Sync complete: {len(downloaded)} downloaded, {len(skipped)} skipped.")
        return True
    except Exception as exc:
        log_fn(f"Sync error: {exc}")
        return False


# ==============================================================================
# Background Tool Job Manager
# ==============================================================================

_tool_jobs: Dict[str, Dict[str, Any]] = {}
_tool_lock = threading.Lock()


def get_tool_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _tool_lock:
        job = _tool_jobs.get(job_id)
        if not job:
            return None
        return {
            "id": job["id"],
            "tool": job["tool"],
            "folder": job["folder"],
            "status": job["status"],
            "start_time": job["start_time"],
            "logs": list(job["logs"]),
            "summary": dict(job.get("summary", {})),
            "error": job.get("error"),
        }


def get_active_tool_jobs() -> List[Dict[str, Any]]:
    with _tool_lock:
        return [
            {
                "id": j["id"],
                "tool": j["tool"],
                "folder": j["folder"],
                "status": j["status"],
                "start_time": j["start_time"],
            }
            for j in _tool_jobs.values()
        ]


def _resolve_targets(folder_arg: Optional[str]) -> Tuple[Optional[Path], List[str]]:
    saved_config = load_saved_config()
    music_root_str = str(saved_config.get("music_root", ""))
    music_root = Path(music_root_str).expanduser() if music_root_str else None

    selected = saved_config.get("selected_folders", [])
    selected_list = [str(x) for x in selected] if isinstance(selected, list) else []

    if folder_arg and folder_arg != "__ALL__":
        folders = [folder_arg]
    else:
        folders = selected_list

    return music_root, folders


def _tool_worker(job_id: str, tool_name: str, folder_arg: Optional[str], options: Dict[str, Any]) -> None:
    def log(msg: str) -> None:
        with _tool_lock:
            if job_id in _tool_jobs:
                _tool_jobs[job_id]["logs"].append(msg)

    try:
        music_root, folders = _resolve_targets(folder_arg)

        if tool_name == "sync_exports":
            saved_config = load_saved_config()
            overrides = parse_overrides(str(saved_config.get("overrides", "")))
            mapping = build_mapping(folders, overrides)
            playlist_names = [p for _, p in mapping]
            if not playlist_names:
                log("No playlists mapped or selected in configuration.")
                with _tool_lock:
                    _tool_jobs[job_id]["status"] = "failed"
                    _tool_jobs[job_id]["error"] = "No playlists mapped"
                return

            visible = bool(options.get("visible_browser", False))
            ok = sync_exportify_tool(playlist_names, headless=not visible, log_fn=log)
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "complete" if ok else "failed"
            return

        # For music file tools, we need music_root to be valid
        if not music_root or not music_root.exists() or not music_root.is_dir():
            log(f"ERROR: Music root folder does not exist: {music_root}")
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "failed"
                _tool_jobs[job_id]["error"] = "Invalid music root folder"
            return

        if tool_name == "strip_metadata":
            total_scanned = 0
            total_modified = 0
            total_errors = 0
            for folder_name in folders:
                target_dir = music_root / folder_name
                if not target_dir.exists() or not target_dir.is_dir():
                    log(f"Folder not found: {folder_name}, skipping.")
                    continue
                log(f"\n=== Stripping metadata in: {folder_name} ===")
                res = strip_metadata_dir(target_dir, log_fn=log)
                total_scanned += res["scanned"]
                total_modified += res["modified"]
                total_errors += res["errors"]

            log(f"\n==========================================")
            log(f"Finished Strip Metadata! Scanned: {total_scanned}, Modified: {total_modified}, Errors: {total_errors}")
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "complete"
                _tool_jobs[job_id]["summary"] = {
                    "scanned": total_scanned,
                    "modified": total_modified,
                    "errors": total_errors,
                }

        elif tool_name == "import_metadata":
            saved_config = load_saved_config()
            overrides = parse_overrides(str(saved_config.get("overrides", "")))
            mapping = dict(build_mapping(folders, overrides))

            total_scanned = 0
            total_matched = 0
            total_unmatched = 0
            total_errors = 0

            for folder_name in folders:
                target_dir = music_root / folder_name
                if not target_dir.exists() or not target_dir.is_dir():
                    log(f"Folder not found: {folder_name}, skipping.")
                    continue

                playlist_name = mapping.get(folder_name, folder_name)
                csv_path = find_playlist_csv(DEFAULT_EXPORT_DIR, playlist_name)
                if not csv_path:
                    # Try direct filename
                    direct_name = playlist_name.replace(" ", "_") + ".csv"
                    cand = DEFAULT_EXPORT_DIR / direct_name
                    if cand.exists():
                        csv_path = cand

                if not csv_path or not csv_path.exists():
                    log(f"\n=== {folder_name} === (No CSV export found, skipping)")
                    continue

                log(f"\n=== Importing metadata: {folder_name} (using {csv_path.name}) ===")
                res = import_metadata_dir(target_dir, csv_path, log_fn=log)
                total_scanned += res["scanned"]
                total_matched += res["matched"]
                total_unmatched += res["unmatched"]
                total_errors += res["errors"]

            log(f"\n==========================================")
            log(f"Finished Import Metadata! Scanned: {total_scanned}, Matched: {total_matched}, Unmatched: {total_unmatched}, Errors: {total_errors}")
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "complete"
                _tool_jobs[job_id]["summary"] = {
                    "scanned": total_scanned,
                    "matched": total_matched,
                    "unmatched": total_unmatched,
                    "errors": total_errors,
                }

        elif tool_name == "standardize_covers":
            dry_run = bool(options.get("dry_run", False))
            total_scanned = 0
            total_fixed = 0
            total_updated = 0

            for folder_name in folders:
                target_dir = music_root / folder_name
                if not target_dir.exists() or not target_dir.is_dir():
                    log(f"Folder not found: {folder_name}, skipping.")
                    continue
                log(f"\n=== Standardizing covers in: {folder_name} ===")
                res = standardize_covers_dir(target_dir, dry_run=dry_run, recursive=True, log_fn=log)
                total_scanned += res["flac_count"]
                total_fixed += res["albums_fixed"]
                total_updated += res["files_updated"]

            log(f"\n==========================================")
            log(f"Finished Standardize Covers! FLACs: {total_scanned}, Inconsistent Albums: {total_fixed}, Files Updated: {total_updated}")
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "complete"
                _tool_jobs[job_id]["summary"] = {
                    "flac_count": total_scanned,
                    "albums_fixed": total_fixed,
                    "files_updated": total_updated,
                }

        elif tool_name == "run_rsgain":
            # If all folders, run on music_root, otherwise run on selected folder
            if folder_arg == "__ALL__" or not folder_arg:
                target_path = music_root
            else:
                target_path = music_root / folder_arg

            log(f"\n=== Starting rsgain scan on: {target_path} ===")
            ok = run_rsgain_dir(target_path, log_fn=log)
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "complete" if ok else "failed"

        else:
            log(f"Unknown tool name: {tool_name}")
            with _tool_lock:
                _tool_jobs[job_id]["status"] = "failed"
                _tool_jobs[job_id]["error"] = f"Unknown tool: {tool_name}"

    except Exception as exc:
        log(f"\nUnexpected error running {tool_name}: {exc}")
        with _tool_lock:
            _tool_jobs[job_id]["status"] = "failed"
            _tool_jobs[job_id]["error"] = str(exc)


def start_tool_job(
    tool_name: str,
    folder_arg: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> str:
    job_id = str(uuid.uuid4())
    options = options or {}
    start_time = datetime.now().strftime("%H:%M:%S")

    with _tool_lock:
        _tool_jobs[job_id] = {
            "id": job_id,
            "tool": tool_name,
            "folder": folder_arg or "All Folders",
            "status": "running",
            "start_time": start_time,
            "logs": [f"[{start_time}] Started {tool_name} for {folder_arg or 'All Folders'}"],
            "summary": {},
            "error": None,
        }

    thread = threading.Thread(
        target=_tool_worker,
        args=(job_id, tool_name, folder_arg, options),
        daemon=True,
    )
    thread.start()
    return job_id
