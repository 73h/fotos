import os
from pathlib import Path

from flask import Flask

from ..config import AppConfig
from ..index.store import ensure_schema


def _load_env_timelapse(project_root: Path) -> None:
    """Laedt .env.timelapse aus dem Projektstamm, falls vorhanden."""
    env_file = project_root / ".env.timelapse"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: $env:KEY="VALUE"
        if line.startswith("$env:"):
            line = line[5:]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def create_app(
    app_config: AppConfig,
    custom_db_path: str | None = None,
    custom_cache_dir: str | None = None,
) -> Flask:
    app = Flask(__name__)

    # .env.timelapse automatisch laden, falls vorhanden
    project_root = Path(__file__).resolve().parents[3]
    _load_env_timelapse(project_root)

    app.config["APP_CONFIG"] = app_config
    app.config["DB_PATH"] = app_config.resolve_db_path(custom_db_path)
    app.config["CACHE_DIR"] = app_config.resolve_cache_dir(custom_cache_dir)
    app.config["THUMB_SIZE"] = 360
    ensure_schema(app.config["DB_PATH"])

    from .routes import web_blueprint

    app.register_blueprint(web_blueprint)
    return app

