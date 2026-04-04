@echo off
REM sync_hidden.bat — runs sync_playlists.py with no visible window.
REM Double-click or schedule this; no console will appear.

cd /d "%~dp0.."

powershell -WindowStyle Hidden -NonInteractive -Command ^
    "& '.\.venv\Scripts\python.exe' '.\scripts\sync_playlists.py'"
