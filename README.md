# Local Playlist Checker (Exportify + Local Files)

This app compares local downloaded songs in folders (for example `OTAKU`) with matching Spotify playlists by name, without using the Spotify API directly in your code.

It automates [Exportify](https://github.com/pavelkomarov/exportify) in a browser session, downloads playlist CSV files, then shows a dashboard with:

- Songs missing locally (in playlist but not in folder)
- Extra local songs (in folder but not in playlist)

## Why this avoids API usage

- The app does not call Spotify Web API itself.
- It drives Exportify in your browser and uses CSV exports as the source of truth.

## Requirements

- Python 3.10+
- A local music root folder containing subfolders that match playlist names
- Internet access to open Exportify and sign in once

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Install Playwright browser:

```bash
playwright install chromium
```

3. Run the dashboard:

```bash
python app.py
```

## How to use

1. Open your browser at `http://127.0.0.1:5000`.
2. Set your local music root folder.
3. Select folders to track using the comma-separated field (example: `OTAKU, Chill Vibes`).
4. Optionally set name overrides with `folder=playlist` format.
5. Optionally enable **Silent sync (headless browser)** if you do not want a browser window.
6. Click **Sync Playlist CSVs from Exportify**.
7. If silent sync is disabled, a Chromium window opens:
   - If needed, click login and sign in to Spotify.
   - Wait for playlists to load.
8. Click **Compare Local vs Playlist**.
9. Review missing and extra tracks per playlist and overall totals.

## Notes and limitations

- Filename parsing is best-effort. Files named like `Artist - Song.mp3` match most accurately.
- If your naming style is different, comparison still falls back to title-only matching.
- Exportify profile data is stored in `.exportify-profile` so you usually do not need to log in every run.
- For first-time login, run one non-silent sync so Spotify auth is saved in the profile, then silent mode can run without opening a visible browser.
- CSV exports are stored in `exports/` by default.
- Export CSV folder and Exportify profile folder are fixed to `exports/` and `.exportify-profile/` in this UI version.
- Dashboard settings are saved automatically to `.playlist-checker-config.json` so paths and mappings persist between runs.

## Legal and Terms

Use this for personal library management and follow Spotify/Exportify terms.
