from flask import Flask

from ..config import AppConfig


def create_app(
    app_config: AppConfig,
    custom_db_path: str | None = None,
    custom_cache_dir: str | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = app_config.resolve_db_path(custom_db_path)
    app.config["CACHE_DIR"] = app_config.resolve_cache_dir(custom_cache_dir)
    app.config["THUMB_SIZE"] = 360

    from .routes import web_blueprint

    app.register_blueprint(web_blueprint)
    return app

