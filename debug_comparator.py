import json
from pathlib import Path
from app.comparator import build_comparison_results
from app.config import EXPORT_DIR
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('config/playlist-checker-config.json') as f:
    config = json.load(f)

music_root = Path(config['music_root']).expanduser()
export_dir = EXPORT_DIR
mapping = config['mapping']
duration_threshold_ms = config.get('duration_threshold_ms', 5000)

results = build_comparison_results(music_root, export_dir, mapping, duration_threshold_ms)

for r in results:
    for m in r['artist_mismatches']:
        if 'Beneath the Mask' in m['title'] or 'Kanye' in m['local_artists']:
            print('Found mismatch!', m['title'])
            l = m['local_artists']
            s = m['recommended_artist']
            from app.utils import normalize_text, parse_artists
            print('Local:', repr(l))
            print('Spotify:', repr(s))
            print('Eq norm:', normalize_text(l) == normalize_text(s))
            
            local_track_artists = parse_artists(l)
            local_set = {normalize_text(a) for a in local_track_artists}
            
            spotify_track_artists = m['all_spotify_artists']
            spotify_set = {normalize_text(a) for a in spotify_track_artists}
            
            print('Local set:', local_set)
            print('Spotify set:', spotify_set)
            print('Eq set:', local_set == spotify_set)
            
            from app.scanner import scan_local_tracks
            local_folder = music_root / r['folder']
            local_tracks = scan_local_tracks(local_folder)
            for track in local_tracks:
                if 'Beneath the Mask' in track.metadata_title or 'Wouldn' in track.metadata_title:
                    print('Scanned track:', track.metadata_title)
                    print('Artists list:', track.artists)
                    print('Raw artist:', track.metadata_artists_raw)
