from __future__ import annotations

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
from typing import Dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "playlist-checker-config.json"
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

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


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
