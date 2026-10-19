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

    @flask_app.context_processor
    def inject_static_version() -> dict[str, str]:
        css_file = _PROJECT_ROOT / "frontend" / "static" / "style.css"
        try:
            version = str(int(css_file.stat().st_mtime))
        except OSError:
            version = "1.0.0"
        return {"css_version": version}

    @flask_app.after_request
    def add_cache_headers(response):
        if response.content_type and response.content_type.startswith("text/css"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return flask_app
