from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImageRecord:
    path: Path
    size_bytes: int
    modified_ts: float


def scan_images(root: Path, supported_extensions: tuple[str, ...]) -> list[ImageRecord]:
    records: list[ImageRecord] = []
    if not root.exists():
        return records

    extensions = set(ext.lower() for ext in supported_extensions)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue

        stat = path.stat()
        records.append(
            ImageRecord(path=path, size_bytes=stat.st_size, modified_ts=stat.st_mtime)
        )

    return records

