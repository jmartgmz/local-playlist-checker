@echo off
REM Run Local Playlist Checker on Windows

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo Virtual environment activated
) else (
    echo Error: Virtual environment not found at .venv\Scripts\activate.bat
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)

echo Starting Local Playlist Checker...
python app.py

pause
