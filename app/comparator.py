from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.config import LARGE_DURATION_DISCREPANCY_MS
from app.exportify import find_playlist_csv, read_exportify_csv
from app.models import Track
from app.scanner import scan_local_tracks
from app.utils import build_expected_filename, format_duration_ms, track_to_row


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
    unmatched_local: List[Track] = []

    # Pass 1: Exact normalized title matching
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
            unmatched_local.append(local_track)

    # Pass 2: Fallback matching for remaining unmatched local tracks
    for local_track in unmatched_local:
        local_title = local_track.normalized_title
        match_index = None
        fallback_candidates = []

        for idx, playlist_track in enumerate(playlist_tracks):
            if idx in matched_playlist:
                continue

            p_title = playlist_track.normalized_title
            is_substring = bool(local_title and p_title and (local_title in p_title or p_title in local_title) and min(len(local_title), len(p_title)) >= 3)
            has_artist_overlap = artists_overlap(local_track, playlist_track)

            duration_delta = None
            if local_track.duration_ms is not None and playlist_track.duration_ms is not None:
                duration_delta = abs(local_track.duration_ms - playlist_track.duration_ms)

            rank = None
            if is_substring and has_artist_overlap:
                rank = 0
            elif is_substring:
                rank = 1
            elif has_artist_overlap and duration_delta is not None and duration_delta <= 15000:
                rank = 2
            elif duration_delta is not None and duration_delta <= 3000:
                rank = 3

            if rank is not None:
                fallback_candidates.append((rank, duration_delta if duration_delta is not None else 999999, idx))

        if fallback_candidates:
            match_index = min(fallback_candidates)[2]

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
) -> Tuple[List[Dict[str, object]], int, int, int, int, int, int, int]:
    results: List[Dict[str, object]] = []
    total_missing = 0
    total_extra = 0
    total_duration_discrepancies = 0
    total_album_mismatches = 0
    total_title_mismatches = 0
    total_artist_mismatches = 0
    total_filename_mismatches = 0

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
            "album_mismatches": [],
            "title_mismatches": [],
            "artist_mismatches": [],
            "filename_mismatches": [],
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
        album_mismatches: List[Dict[str, str]] = []
        title_mismatches: List[Dict[str, str]] = []
        artist_mismatches: List[Dict[str, str]] = []
        filename_mismatches: List[Dict[str, str]] = []
        # Build a set of Spotify titles that appear more than once in matched pairs.
        # When multiple playlist tracks share the same title (e.g. different albums),
        # the user intentionally adds suffixes like "(Live)" to avoid file overwrites,
        # so we skip filename mismatch checks for those tracks.
        from collections import Counter
        _matched_title_counts = Counter(pt.title for _, pt, _ in matched_pairs)
        _duplicate_titles = {t for t, c in _matched_title_counts.items() if c > 1}
        for local_track, playlist_track, match_quality in matched_pairs:
            # Duration checks are noisy for title-only fallback matches,
            # but still run them when artists normalize to empty (e.g. ".........").
            if match_quality == "artist" or not local_track.normalized_artists:
                if local_track.duration_ms is not None and playlist_track.duration_ms is not None:
                    delta_ms = abs(local_track.duration_ms - playlist_track.duration_ms)
                    if delta_ms > duration_threshold_ms:
                        duration_discrepancies.append(
                            {
                                "title": playlist_track.title,
                                "artists": ", ".join(playlist_track.artists) if playlist_track.artists else "Unknown",
                                "source": local_track.source,
                                "local_duration": format_duration_ms(local_track.duration_ms),
                                "spotify_duration": format_duration_ms(playlist_track.duration_ms),
                                "difference": format_duration_ms(delta_ms),
                                "file_path": local_track.file_path or "",
                            }
                        )
            
            from app.utils import normalize_text
            
            # Check for Album Mismatch
            if local_track.album and playlist_track.album:
                if local_track.album.strip() != playlist_track.album.strip():
                    album_mismatches.append(
                        {
                            "title": playlist_track.title,
                            "artists": ", ".join(playlist_track.artists) if playlist_track.artists else "Unknown",
                            "source": local_track.source,
                            "local_album": local_track.album,
                            "spotify_album": playlist_track.album,
                            "file_path": local_track.file_path or "",
                        }
                    )
                    
            # Check for Title Mismatch
            if local_track.metadata_title and playlist_track.title:
                if local_track.metadata_title.strip() != playlist_track.title.strip():
                    title_mismatches.append(
                        {
                            "artists": ", ".join(playlist_track.artists) if playlist_track.artists else "Unknown",
                            "source": local_track.source,
                            "local_title": local_track.metadata_title,
                            "spotify_title": playlist_track.title,
                            "file_path": local_track.file_path or "",
                        }
                    )

            # Check for Artist Mismatch (Navidrome multi-artist tags)
            # The correct approach for FLAC is:
            #   ARTIST  = display name (e.g. "Kanye West, PARTYNEXTDOOR")
            #   ARTISTS = individual entries (one per artist)
            if local_track.metadata_artists and playlist_track.artists:
                spotify_display = ", ".join(playlist_track.artists)
                local_set = {normalize_text(a, strip_parens=False) for a in local_track.metadata_artists}
                spotify_set = {normalize_text(a, strip_parens=False) for a in playlist_track.artists}

                # Flag if the parsed metadata artists don't match exactly (spelling/missing artists),
                # or if the raw display string doesn't match Spotify's formatting.
                # OR if the file lacks the multi-valued ARTISTS tags needed for Navidrome.
                if (local_set != spotify_set 
                    or local_track.metadata_artists_raw.strip() != spotify_display.strip()
                    or not local_track.has_navidrome_artists):
                    artist_mismatches.append(
                        {
                            "title": playlist_track.title,
                            "source": local_track.source,
                            "local_artists": local_track.metadata_artists_raw,
                            "recommended_artist": spotify_display,
                            "all_spotify_artists": playlist_track.artists,
                            "display_artist": spotify_display,
                            "file_path": local_track.file_path or "",
                        }
                    )

            # Check for Filename Mismatch (skip if title appears multiple times — user
            # intentionally disambiguates with suffixes like "(Live)" to prevent overwrites)
            if local_track.file_path and playlist_track.artists and playlist_track.title and playlist_track.title not in _duplicate_titles:
                import re as _re
                actual_path = Path(local_track.file_path)
                actual_stem = actual_path.stem
                spotify_display = ", ".join(playlist_track.artists)
                expected_stem = build_expected_filename(spotify_display, playlist_track.title)
                
                if actual_stem != expected_stem:
                    # Try stripping leading track numbers (e.g. "01 - ", "1. ", "03_")
                    clean_stem = _re.sub(r"^\d{1,3}[\s._-]+", "", actual_stem)
                    if clean_stem != expected_stem:
                        filename_mismatches.append(
                            {
                                "title": playlist_track.title,
                                "artists": spotify_display,
                                "source": local_track.source,
                                "current_filename": actual_path.name,
                                "expected_filename": expected_stem + actual_path.suffix,
                                "file_path": local_track.file_path,
                            }
                        )
            
        duration_discrepancies.sort(
            key=lambda row: (
                row["artists"].casefold() == "unknown",
                row["artists"].casefold(),
                row["title"].casefold(),
            )
        )
        album_mismatches.sort(
            key=lambda row: (
                row["artists"].casefold() == "unknown",
                row["artists"].casefold(),
                row["title"].casefold(),
            )
        )
        title_mismatches.sort(
            key=lambda row: (
                row["artists"].casefold() == "unknown",
                row["artists"].casefold(),
                row["spotify_title"].casefold(),
            )
        )
        artist_mismatches.sort(
            key=lambda row: (
                row["recommended_artist"].casefold(),
                row["title"].casefold(),
            )
        )
        filename_mismatches.sort(
            key=lambda row: (
                row["artists"].casefold(),
                row["title"].casefold(),
            )
        )

        total_missing += len(missing)
        total_extra += len(extra)
        total_duration_discrepancies += len(duration_discrepancies)
        total_album_mismatches += len(album_mismatches)
        total_title_mismatches += len(title_mismatches)
        total_artist_mismatches += len(artist_mismatches)
        total_filename_mismatches += len(filename_mismatches)

        entry["missing"] = [track_to_row(track, folder=folder) for track in sorted_missing]
        entry["extra"] = [track_to_row(track) for track in extra]
        entry["duration_discrepancies"] = duration_discrepancies
        entry["album_mismatches"] = album_mismatches
        entry["title_mismatches"] = title_mismatches
        entry["artist_mismatches"] = artist_mismatches
        entry["filename_mismatches"] = filename_mismatches
        entry["missing_count"] = len(missing)
        entry["extra_count"] = len(extra)
        entry["duration_discrepancy_count"] = len(duration_discrepancies)
        entry["album_mismatch_count"] = len(album_mismatches)
        entry["title_mismatch_count"] = len(title_mismatches)
        entry["artist_mismatch_count"] = len(artist_mismatches)
        entry["filename_mismatch_count"] = len(filename_mismatches)
        results.append(entry)

    return results, total_missing, total_extra, total_duration_discrepancies, total_album_mismatches, total_title_mismatches, total_artist_mismatches, total_filename_mismatches
