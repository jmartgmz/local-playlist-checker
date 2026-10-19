#!/usr/bin/env python
"""standardize_covers.py — Standardize album covers across FLAC files in a folder.

Phase 1: Clears all picture description fields (e.g. "Album cover").
Phase 2: Scans files and groups them by album tag.
Phase 3: For each album with inconsistent covers, replaces all with
         the highest quality one found. Also fixes wrong picture types
         (e.g. type 0 "Other" -> type 3 "Front Cover").

Usage:
  python scripts/standardize_covers.py <folder> [--dry-run] [--recursive] [--exclude <pattern>]
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from mutagen.flac import FLAC, Picture
except ImportError:
    print("Please install mutagen first: pip install mutagen")
    sys.exit(1)


def safe_str(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return repr(value)


def get_album_key(flac_obj: FLAC) -> str | None:
    album = flac_obj.get("album")
    if album:
        name = album[0].strip()
        return name if name else None
    return None


def get_cover_info(flac_obj: FLAC) -> dict | None:
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


def pick_best_cover(cover_infos: list[dict]) -> dict:
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
    p.desc = ""  # Clean description
    p.width = picture.width
    p.height = picture.height
    p.depth = picture.depth
    p.colors = picture.colors
    p.data = picture.data
    return p


def apply_cover(flac_path: Path, best_picture: Picture, dry_run: bool = False) -> bool:
    try:
        f = FLAC(str(flac_path))
        front_cover = make_front_cover(best_picture)
        f.clear_pictures()
        f.add_picture(front_cover)
        if not dry_run:
            f.save()
        return True
    except PermissionError:
        print(f"    ERROR: Permission denied saving {safe_str(flac_path.name)} (file may be locked)")
        return False
    except Exception as e:
        print(f"    ERROR saving {safe_str(flac_path.name)}: {e}")
        return False


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python standardize_covers.py <folder> [--dry-run] [--recursive] [--exclude <pattern>]")
        sys.exit(1)

    folder = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv
    recursive = "--recursive" in sys.argv

    excludes = []
    for i, arg in enumerate(sys.argv):
        if arg == "--exclude" and i + 1 < len(sys.argv):
            excludes.append(sys.argv[i + 1].lower())

    if not folder.is_dir():
        print(f"Error: {folder} is not a directory.")
        sys.exit(1)

    if dry_run:
        print("=== DRY RUN MODE (no changes will be saved) ===\n")

    if recursive:
        flac_files = sorted(folder.rglob("*.flac"))
    else:
        flac_files = sorted(folder.glob("*.flac"))

    if excludes:
        original_count = len(flac_files)
        flac_files = [
            f for f in flac_files
            if not any(exc in str(f.parent).lower() for exc in excludes)
        ]
        skipped = original_count - len(flac_files)
        print(f"Found {len(flac_files)} FLAC files in: {folder} (excluded {skipped})\n")
    else:
        print(f"Found {len(flac_files)} FLAC files in: {folder}{' (recursive)' if recursive else ''}\n")

    # Phase 1: Clear picture descriptions
    print("--- Phase 1: Clearing picture descriptions ---\n")
    desc_cleared = 0
    for fpath in flac_files:
        try:
            f = FLAC(str(fpath))
        except Exception as e:
            print(f"  ERROR reading {safe_str(fpath.name)}: {e}")
            continue

        pics = f.pictures
        if not pics:
            continue

        changed = False
        for p in pics:
            if p.desc:
                print(f"  CLEAR: {safe_str(fpath.name)} — desc \"{safe_str(p.desc)}\" -> \"\"")
                if not dry_run:
                    p.desc = ""
                changed = True

        if changed:
            if not dry_run:
                f.clear_pictures()
                for p in pics:
                    f.add_picture(p)
                try:
                    f.save()
                except Exception as e:
                    print(f"  ERROR saving {safe_str(fpath.name)}: {e}")
                    continue
            desc_cleared += 1

    print(f"\n  Descriptions cleared: {desc_cleared} file(s)\n")

    # Phase 2: Group by album
    albums = defaultdict(list)
    no_album = []
    no_cover = []

    print("--- Phase 2: Scanning files ---\n")
    for fpath in flac_files:
        try:
            f = FLAC(str(fpath))
        except Exception as e:
            print(f"  ERROR reading {safe_str(fpath.name)}: {e}")
            continue

        album = get_album_key(f)
        if not album:
            no_album.append(fpath)
            continue

        cover = get_cover_info(f)
        if not cover:
            no_cover.append(fpath)
            albums[album].append((fpath, None))
        else:
            albums[album].append((fpath, cover))

    print(f"  Albums found: {len(albums)}")
    print(f"  Files with no album tag: {len(no_album)}")
    print(f"  Files with no cover art: {len(no_cover)}")

    # Phase 3: Unify covers
    print("\n--- Phase 3: Checking album cover consistency ---\n")
    albums_fixed = 0
    files_updated = 0
    albums_skipped = 0

    for album_name in sorted(albums.keys(), key=lambda x: x.lower()):
        tracks = albums[album_name]
        tracks_with_covers = [(p, c) for p, c in tracks if c is not None]
        tracks_without_covers = [(p, c) for p, c in tracks if c is None]

        if not tracks_with_covers:
            print(f"  [{safe_str(album_name)}] — {len(tracks)} track(s), NO covers found. Skipping.")
            albums_skipped += 1
            continue

        unique_hashes = set(c["hash"] for _, c in tracks_with_covers)
        wrong_type_tracks = [(p, c) for p, c in tracks_with_covers if c["pic_type"] != 3]

        if len(unique_hashes) == 1 and len(tracks_without_covers) == 0 and len(wrong_type_tracks) == 0:
            continue

        all_covers = [c for _, c in tracks_with_covers]
        best = pick_best_cover(all_covers)
        best_picture = best["picture"]

        needs_update = []
        for fpath, cover in tracks:
            if cover is None or cover["hash"] != best["hash"] or cover["pic_type"] != 3:
                needs_update.append((fpath, cover))

        if not needs_update:
            continue

        albums_fixed += 1
        print(f"  [{safe_str(album_name)}] — updating {len(needs_update)}/{len(tracks)} track(s) with best cover ({best['width']}x{best['height']})")

        for fpath, cover in needs_update:
            action = "WOULD UPDATE" if dry_run else "UPDATE"
            reason = ""
            if cover is None:
                reason = " (missing cover)"
            elif cover["hash"] != best["hash"]:
                reason = " (different image)"
            elif cover["pic_type"] != 3:
                reason = f" (wrong type: {cover['pic_type']})"

            success = apply_cover(fpath, best_picture, dry_run)
            if success:
                print(f"      {action}: {safe_str(fpath.name)}{reason}")
                files_updated += 1

    print("\n--- Summary ---")
    print(f"  Descriptions cleared: {desc_cleared}")
    print(f"  Albums scanned: {len(albums)}")
    print(f"  Albums with inconsistent covers: {albums_fixed}")
    print(f"  Albums skipped (no covers): {albums_skipped}")
    print(f"  Files updated: {files_updated}")
    if dry_run:
        print("\n  (Dry run — no files modified)")


if __name__ == "__main__":
    main()
