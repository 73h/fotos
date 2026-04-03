from pathlib import Path

from flask import Flask

from ..config import AppConfig
from ..index.store import ensure_schema

def _initialize_settings(db_path: Path) -> None:
    """Initialisiert alle Settings-Module mit DB-Pfad."""
    try:
        from ..detectors.labels import initialize_yolo_settings
        initialize_yolo_settings(db_path)
    except Exception:
        pass

    try:
        from ..persons.service import initialize_person_settings
        initialize_person_settings(db_path)
    except Exception:
        pass

    try:
        from ..persons.embeddings import initialize_insightface_settings
        initialize_insightface_settings(db_path)
    except Exception:
        pass


def create_app(
    app_config: AppConfig,
    custom_db_path: str | None = None,
    custom_cache_dir: str | None = None,
) -> Flask:
    app = Flask(__name__)


    app.config["APP_CONFIG"] = app_config
    app.config["DB_PATH"] = app_config.resolve_db_path(custom_db_path)
    app.config["CACHE_DIR"] = app_config.resolve_cache_dir(custom_cache_dir)
    app.config["THUMB_SIZE"] = 360
    ensure_schema(app.config["DB_PATH"])

    # Initialisiere alle Settings-Module mit DB-Pfad
    _initialize_settings(app.config["DB_PATH"])

    from .routes import web_blueprint

    app.register_blueprint(web_blueprint)
    return app

