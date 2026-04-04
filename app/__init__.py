from __future__ import annotations

from pathlib import Path

from flask import Flask

# Resolve the project root (one level above this package directory)
_PROJECT_ROOT = Path(__file__).parent.parent


def create_app() -> Flask:
    """Application factory — creates and configures the Flask app."""
    flask_app = Flask(
        __name__,
        template_folder=str(_PROJECT_ROOT / "frontend" / "templates"),
        static_folder=str(_PROJECT_ROOT / "frontend" / "static"),
    )
    flask_app.secret_key = "local-playlist-checker-dev"

    from app.routes.api import api_bp
    from app.routes.main import main_bp

    flask_app.register_blueprint(main_bp)
    flask_app.register_blueprint(api_bp)

    return flask_app
