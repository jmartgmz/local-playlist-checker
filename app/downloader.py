from __future__ import annotations

import threading
import uuid
from typing import Dict

download_jobs: Dict[str, Dict[str, object]] = {}
download_lock = threading.Lock()


def start_download(spotify_url: str, output_dir: str, folder: str = "") -> str:
    """Queue a SpotiFLAC download job and start it in a background thread.

    Returns the job_id string.
    """
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
    return job_id


def get_job(job_id: str) -> Dict[str, object] | None:
    return download_jobs.get(job_id)


def get_all_jobs() -> Dict[str, Dict[str, object]]:
    return download_jobs


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
