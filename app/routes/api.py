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


@api_bp.route("/fix_artist", methods=["POST"])
def fix_artist():
    """Rewrite the ARTIST metadata tag in a local audio file."""
    data = request.get_json(silent=True) or {}
    file_path = (data.get("file_path") or "").strip()
    display_artist = (data.get("display_artist") or "").strip()
    all_artists = data.get("all_artists") or []
    primary_artist = all_artists[0] if all_artists else display_artist

    if not file_path:
        return jsonify({"error": "Missing file_path"}), 400
    if not display_artist:
        return jsonify({"error": "Missing display_artist"}), 400

    saved_config = load_saved_config()
    music_root = str(saved_config.get("music_root", ""))
    if not music_root:
        return jsonify({"error": "No music root configured"}), 400

    target = Path(file_path).resolve()
    root = Path(music_root).expanduser().resolve()

    # Safety: only allow modifying files inside the music root
    if not str(target).startswith(str(root)):
        return jsonify({"error": "Path is outside music root"}), 403

    if not target.exists():
        return jsonify({"error": "File not found"}), 404

    if target.suffix.lower() not in AUDIO_EXTENSIONS:
        return jsonify({"error": "Not an audio file"}), 400

    try:
        import mutagen
        audio = mutagen.File(str(target))
        if audio is None:
            return jsonify({"error": "Could not read audio file metadata"}), 400

        old_artist = None
        suffix = target.suffix.lower()

        if suffix == ".flac" or hasattr(audio, "tags") and isinstance(audio.tags, dict):
            # Vorbis comments (FLAC, OGG, OPUS)
            if hasattr(audio, "tags") and audio.tags is not None:
                if "artist" in audio.tags:
                    old_val = audio.tags["artist"]
                    old_artist = str(old_val[0]) if isinstance(old_val, list) else str(old_val)
            audio["artist"] = [display_artist]
            audio["artists"] = all_artists
            audio["albumartist"] = [primary_artist]
            audio.save()

        elif suffix == ".mp3":
            # ID3 tags
            from mutagen.id3 import TPE1, TXXX, TPE2
            if audio.tags and "TPE1" in audio.tags:
                old_tag = audio.tags["TPE1"]
                old_artist = str(old_tag.text[0]) if hasattr(old_tag, "text") and old_tag.text else str(old_tag)
            if audio.tags is None:
                audio.add_tags()
            
            # Remove any existing TPE1 or TXXX:ARTISTS tags
            audio.tags.delall("TPE1")
            audio.tags.delall("TXXX:ARTISTS")
            audio.tags.delall("TPE2")
            
            audio.tags.add(TPE1(encoding=3, text=[display_artist]))
            audio.tags.add(TPE2(encoding=3, text=[primary_artist]))
            if all_artists:
                audio.tags.add(TXXX(encoding=3, desc="ARTISTS", text=all_artists))
            audio.save()

        elif suffix in (".m4a", ".aac", ".alac"):
            # MP4/M4A tags
            if audio.tags and "\xa9ART" in audio.tags:
                old_val = audio.tags["\xa9ART"]
                old_artist = str(old_val[0]) if isinstance(old_val, list) else str(old_val)
            audio["\xa9ART"] = [display_artist]
            audio["aART"] = [primary_artist]
            audio.save()

        else:
            # Generic fallback — try Vorbis-style
            if hasattr(audio, "tags") and audio.tags is not None:
                if "artist" in audio.tags:
                    old_val = audio.tags["artist"]
                    old_artist = str(old_val[0]) if isinstance(old_val, list) else str(old_val)
            audio["artist"] = [display_artist]
            audio["artists"] = all_artists
            audio["albumartist"] = [primary_artist]
            audio.save()

        return jsonify({
            "status": "fixed",
            "file": str(target),
            "old_artist": old_artist,
            "display_artist": display_artist,
            "all_artists": all_artists,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/fix_title", methods=["POST"])
def fix_title():
    """Rewrite the TITLE metadata tag in a local audio file."""
    data = request.get_json(silent=True) or {}
    file_path = (data.get("file_path") or "").strip()
    new_title = (data.get("new_title") or "").strip()

    if not file_path:
        return jsonify({"error": "Missing file_path"}), 400
    if not new_title:
        return jsonify({"error": "Missing new_title"}), 400

    saved_config = load_saved_config()
    music_root = str(saved_config.get("music_root", ""))
    if not music_root:
        return jsonify({"error": "No music root configured"}), 400

    target = Path(file_path).resolve()
    root = Path(music_root).expanduser().resolve()

    if not str(target).startswith(str(root)):
        return jsonify({"error": "Path is outside music root"}), 403

    if not target.exists():
        return jsonify({"error": "File not found"}), 404

    if target.suffix.lower() not in AUDIO_EXTENSIONS:
        return jsonify({"error": "Not an audio file"}), 400

    try:
        import mutagen
        audio = mutagen.File(str(target))
        if audio is None:
            return jsonify({"error": "Could not read audio file metadata"}), 400

        suffix = target.suffix.lower()

        if suffix == ".flac" or hasattr(audio, "tags") and isinstance(audio.tags, dict):
            audio["title"] = [new_title]
            audio.save()

        elif suffix == ".mp3":
            from mutagen.id3 import TIT2
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("TIT2")
            audio.tags.add(TIT2(encoding=3, text=[new_title]))
            audio.save()

        elif suffix in (".m4a", ".aac", ".alac"):
            audio["\xa9nam"] = [new_title]
            audio.save()

        else:
            audio["title"] = [new_title]
            audio.save()

        return jsonify({"status": "fixed", "file": str(target), "new_title": new_title})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/fix_album", methods=["POST"])
def fix_album():
    """Rewrite the ALBUM metadata tag in a local audio file."""
    data = request.get_json(silent=True) or {}
    file_path = (data.get("file_path") or "").strip()
    new_album = (data.get("new_album") or "").strip()

    if not file_path:
        return jsonify({"error": "Missing file_path"}), 400
    if not new_album:
        return jsonify({"error": "Missing new_album"}), 400

    saved_config = load_saved_config()
    music_root = str(saved_config.get("music_root", ""))
    if not music_root:
        return jsonify({"error": "No music root configured"}), 400

    target = Path(file_path).resolve()
    root = Path(music_root).expanduser().resolve()

    if not str(target).startswith(str(root)):
        return jsonify({"error": "Path is outside music root"}), 403

    if not target.exists():
        return jsonify({"error": "File not found"}), 404

    if target.suffix.lower() not in AUDIO_EXTENSIONS:
        return jsonify({"error": "Not an audio file"}), 400

    try:
        import mutagen
        audio = mutagen.File(str(target))
        if audio is None:
            return jsonify({"error": "Could not read audio file metadata"}), 400

        suffix = target.suffix.lower()

        if suffix == ".flac" or hasattr(audio, "tags") and isinstance(audio.tags, dict):
            audio["album"] = [new_album]
            audio.save()

        elif suffix == ".mp3":
            from mutagen.id3 import TALB
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("TALB")
            audio.tags.add(TALB(encoding=3, text=[new_album]))
            audio.save()

        elif suffix in (".m4a", ".aac", ".alac"):
            audio["\xa9alb"] = [new_album]
            audio.save()

        else:
            audio["album"] = [new_album]
            audio.save()

        return jsonify({"status": "fixed", "file": str(target), "new_album": new_album})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/fix_filename", methods=["POST"])
def fix_filename():
    """Rename a local audio file to match the expected Spotify-derived filename."""
    data = request.get_json(silent=True) or {}
    file_path = (data.get("file_path") or "").strip()
    new_filename = (data.get("new_filename") or "").strip()

    if not file_path:
        return jsonify({"error": "Missing file_path"}), 400
    if not new_filename:
        return jsonify({"error": "Missing new_filename"}), 400

    saved_config = load_saved_config()
    music_root = str(saved_config.get("music_root", ""))
    if not music_root:
        return jsonify({"error": "No music root configured"}), 400

    target = Path(file_path).resolve()
    root = Path(music_root).expanduser().resolve()

    if not str(target).startswith(str(root)):
        return jsonify({"error": "Path is outside music root"}), 403

    if not target.exists():
        return jsonify({"error": "File not found"}), 404

    if target.suffix.lower() not in AUDIO_EXTENSIONS:
        return jsonify({"error": "Not an audio file"}), 400

    new_path = target.parent / new_filename

    if new_path.exists() and new_path != target:
        return jsonify({"error": f"Target file already exists: {new_filename}"}), 409

    try:
        target.rename(new_path)
        return jsonify({
            "status": "renamed",
            "old_file": str(target),
            "new_file": str(new_path),
        })
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
