"""Entry point for Local Playlist Checker."""
from __future__ import annotations

import os

from app import create_app

application = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("DEBUG", "true").lower() in {"1", "true", "yes", "on"}
    application.run(host=host, port=port, debug=debug)
