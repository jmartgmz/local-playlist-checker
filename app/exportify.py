from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from playwright.sync_api import BrowserContext, Error, Page, TimeoutError, sync_playwright

from app.config import DEFAULT_LOGIN_WAIT_SECONDS
from app.models import Track
from app.utils import parse_artists, parse_duration_to_ms, sanitize_filename


def read_exportify_csv(csv_path: Path) -> List[Track]:
    rows: List[Track] = []
    if not csv_path.exists():
        return rows

    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            title = (row.get("Track Name") or "").strip()
            artists_text = (row.get("Artist Name(s)") or "").strip()
            duration_ms = parse_duration_to_ms(
                (row.get("Track Duration (ms)") or row.get("Duration (ms)") or row.get("Track Duration") or "").strip()
            )
            spotify_uri = (row.get("Track URI") or "").strip()
            if title:
                rows.append(
                    Track(
                        title=title,
                        artists=parse_artists(artists_text),
                        source=csv_path.name,
                        duration_ms=duration_ms,
                        spotify_uri=spotify_uri,
                    )
                )
    return rows


def find_playlist_csv(export_dir: Path, playlist_name: str) -> Optional[Path]:
    if not export_dir.exists():
        return None

    target = sanitize_filename(playlist_name).lower()
    candidates = []
    for path in export_dir.glob("*.csv"):
        score = 0
        stem = path.stem.lower()
        if stem == target:
            score = 3
        elif stem.startswith(target):
            score = 2
        elif target in stem:
            score = 1
        if score:
            candidates.append((score, path.stat().st_mtime, path))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][2]


def ensure_playlist_table_ready(page: Page, wait_seconds: int = 180, headless: bool = False) -> None:
    """Wait for Exportify playlists to load. Handles login detection and timeouts."""
    deadline = time.time() + wait_seconds
    startup_deadline = time.time() + 15  # Give 15 seconds for initial page load
    page_bootstrapped = False
    consecutive_no_load = 0

    while time.time() < deadline:
        try:
            login_visible = page.locator("#loginButton").is_visible(timeout=100)
            row_count = page.locator("#playlistsContainer tbody tr").count()
        except Exception:
            time.sleep(0.5)
            continue

        if row_count > 0:
            return

        if not page_bootstrapped:
            if time.time() > startup_deadline or login_visible or page.locator("#playlistsContainer").count() > 0:
                page_bootstrapped = True

        if headless and page_bootstrapped:
            if row_count == 0:
                consecutive_no_load += 1
                if consecutive_no_load >= 60:
                    raise RuntimeError(
                        "Silent mode could not load playlists from Exportify. "
                        "Run one sync with silent mode off to verify playlists load, then enable silent mode again."
                    )
            else:
                consecutive_no_load = 0

        if login_visible:
            time.sleep(1)
        else:
            time.sleep(0.5)

    try:
        login_visible = page.locator("#loginButton").is_visible(timeout=100)
    except Exception:
        login_visible = False

    if login_visible:
        raise RuntimeError(
            "Silent mode could not access playlists because Exportify requires Spotify login. "
            "Run one sync with silent mode off to sign in, then enable silent mode again."
        )

    raise TimeoutError("Playlists did not load from Exportify before timeout. Try again in a moment.")


def build_playlist_row_map(page: Page) -> Dict[str, int]:
    rows = page.locator("#playlistsContainer tbody tr")
    count = rows.count()
    row_map: Dict[str, int] = {}

    for idx in range(count):
        row = rows.nth(idx)
        name = row.locator("td:nth-child(2)").inner_text().strip()
        if name:
            row_map[name.lower()] = idx
    return row_map


def download_exportify_csvs(
    playlist_names: Sequence[str],
    export_dir: Path,
    profile_dir: Path,
    login_wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
    headless: bool = True,
) -> Tuple[List[str], List[str]]:
    export_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    downloaded: List[str] = []
    skipped: List[str] = []

    with sync_playwright() as playwright:
        context: BrowserContext = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            accept_downloads=True,
        )

        try:
            page = context.new_page()
            page.goto("https://exportify.net", wait_until="load", timeout=30000)

            ensure_playlist_table_ready(page, wait_seconds=login_wait_seconds, headless=headless)

            row_map = build_playlist_row_map(page)
            if not row_map:
                raise RuntimeError(
                    "Exportify loaded but no playlists were detected. "
                    "Run one sync with silent mode off to confirm playlist visibility."
                )

            for playlist_name in playlist_names:
                row_index = row_map.get(playlist_name.lower())
                if row_index is None:
                    skipped.append(f"{playlist_name} (not found in Exportify)")
                    continue

                row = page.locator("#playlistsContainer tbody tr").nth(row_index)
                export_button = row.locator("button[id^='export']")

                try:
                    with page.expect_download(timeout=120000) as dl_info:
                        export_button.click()
                    download = dl_info.value
                    out_name = f"{sanitize_filename(playlist_name)}.csv"
                    download.save_as(str(export_dir / out_name))
                    downloaded.append(playlist_name)
                except (TimeoutError, Error):
                    skipped.append(f"{playlist_name} (export click/download failed)")
        finally:
            context.close()

    return downloaded, skipped
