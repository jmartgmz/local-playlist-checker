from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from app.config import AUDIO_EXTENSIONS, load_saved_config
from app.downloader import get_all_jobs, get_job, start_download

api_bp = Blueprint("api", __name__)


@api_bp.route("/download", methods=["POST"])
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

    job_id = start_download(spotify_url=spotify_url, output_dir=output_dir, folder=folder)

    return jsonify({"job_id": job_id, "status": "queued"})


@api_bp.route("/download_status")
def download_status():
    """Return the status of one or all download jobs."""
    job_id = request.args.get("job_id", "").strip()
    if job_id:
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({"job_id": job_id, **job})

    return jsonify(get_all_jobs())


@api_bp.route("/delete", methods=["POST"])
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
