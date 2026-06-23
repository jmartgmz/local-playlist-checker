import json
from pathlib import Path
from app.comparator import build_comparison_results
from app.config import DEFAULT_EXPORT_DIR
from app.utils import build_mapping, parse_overrides, parse_selected_folders
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('config/playlist-checker-config.json') as f:
    config = json.load(f)

music_root = Path(config['music_root']).expanduser()
export_dir = DEFAULT_EXPORT_DIR
selected_folders = parse_selected_folders('OTAKU')
overrides = parse_overrides(config.get('overrides', ''))
mapping = build_mapping(selected_folders, overrides)

duration_threshold_ms = config.get('duration_threshold_seconds', 5) * 1000

returns = build_comparison_results(music_root, export_dir, mapping, duration_threshold_ms)
results = returns[0]

for r in results:
    for e in r['missing']:
        if '悔やむ' in e['title']:
            print('MISSING:', e['title'])
    for e in r['extra']:
        if '悔やむ' in e['title'] or '悔やむ' in e['source']:
            print('EXTRA:', e['title'], '(', e['source'], ')')
    for m in r['title_mismatches']:
        if '悔やむ' in m['local_title'] or '悔やむ' in m['spotify_title']:
            print('TITLE MISMATCH:', m['local_title'], '!=', m['spotify_title'])
