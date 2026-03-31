import hashlib
from pathlib import Path

from PIL import Image, ImageOps


def _build_thumb_key(image_path: Path, size: int, modified_ts: float) -> str:
    raw = f"{image_path.resolve()}|{size}|{modified_ts}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def ensure_thumbnail(image_path: Path, cache_root: Path, size: int) -> Path | None:
    if not image_path.exists() or not image_path.is_file():
        return None

    try:
        modified_ts = image_path.stat().st_mtime
    except OSError:
        return None

    key = _build_thumb_key(image_path=image_path, size=size, modified_ts=modified_ts)
    thumb_path = cache_root / "thumbnails" / key[:2] / f"{key}.jpg"
    if thumb_path.exists():
        return thumb_path

    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((size, size))
            img.save(thumb_path, format="JPEG", quality=82, optimize=True)
    except Exception:
        return None

    return thumb_path

