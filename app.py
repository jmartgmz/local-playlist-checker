from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from flask import Flask, flash, jsonify, render_template, request
from playwright.sync_api import BrowserContext, Error, Page, TimeoutError, sync_playwright

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".wav",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".alac",
}

DEFAULT_EXPORT_DIR = Path("exports")
DEFAULT_PROFILE_DIR = Path(".exportify-profile")
DEFAULT_LOGIN_WAIT_SECONDS = 180
DEFAULT_CONFIG_PATH = Path(".playlist-checker-config.json")
LARGE_DURATION_DISCREPANCY_MS = 15000
MIN_DURATION_THRESHOLD_SECONDS = 1
MAX_DURATION_THRESHOLD_SECONDS = 120
EXCLUDED_DISCOVERED_FOLDERS = {
    ".stfolder",
    ".thumbnails",
    "game ost",
    "instrumental",
    "kanyedits",
    "melancholy",
    "misc demos",
    "odd music",
    "otaku",
    "spanish",
    "unorganized",
}

app = Flask(__name__)
app.secret_key = "local-playlist-checker-dev"


@dataclass
class Track:
    title: str
    artists: List[str]
    source: str
    duration_ms: Optional[int] = None
    spotify_uri: Optional[str] = None
    file_path: Optional[str] = None

    @property
    def normalized_title(self) -> str:
        return normalize_text(self.title)

    @property
    def normalized_artists(self) -> List[str]:
        return [normalize_text(artist) for artist in self.artists if normalize_text(artist)]


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
    return re.sub(r"[\\/:*?\"<>|]", "", name).strip().replace(" ", "_")


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


def probe_audio_duration_ms(path: Path) -> Optional[int]:
    try:
        mutagen_file = __import__("mutagen", fromlist=["File"]).File
    except Exception:
        return None

    try:
        metadata = mutagen_file(path)
    except Exception:
        return None

    if metadata is None or not getattr(metadata, "info", None):
        return None

    length_seconds = getattr(metadata.info, "length", None)
    if length_seconds is None:
        return None

    try:
        return int(float(length_seconds) * 1000)
    except (TypeError, ValueError):
        return None


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

    return Track(
        title=title,
        artists=artists,
        source=path.name,
        duration_ms=probe_audio_duration_ms(path),
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


def artists_overlap(local_track: Track, playlist_track: Track) -> bool:
    local_artists = set(local_track.normalized_artists)
    playlist_artists = set(playlist_track.normalized_artists)
    if not local_artists:
        return True
    return bool(local_artists.intersection(playlist_artists))


def compact_title(value: str) -> str:
    value = value.casefold().strip().replace("_", " ")
    return re.sub(r"\s+", " ", value)


def compare_tracks(
    local_tracks: Sequence[Track],
    playlist_tracks: Sequence[Track],
) -> Tuple[List[Track], List[Track], List[Tuple[Track, Track, str]]]:
    matched_playlist: set[int] = set()
    extra_local: List[Track] = []
    matched_pairs: List[Tuple[Track, Track, str]] = []

    for local_track in local_tracks:
        local_title = local_track.normalized_title
        match_index: Optional[int] = None
        match_quality = "title"
        title_candidates: List[Tuple[int, Track]] = []

        for idx, playlist_track in enumerate(playlist_tracks):
            if idx in matched_playlist:
                continue
            if local_title != playlist_track.normalized_title:
                continue
            title_candidates.append((idx, playlist_track))

        if title_candidates:
            artist_candidates = [
                candidate for candidate in title_candidates if artists_overlap(local_track, candidate[1])
            ]
            candidate_pool = artist_candidates if artist_candidates else title_candidates

            local_raw_title = compact_title(local_track.title)

            def candidate_key(candidate: Tuple[int, Track]) -> Tuple[int, int, int, int]:
                idx, playlist_track = candidate
                exact_title_rank = 0 if local_raw_title == compact_title(playlist_track.title) else 1
                if local_track.duration_ms is None or playlist_track.duration_ms is None:
                    return exact_title_rank, 1, 0, idx
                duration_delta = abs(local_track.duration_ms - playlist_track.duration_ms)
                return exact_title_rank, 0, duration_delta, idx

            match_index = min(candidate_pool, key=candidate_key)[0]

        if match_index is not None:
            matched_playlist.add(match_index)
            playlist_track = playlist_tracks[match_index]
            local_artists = set(local_track.normalized_artists)
            playlist_artists = set(playlist_track.normalized_artists)
            if local_artists and playlist_artists and local_artists.intersection(playlist_artists):
                match_quality = "artist"
            matched_pairs.append((local_track, playlist_track, match_quality))
        else:
            extra_local.append(local_track)

    missing_playlist = [
        playlist_track
        for idx, playlist_track in enumerate(playlist_tracks)
        if idx not in matched_playlist
    ]
    return missing_playlist, extra_local, matched_pairs


def ensure_playlist_table_ready(page: Page, wait_seconds: int = 180, headless: bool = False) -> None:
    """Wait for Exportify playlists to load. Handles login detection and timeouts."""
    deadline = time.time() + wait_seconds
    startup_deadline = time.time() + 15  # Give 15 seconds for initial page load
    page_bootstrapped = False
    consecutive_no_load = 0

    while time.time() < deadline:
        # Check if page elements exist (not just loaded, but callable)
        try:
            login_visible = page.locator("#loginButton").is_visible(timeout=100)
            row_count = page.locator("#playlistsContainer tbody tr").count()
        except:
            # Page not ready yet, wait and retry
            time.sleep(0.5)
            continue

        # Playlists loaded successfully
        if row_count > 0:
            return

        # Mark page as bootstrapped once we can interact with UI elements
        if not page_bootstrapped:
            if time.time() > startup_deadline or login_visible or page.locator("#playlistsContainer").count() > 0:
                page_bootstrapped = True

        # In headless mode, after page is bootstrapped, fail faster if playlists never appear
        if headless and page_bootstrapped:
            if row_count == 0:
                consecutive_no_load += 1
                # 60 iterations * 0.5s = ~30 seconds after bootstrap to give time for load
                if consecutive_no_load >= 60:
                    raise RuntimeError(
                        "Silent mode could not load playlists from Exportify. "
                        "Run one sync with silent mode off to verify playlists load, then enable silent mode again."
                    )
            else:
                consecutive_no_load = 0

        # Sleep based on context
        if login_visible:
            time.sleep(1)  # Show login screen, wait longer between checks
        else:
            time.sleep(0.5)  # Wait shorter if actively loading

    # After full timeout, determine most useful error message
    try:
        login_visible = page.locator("#loginButton").is_visible(timeout=100)
    except:
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
            # Use "load" instead of "domcontentloaded" to wait for more page initialization
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


def default_config() -> Dict[str, object]:
    return {
        "music_root": "",
        "selected_folders": [],
        "overrides": "",
        "silent_sync": True,
        "duration_threshold_seconds": LARGE_DURATION_DISCREPANCY_MS // 1000,
    }


def clamp_duration_threshold_seconds(raw_value: object) -> int:
    if raw_value is None:
        return LARGE_DURATION_DISCREPANCY_MS // 1000
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return LARGE_DURATION_DISCREPANCY_MS // 1000
    return max(MIN_DURATION_THRESHOLD_SECONDS, min(MAX_DURATION_THRESHOLD_SECONDS, value))


def load_saved_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, object]:
    config = default_config()
    if not config_path.exists():
        return config

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return config

    if isinstance(raw, dict):
        config.update(raw)

    if not isinstance(config.get("selected_folders"), list):
        config["selected_folders"] = []
    if not isinstance(config.get("silent_sync"), bool):
        config["silent_sync"] = True
    if not isinstance(config.get("music_root"), str):
        config["music_root"] = ""
    if not isinstance(config.get("overrides"), str):
        config["overrides"] = ""
    config["duration_threshold_seconds"] = clamp_duration_threshold_seconds(
        config.get("duration_threshold_seconds")
    )

    return config


def save_config(config_data: Dict[str, object], config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")


def track_to_row(track: Track, folder: str = "") -> Dict[str, str]:
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


def build_comparison_results(
    music_root: Path,
    export_dir: Path,
    mapping: Sequence[Tuple[str, str]],
    duration_threshold_ms: int,
) -> Tuple[List[Dict[str, object]], int, int, int]:
    results: List[Dict[str, object]] = []
    total_missing = 0
    total_extra = 0
    total_duration_discrepancies = 0

    for folder, playlist_name in mapping:
        local_folder = music_root / folder
        csv_path = find_playlist_csv(export_dir, playlist_name)

        local_tracks = scan_local_tracks(local_folder)
        entry: Dict[str, object] = {
            "playlist_name": playlist_name,
            "folder": folder,
            "local_folder": str(local_folder),
            "local_count": len(local_tracks),
            "csv_path": str(csv_path) if csv_path else None,
            "missing": [],
            "extra": [],
            "duration_discrepancies": [],
            "error": None,
        }

        if csv_path is None:
            entry["error"] = f"No Exportify CSV found for playlist '{playlist_name}' in {export_dir}"
            results.append(entry)
            continue

        playlist_tracks = read_exportify_csv(csv_path)
        missing, extra, matched_pairs = compare_tracks(local_tracks, playlist_tracks)
        sorted_missing = sorted(
            missing,
            key=lambda track: (
                not bool(track.normalized_artists),
                track.normalized_artists[0] if track.normalized_artists else "",
                track.normalized_title,
            ),
        )
        duration_discrepancies: List[Dict[str, str]] = []
        for local_track, playlist_track, match_quality in matched_pairs:
            # Duration checks are noisy for title-only fallback matches.
            if match_quality != "artist":
                continue
            if local_track.duration_ms is None or playlist_track.duration_ms is None:
                continue
            delta_ms = abs(local_track.duration_ms - playlist_track.duration_ms)
            if delta_ms >= duration_threshold_ms:
                duration_discrepancies.append(
                    {
                        "title": playlist_track.title,
                        "artists": ", ".join(playlist_track.artists) if playlist_track.artists else "Unknown",
                        "source": local_track.source,
                        "local_duration": format_duration_ms(local_track.duration_ms),
                        "spotify_duration": format_duration_ms(playlist_track.duration_ms),
                        "difference": format_duration_ms(delta_ms),
                    }
                )
        duration_discrepancies.sort(
            key=lambda row: (
                row["artists"].casefold() == "unknown",
                row["artists"].casefold(),
                row["title"].casefold(),
            )
        )

        total_missing += len(missing)
        total_extra += len(extra)
        total_duration_discrepancies += len(duration_discrepancies)

        entry["missing"] = [track_to_row(track, folder=folder) for track in sorted_missing]
        entry["extra"] = [track_to_row(track) for track in extra]
        entry["duration_discrepancies"] = duration_discrepancies
        entry["missing_count"] = len(missing)
        entry["extra_count"] = len(extra)
        entry["duration_discrepancy_count"] = len(duration_discrepancies)
        results.append(entry)

    return results, total_missing, total_extra, total_duration_discrepancies


@app.route("/", methods=["GET", "POST"])
def index() -> str:
    saved_config = load_saved_config()

    if request.method == "POST":
        music_root_input = request.form.get("music_root", str(saved_config.get("music_root", "")))
        export_dir_input = str(DEFAULT_EXPORT_DIR)
        profile_dir_input = str(DEFAULT_PROFILE_DIR)
        overrides_input = request.form.get("overrides", str(saved_config.get("overrides", "")))
        selected_folders_raw = request.form.get("selected_folders", "")
        silent_sync = request.form.get("silent_sync") == "on"
        duration_threshold_seconds = clamp_duration_threshold_seconds(
            request.form.get("duration_threshold_seconds")
        )
    else:
        music_root_input = str(saved_config.get("music_root", ""))
        export_dir_input = str(DEFAULT_EXPORT_DIR)
        profile_dir_input = str(DEFAULT_PROFILE_DIR)
        overrides_input = str(saved_config.get("overrides", ""))
        selected_config_obj = saved_config.get("selected_folders", [])
        selected_config_list = selected_config_obj if isinstance(selected_config_obj, list) else []
        selected_from_config = [str(item) for item in selected_config_list]
        selected_folders_raw = ",".join(selected_from_config)
        silent_sync = bool(saved_config.get("silent_sync", True))
        duration_threshold_seconds = clamp_duration_threshold_seconds(
            saved_config.get("duration_threshold_seconds")
        )

    action = request.form.get("action", "")

    music_root = Path(music_root_input).expanduser() if music_root_input.strip() else None
    export_dir = Path(export_dir_input).expanduser()
    profile_dir = Path(profile_dir_input).expanduser()
    duration_threshold_ms = duration_threshold_seconds * 1000

    discovered_folders = collect_discovered_folders(music_root)
    selected_folders = parse_selected_folders(selected_folders_raw)
    if not selected_folders and discovered_folders and request.method == "POST":
        selected_folders = discovered_folders

    overrides = parse_overrides(overrides_input)
    mapping = build_mapping(selected_folders, overrides)

    try:
        save_config(
            {
                "music_root": music_root_input,
                "selected_folders": selected_folders,
                "overrides": overrides_input,
                "silent_sync": silent_sync,
                "duration_threshold_seconds": duration_threshold_seconds,
            }
        )
    except OSError:
        flash("Could not save settings file. Check write permissions in app directory.", "warning")

    results: List[Dict[str, object]] = []
    total_missing = 0
    total_extra = 0
    total_duration_discrepancies = 0

    if request.method == "POST":
        if action == "sync":
            if not mapping:
                flash("No folder-to-playlist mappings selected.", "error")
            else:
                playlist_names = [playlist for _, playlist in mapping]
                if silent_sync:
                    flash("Running sync silently (headless browser).", "info")
                else:
                    flash(
                        "Chromium will open. If needed, sign in to Spotify on Exportify and wait for playlists to load.",
                        "info",
                    )
                try:
                    downloaded, skipped = download_exportify_csvs(
                        playlist_names=playlist_names,
                        export_dir=export_dir,
                        profile_dir=profile_dir,
                        headless=silent_sync,
                    )
                    if downloaded:
                        flash("Downloaded: " + ", ".join(downloaded), "success")
                    if skipped:
                        flash("Skipped: " + ", ".join(skipped), "warning")
                except (RuntimeError, TimeoutError, Error) as exc:
                    flash(str(exc), "warning")

        if action == "sync_and_compare":
            if not mapping:
                flash("No folder-to-playlist mappings selected.", "error")
            elif not music_root or not music_root.exists() or not music_root.is_dir():
                flash("Set a valid local music root folder first.", "error")
            else:
                playlist_names = [playlist for _, playlist in mapping]
                if silent_sync:
                    flash("Running sync silently (headless browser).", "info")
                else:
                    flash(
                        "Chromium will open. If needed, sign in to Spotify on Exportify and wait for playlists to load.",
                        "info",
                    )
                try:
                    downloaded, skipped = download_exportify_csvs(
                        playlist_names=playlist_names,
                        export_dir=export_dir,
                        profile_dir=profile_dir,
                        headless=silent_sync,
                    )
                    if downloaded:
                        flash("Downloaded: " + ", ".join(downloaded), "success")
                    if skipped:
                        flash("Skipped: " + ", ".join(skipped), "warning")
                    # Now run comparison
                    results, total_missing, total_extra, total_duration_discrepancies = build_comparison_results(
                        music_root=music_root,
                        export_dir=export_dir,
                        mapping=mapping,
                        duration_threshold_ms=duration_threshold_ms,
                    )
                except (RuntimeError, TimeoutError, Error) as exc:
                    flash(str(exc), "warning")

        if action == "compare":
            if not mapping:
                flash("No folder-to-playlist mappings selected.", "error")
            elif not music_root or not music_root.exists() or not music_root.is_dir():
                flash("Set a valid local music root folder first.", "error")
            else:
                results, total_missing, total_extra, total_duration_discrepancies = build_comparison_results(
                    music_root=music_root,
                    export_dir=export_dir,
                    mapping=mapping,
                    duration_threshold_ms=duration_threshold_ms,
                )

    mapping_rows = [{"folder": folder, "playlist": playlist} for folder, playlist in mapping]

    return render_template(
        "index.html",
        music_root=music_root_input,
        export_dir=export_dir_input,
        profile_dir=profile_dir_input,
        discovered_folders=discovered_folders,
        selected_folders=selected_folders,
        selected_folders_raw=",".join(selected_folders),
        silent_sync=silent_sync,
        duration_threshold_seconds=duration_threshold_seconds,
        mapping_rows=mapping_rows,
        results=results,
        total_missing=total_missing,
        total_extra=total_extra,
        total_duration_discrepancies=total_duration_discrepancies,
    )


@app.route("/health")
def health() -> str:
    return "ok"


# ---------------------------------------------------------------------------
# SpotiFLAC download integration
# ---------------------------------------------------------------------------

download_jobs: Dict[str, Dict[str, object]] = {}
download_lock = threading.Lock()


def _run_spotiflac_download(job_id: str, spotify_url: str, output_dir: str) -> None:
    """Background worker that runs a single SpotiFLAC download."""
    download_jobs[job_id]["status"] = "downloading"
    try:
        from SpotiFLAC import SpotiFLAC as spotiflac_download

        with download_lock:
            spotiflac_download(
                url=spotify_url,
                output_dir=output_dir,
                services=["tidal", "spoti", "qobuz", "amazon", "youtube"],
                filename_format="{artist} - {title}",
            )
        download_jobs[job_id]["status"] = "complete"
    except Exception as exc:
        download_jobs[job_id]["status"] = "failed"
        download_jobs[job_id]["error"] = str(exc)


@app.route("/download", methods=["POST"])
def download_track():
    """Accept a Spotify URL and target folder, launch SpotiFLAC in the background."""
    data = request.get_json(silent=True) or {}
    spotify_url = (data.get("spotify_url") or "").strip()
    folder = (data.get("folder") or "").strip()

    if not spotify_url:
        return jsonify({"error": "Missing spotify_url"}), 400

    saved_config = load_saved_config()
    music_root = str(saved_config.get("music_root", ""))
    if not music_root:
        return jsonify({"error": "No music root folder configured"}), 400

    output_dir = str(Path(music_root).expanduser() / folder) if folder else music_root
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "status": "queued",
        "spotify_url": spotify_url,
        "folder": folder,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_spotiflac_download,
        args=(job_id, spotify_url, output_dir),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "queued"})


@app.route("/download_status")
def download_status():
    """Return the status of one or all download jobs."""
    job_id = request.args.get("job_id", "").strip()
    if job_id:
        job = download_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({"job_id": job_id, **job})

    return jsonify(download_jobs)


@app.route("/delete", methods=["POST"])
def delete_track():
    """Delete a local audio file. Validates the path is inside the music root."""
    data = request.get_json(silent=True) or {}
    file_path = (data.get("file_path") or "").strip()

    if not file_path:
        return jsonify({"error": "Missing file_path"}), 400

    saved_config = load_saved_config()
    music_root = str(saved_config.get("music_root", ""))
    if not music_root:
        return jsonify({"error": "No music root configured"}), 400

    target = Path(file_path).resolve()
    root = Path(music_root).expanduser().resolve()

    # Safety: only allow deleting files inside the music root
    if not str(target).startswith(str(root)):
        return jsonify({"error": "Path is outside music root"}), 403

    if not target.exists():
        return jsonify({"error": "File not found"}), 404

    if target.suffix.lower() not in AUDIO_EXTENSIONS:
        return jsonify({"error": "Not an audio file"}), 400

    try:
        target.unlink()
        return jsonify({"status": "deleted", "file": str(target)})
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("DEBUG", "true").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
