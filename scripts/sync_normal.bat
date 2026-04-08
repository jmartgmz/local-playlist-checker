@echo off
REM sync_normal.bat — runs sync_playlists.py with visible console window.
REM Double-click or schedule this; console will be visible during execution.

cd /d "%~dp0.."

powershell -NonInteractive -Command ^
    "& '.\.venv\Scripts\python.exe' '.\scripts\sync_playlists.py' --visible"
