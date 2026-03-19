from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from flask import Flask, flash, render_template, request
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
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sanitize_filename(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "", name).strip().replace(" ", "_")


def parse_artists(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r";|,|\band\b|\bwith\b|\bx\b", value, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


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

    return Track(title=title, artists=artists, source=path.name)


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
            if title:
                rows.append(
                    Track(
                        title=title,
                        artists=parse_artists(artists_text),
                        source=csv_path.name,
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


def compare_tracks(local_tracks: Sequence[Track], playlist_tracks: Sequence[Track]) -> Tuple[List[Track], List[Track]]:
    matched_playlist: set[int] = set()
    extra_local: List[Track] = []

    for local_track in local_tracks:
        local_title = local_track.normalized_title
        match_index: Optional[int] = None

        for idx, playlist_track in enumerate(playlist_tracks):
            if idx in matched_playlist:
                continue
            if local_title != playlist_track.normalized_title:
                continue
            if not artists_overlap(local_track, playlist_track):
                continue
            match_index = idx
            break

        if match_index is None:
            for idx, playlist_track in enumerate(playlist_tracks):
                if idx in matched_playlist:
                    continue
                if local_title == playlist_track.normalized_title:
                    match_index = idx
                    break

        if match_index is not None:
            matched_playlist.add(match_index)
        else:
            extra_local.append(local_track)

    missing_playlist = [
        playlist_track
        for idx, playlist_track in enumerate(playlist_tracks)
        if idx not in matched_playlist
    ]
    return missing_playlist, extra_local


def ensure_playlist_table_ready(page: Page, wait_seconds: int = 180) -> None:
    deadline = time.time() + wait_seconds

    while time.time() < deadline:
        login_visible = page.locator("#loginButton").count() > 0 and page.locator("#loginButton").first.is_visible()
        row_count = page.locator("#playlistsContainer tbody tr").count()

        if row_count > 0:
            return

        if login_visible:
            time.sleep(1)
        else:
            time.sleep(0.5)

    raise TimeoutError("Playlists did not load. Complete Spotify login in the opened browser and retry.")


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
            page.goto("https://exportify.net", wait_until="domcontentloaded")

            if headless:
                login_visible = page.locator("#loginButton").count() > 0 and page.locator("#loginButton").first.is_visible()
                row_count = page.locator("#playlistsContainer tbody tr").count()
                if login_visible and row_count == 0:
                    raise RuntimeError(
                        "Silent mode could not access playlists because Exportify requires Spotify login. "
                        "Run one sync with silent mode off to sign in, then enable silent mode again."
                    )

            ensure_playlist_table_ready(page, wait_seconds=login_wait_seconds)

            row_map = build_playlist_row_map(page)

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
    }


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

    return config


def save_config(config_data: Dict[str, object], config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")


def track_to_row(track: Track) -> Dict[str, str]:
    return {
        "title": track.title,
        "artists": ", ".join(track.artists) if track.artists else "Unknown",
        "source": track.source,
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
) -> Tuple[List[Dict[str, object]], int, int]:
    results: List[Dict[str, object]] = []
    total_missing = 0
    total_extra = 0

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
            "error": None,
        }

        if csv_path is None:
            entry["error"] = f"No Exportify CSV found for playlist '{playlist_name}' in {export_dir}"
            results.append(entry)
            continue

        playlist_tracks = read_exportify_csv(csv_path)
        missing, extra = compare_tracks(local_tracks, playlist_tracks)
        total_missing += len(missing)
        total_extra += len(extra)

        entry["missing"] = [track_to_row(track) for track in missing]
        entry["extra"] = [track_to_row(track) for track in extra]
        entry["missing_count"] = len(missing)
        entry["extra_count"] = len(extra)
        results.append(entry)

    return results, total_missing, total_extra


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

    action = request.form.get("action", "")

    music_root = Path(music_root_input).expanduser() if music_root_input.strip() else None
    export_dir = Path(export_dir_input).expanduser()
    profile_dir = Path(profile_dir_input).expanduser()

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
            }
        )
    except OSError:
        flash("Could not save settings file. Check write permissions in app directory.", "warning")

    results: List[Dict[str, object]] = []
    total_missing = 0
    total_extra = 0

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
                except RuntimeError as exc:
                    flash(str(exc), "warning")

        if action == "compare":
            if not mapping:
                flash("No folder-to-playlist mappings selected.", "error")
            elif not music_root or not music_root.exists() or not music_root.is_dir():
                flash("Set a valid local music root folder first.", "error")
            else:
                results, total_missing, total_extra = build_comparison_results(
                    music_root=music_root,
                    export_dir=export_dir,
                    mapping=mapping,
                )

    mapping_rows = [{"folder": folder, "playlist": playlist} for folder, playlist in mapping]

    return render_template(
        "index.html",
        music_root=music_root_input,
        export_dir=export_dir_input,
        profile_dir=profile_dir_input,
        overrides=overrides_input,
        discovered_folders=discovered_folders,
        selected_folders=selected_folders,
        selected_folders_raw=",".join(selected_folders),
        silent_sync=silent_sync,
        mapping_rows=mapping_rows,
        results=results,
        total_missing=total_missing,
        total_extra=total_extra,
    )


@app.route("/health")
def health() -> str:
    return "ok"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
