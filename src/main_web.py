from pathlib import Path

from app.config import AppConfig
from app.web import create_app


if __name__ == "__main__":
    workspace_root = Path(__file__).resolve().parents[1]
    config = AppConfig.from_workspace(workspace_root=workspace_root)
    app = create_app(app_config=config)
    app.run(host="127.0.0.1", port=5000, debug=False)

