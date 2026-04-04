from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.config import LARGE_DURATION_DISCREPANCY_MS
from app.exportify import find_playlist_csv, read_exportify_csv
from app.models import Track
from app.scanner import scan_local_tracks
from app.utils import format_duration_ms, track_to_row


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
            else:
                match_quality = "title"
            matched_pairs.append((local_track, playlist_track, match_quality))
        else:
            extra_local.append(local_track)

    missing_playlist = [
        playlist_track
        for idx, playlist_track in enumerate(playlist_tracks)
        if idx not in matched_playlist
    ]
    return missing_playlist, extra_local, matched_pairs


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
