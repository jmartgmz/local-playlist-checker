#!/bin/bash
# Run Local Playlist Checker on Linux/macOS
# This script lives in scripts/ — step up to the project root first.

cd "$(dirname "$0")/.."

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo "Virtual environment activated"
else
    echo "Error: Virtual environment not found at .venv/bin/activate"
    echo "Please run: python3 -m venv .venv"
    exit 1
fi

echo "Starting Local Playlist Checker..."
python run.py
