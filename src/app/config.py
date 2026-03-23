from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    workspace_root: Path
    db_path: Path = field(default=Path("data/photo_index.db"))
    supported_extensions: tuple[str, ...] = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
    )

    @staticmethod
    def from_workspace(workspace_root: Path) -> "AppConfig":
        return AppConfig(workspace_root=workspace_root)

    def resolve_db_path(self, custom_db_path: str | None = None) -> Path:
        db_candidate = Path(custom_db_path) if custom_db_path else self.db_path
        if not db_candidate.is_absolute():
            db_candidate = self.workspace_root / db_candidate
        return db_candidate

