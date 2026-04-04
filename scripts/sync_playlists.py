#!/usr/bin/env python
"""sync_playlists.py — standalone Exportify CSV syncer.

Reads saved config and downloads fresh CSVs for all configured playlists.
No Flask, no web UI — just the sync.

Usage (from project root):
    python scripts/sync_playlists.py [--visible]

Options:
    --visible   Open a real browser window instead of running headless.
                Use this once if Spotify login is required.

A log file is always written to logs/sync.log in the project root.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure the project root is on sys.path so `app` package can be imported.
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import DEFAULT_EXPORT_DIR, DEFAULT_PROFILE_DIR, load_saved_config
from app.exportify import download_exportify_csvs
from app.utils import build_mapping, parse_overrides

_LOG_PATH = _PROJECT_ROOT / "logs" / "sync.log"


def _setup_logging() -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(_LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    _setup_logging()
    log = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Sync Exportify playlist CSVs.")
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Run with a visible browser (useful when Spotify login is needed).",
    )
    args = parser.parse_args()

    log.info("=== Sync started ===")

    config = load_saved_config()
    selected_folders = config.get("selected_folders", [])
    if not isinstance(selected_folders, list) or not selected_folders:
        log.error("No playlists configured. Open the web UI and save your settings first.")
        sys.exit(1)

    overrides_raw = str(config.get("overrides", ""))
    overrides = parse_overrides(overrides_raw)
    mapping = build_mapping([str(f) for f in selected_folders], overrides)
    playlist_names = [playlist for _, playlist in mapping]

    silent = not args.visible
    mode = "headless" if silent else "visible browser"
    log.info("Mode: %s | Playlists: %d", mode, len(playlist_names))
    for name in playlist_names:
        log.info("  • %s", name)

    try:
        downloaded, skipped = download_exportify_csvs(
            playlist_names=playlist_names,
            export_dir=DEFAULT_EXPORT_DIR,
            profile_dir=DEFAULT_PROFILE_DIR,
            headless=silent,
        )
    except Exception as exc:
        log.exception("Sync failed: %s", exc)
        sys.exit(1)

    for name in downloaded:
        log.info("  ✓ Downloaded: %s", name)
    for name in skipped:
        log.warning("  ✗ Skipped:    %s", name)

    if downloaded and not skipped:
        log.info("=== Sync completed — all %d playlist(s) downloaded ===", len(downloaded))
    elif downloaded:
        log.warning("=== Sync completed with warnings — %d downloaded, %d skipped ===", len(downloaded), len(skipped))
    else:
        log.error("=== Sync failed — no playlists downloaded ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
