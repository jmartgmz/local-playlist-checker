from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from flask import Blueprint, flash, render_template, request

from app.comparator import build_comparison_results
from app.config import (
    DEFAULT_EXPORT_DIR,
    clamp_duration_threshold_seconds,
    load_saved_config,
    save_config,
)
from app.utils import (
    build_mapping,
    collect_discovered_folders,
    parse_overrides,
    parse_selected_folders,
)

main_bp = Blueprint("main", __name__)


@main_bp.route("/", methods=["GET", "POST"])
def index() -> str:
    saved_config = load_saved_config()

    if request.method == "POST":
        music_root_input = request.form.get("music_root", str(saved_config.get("music_root", "")))
        export_dir_input = str(DEFAULT_EXPORT_DIR)
        overrides_input = request.form.get("overrides", str(saved_config.get("overrides", "")))
        selected_folders_raw = request.form.get("selected_folders", "")
        silent_sync = bool(saved_config.get("silent_sync", True))
        duration_threshold_seconds = clamp_duration_threshold_seconds(
            request.form.get("duration_threshold_seconds")
        )
    else:
        music_root_input = str(saved_config.get("music_root", ""))
        export_dir_input = str(DEFAULT_EXPORT_DIR)
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
    total_album_mismatches = 0
    total_title_mismatches = 0

    if request.method == "POST":
        if action in ("sync", "sync_and_compare", "compare"):
            if not mapping:
                flash("No folder-to-playlist mappings selected.", "error")
            elif not music_root or not music_root.exists() or not music_root.is_dir():
                flash("Set a valid local music root folder first.", "error")
            else:
                results, total_missing, total_extra, total_duration_discrepancies, total_album_mismatches, total_title_mismatches = build_comparison_results(
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
        total_album_mismatches=total_album_mismatches,
        total_title_mismatches=total_title_mismatches,
    )


@main_bp.route("/health")
def health() -> str:
    return "ok"
