from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw, ImageFont
import json as json_lib

from ..index.store import ensure_schema

_ALLOWED_RATIOS: dict[str, tuple[int, int]] = {
    "3:2": (3, 2),
    "4:3": (4, 3),
    "16:9": (16, 9),
    "1:1": (1, 1),
}


@dataclass(frozen=True)
class AlbumZipExportResult:
    zip_path: Path
    exported_count: int


def parse_ratio(value: str) -> tuple[int, int]:
    ratio = _ALLOWED_RATIOS.get(value.strip())
    if ratio is None:
        allowed = ", ".join(_ALLOWED_RATIOS)
        raise ValueError(f"Unbekanntes Format '{value}'. Erlaubt: {allowed}")
    return ratio


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned or "all"


def _safe_entry_name(index: int, photo_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", photo_path.stem).strip("._") or f"foto_{index:04d}"
    return f"{index:04d}_{stem}.jpg"


def _get_album_name_and_paths(db_path: Path, album_id: int) -> tuple[str, list[Path]]:
    with sqlite3.connect(db_path) as conn:
        album_row = conn.execute("SELECT name FROM albums WHERE id = ?", (album_id,)).fetchone()
        if album_row is None:
            raise ValueError("Album nicht gefunden.")

        rows = conn.execute(
            """
            SELECT ph.path
            FROM album_photos ap
            JOIN photos ph ON ph.path = ap.photo_path
            WHERE ap.album_id = ?
            ORDER BY COALESCE(ph.taken_ts, ph.modified_ts) ASC
            """,
            (album_id,),
        ).fetchall()

    return str(album_row[0]), [Path(str(row[0])) for row in rows]


def _load_person_mean_embedding(db_path: Path, person_name: str):
    try:
        import numpy as np
    except Exception:
        return None

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.vector_json
            FROM person_refs r
            JOIN persons p ON p.id = r.person_id
            WHERE lower(p.name) = lower(?)
            """,
            (person_name.strip(),),
        ).fetchall()

    vectors = []
    for row in rows:
        try:
            vectors.append(np.array(json.loads(row[0]), dtype=np.float32))
        except Exception:
            continue
    if not vectors:
        return None

    mean_vec = np.mean(vectors, axis=0).astype(np.float32)
    norm = float(np.linalg.norm(mean_vec))
    if norm <= 0:
        return None
    return mean_vec / norm


def _cosine_similarity(vec_a, vec_b) -> float:
    import numpy as np

    na = float(np.linalg.norm(vec_a))
    nb = float(np.linalg.norm(vec_b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (na * nb))


def _detect_target_face_box(photo_path: Path, mean_embedding):
    if mean_embedding is None:
        return None

    try:
        import cv2
        import numpy as np
        from ..persons.embeddings import InsightFaceBackend, resolve_backend
    except Exception:
        return None

    try:
        image = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)
        if image is None:
            return None
        backend = resolve_backend(None)
        if not isinstance(backend, InsightFaceBackend):
            return None
        faces = backend._app.get(image)
        if not faces:
            return None

        best_face = None
        best_score = -1.0
        for face in faces:
            emb = getattr(face, "embedding", None)
            if emb is None:
                continue
            score = _cosine_similarity(np.asarray(emb, dtype=np.float32), mean_embedding)
            if score > best_score:
                best_score = score
                best_face = face

        if best_face is None or best_score < 0.18:
            return None

        bbox = getattr(best_face, "bbox", None)
        if bbox is None or not hasattr(bbox, "__iter__"):
            return None
        x1, y1, x2, y2 = [float(v) for v in bbox]
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2
    except Exception:
        return None


def _detect_person_boxes(photo_path: Path) -> list[tuple[float, float, float, float]]:
    try:
        from ..detectors.labels import detect_person_boxes

        raw_boxes = detect_person_boxes(photo_path)
    except Exception:
        return []

    boxes: list[tuple[float, float, float, float]] = []
    for box in raw_boxes:
        try:
            x1, y1, x2, y2 = [float(v) for v in box[:4]]
            if x2 > x1 and y2 > y1:
                boxes.append((x1, y1, x2, y2))
        except Exception:
            continue
    return boxes


def _compute_crop_box(
    width: int,
    height: int,
    ratio_w: int,
    ratio_h: int,
    target_box: tuple[float, float, float, float] | None,
    people_boxes: list[tuple[float, float, float, float]],
) -> tuple[int, int, int, int]:
    aspect = ratio_w / ratio_h

    if width / height >= aspect:
        max_crop_h = float(height)
        max_crop_w = max_crop_h * aspect
    else:
        max_crop_w = float(width)
        max_crop_h = max_crop_w / aspect

    min_crop_w = max_crop_w * 0.62
    min_crop_h = max_crop_h * 0.62

    if people_boxes:
        ux1 = min(box[0] for box in people_boxes)
        uy1 = min(box[1] for box in people_boxes)
        ux2 = max(box[2] for box in people_boxes)
        uy2 = max(box[3] for box in people_boxes)
        union_w = max(1.0, ux2 - ux1)
        union_h = max(1.0, uy2 - uy1)
        min_crop_w = max(min_crop_w, union_w * 1.15)
        min_crop_h = max(min_crop_h, union_h * 1.15)

    if target_box is not None:
        tx1, ty1, tx2, ty2 = target_box
        target_w = max(1.0, tx2 - tx1)
        target_h = max(1.0, ty2 - ty1)
        target_rel = (target_w * target_h) / max(1.0, width * height)

        min_crop_w = max(min_crop_w, target_w * 2.4)
        min_crop_h = max(min_crop_h, target_h * 2.4)

        if target_rel >= 0.08:
            min_crop_w = max(min_crop_w, max_crop_w * 0.9)
            min_crop_h = max(min_crop_h, max_crop_h * 0.9)

    needed_w = max(min_crop_w, min_crop_h * aspect)
    needed_h = needed_w / aspect
    if needed_h > max_crop_h:
        needed_h = max(min_crop_h, max_crop_h)
        needed_w = needed_h * aspect

    crop_w = min(max_crop_w, max(min_crop_w, needed_w))
    crop_h = min(max_crop_h, max(min_crop_h, crop_w / aspect))
    crop_w = min(max_crop_w, crop_h * aspect)

    if target_box is not None:
        cx = (target_box[0] + target_box[2]) * 0.5
        cy = (target_box[1] + target_box[3]) * 0.5
    elif people_boxes:
        cx = (min(box[0] for box in people_boxes) + max(box[2] for box in people_boxes)) * 0.5
        cy = (min(box[1] for box in people_boxes) + max(box[3] for box in people_boxes)) * 0.5
    else:
        cx = width * 0.5
        cy = height * 0.5

    # Pixelgenaue Seitenverhaeltnisse erzwingen (z.B. 16:9 exakt).
    scale = max(1, int(min(crop_w / ratio_w, crop_h / ratio_h)))
    crop_w_int = max(ratio_w, scale * ratio_w)
    crop_h_int = max(ratio_h, scale * ratio_h)

    left = max(0.0, min(cx - crop_w_int * 0.5, width - crop_w_int))
    top = max(0.0, min(cy - crop_h_int * 0.5, height - crop_h_int))
    left_int = int(round(left))
    top_int = int(round(top))
    right_int = left_int + crop_w_int
    bottom_int = top_int + crop_h_int

    if right_int > width:
        shift = right_int - width
        left_int -= shift
        right_int -= shift
    if bottom_int > height:
        shift = bottom_int - height
        top_int -= shift
        bottom_int -= shift

    left_int = max(0, left_int)
    top_int = max(0, top_int)
    return left_int, top_int, right_int, bottom_int


def _crop_image_to_ratio(
    image: Image.Image,
    ratio_w: int,
    ratio_h: int,
    target_box: tuple[float, float, float, float] | None,
    people_boxes: list[tuple[float, float, float, float]],
) -> Image.Image:
    width, height = image.size
    left, top, right, bottom = _compute_crop_box(width, height, ratio_w, ratio_h, target_box, people_boxes)
    if right <= left or bottom <= top:
        return image.copy()
    return image.crop((left, top, right, bottom))


def _get_place_name_from_coords(latitude: float, longitude: float) -> str | None:
    """Nutzt Reverse-Geocoding (einfache Fallback-Methode ohne API)."""
    try:
        from urllib.request import Request, urlopen

        url = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?lat={latitude}&lon={longitude}&format=json&accept-language=de"
        )
        request_obj = Request(
            url,
            headers={
                "User-Agent": "fotos-export/1.0",
                "Accept": "application/json",
            },
        )
        with urlopen(request_obj, timeout=5) as response:
            data = json_lib.loads(response.read().decode("utf-8"))
            address = data.get("address", {})
            city = address.get("city") or address.get("town") or address.get("village")
            if city:
                return str(city).strip()
            return None
    except Exception:
        return None


def _get_image_metadata(db_path: Path, photo_path: Path) -> dict[str, str | None]:
    """Lädt Metadaten aus der DB für ein Foto (Datum, Ort)."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT taken_ts, exif_json FROM photos WHERE path = ?",
                (str(photo_path),),
            ).fetchone()
            if not row:
                return {}

            taken_ts, exif_json = row
            metadata = {}

            if taken_ts:
                import datetime
                dt = datetime.datetime.fromtimestamp(float(taken_ts))
                metadata["date"] = dt.strftime("%d.%m.%Y")

            if exif_json:
                try:
                    exif = json_lib.loads(exif_json)
                    lat = exif.get("latitude")
                    lon = exif.get("longitude")
                    if lat is not None and lon is not None:
                        place = _get_place_name_from_coords(float(lat), float(lon))
                        if place:
                            metadata["place"] = place
                except Exception:
                    pass

            return metadata
    except Exception:
        return {}


def _draw_metadata_overlay(image: Image.Image, date_text: str, place_text: str | None) -> Image.Image:
    """Zeichnet Ort, Datum rechts unten auf das Bild mit dynamischem Kontrasting."""
    try:
        draw = ImageDraw.Draw(image)
        width, height = image.size

        # Schriftgröße: 5% der Bildhöhe
        font_size = max(int(height * 0.05), 12)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        # Format: "Ort, Datum" oder nur "Datum"
        if place_text:
            overlay_text = f"{place_text}, {date_text}"
        else:
            overlay_text = date_text

        bbox = draw.textbbox((0, 0), overlay_text, font=font)
        text_width = bbox[2] - bbox[0] + 4
        text_height = bbox[3] - bbox[1] + 4

        padding = max(2, int(width / 200))
        x = width - text_width - padding
        y = height - text_height - padding

        sample_region = image.crop((x, y, min(x + text_width, width), min(y + text_height, height)))
        avg_color = sample_region.convert("L").getextrema()
        avg_brightness = sum(avg_color) / 2 if avg_color else 128

        text_color = (255, 255, 255) if avg_brightness < 128 else (0, 0, 0)
        outline_color = (0, 0, 0) if avg_brightness < 128 else (255, 255, 255)

        outline_width = 1
        for adj_x in [-outline_width, 0, outline_width]:
            for adj_y in [-outline_width, 0, outline_width]:
                if adj_x != 0 or adj_y != 0:
                    draw.text((x + adj_x, y + adj_y), overlay_text, font=font, fill=outline_color)

        draw.text((x, y), overlay_text, font=font, fill=text_color)
        return image
    except Exception:
        return image


def export_album_zip(
    db_path: Path,
    cache_dir: Path,
    album_id: int,
    ratio_text: str,
    person_name: str | None = None,
    add_metadata_overlay: bool = False,
) -> AlbumZipExportResult:
    ensure_schema(db_path)
    ratio_w, ratio_h = parse_ratio(ratio_text)

    album_name, photo_paths = _get_album_name_and_paths(db_path, album_id)
    if not photo_paths:
        raise ValueError("Album ist leer.")

    normalized_person = (person_name or "").strip()
    mean_embedding = _load_person_mean_embedding(db_path, normalized_person) if normalized_person else None

    exports_dir = cache_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    person_slug = _slug(normalized_person) if normalized_person else "all"
    album_slug = _slug(album_name)
    ratio_slug = ratio_text.replace(":", "x")
    zip_path = exports_dir / f"album_{album_id}_{album_slug}_{person_slug}_{ratio_slug}.zip"

    exported_count = 0
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, photo_path in enumerate(photo_paths, start=1):
            if not photo_path.exists() or not photo_path.is_file():
                continue

            target_box = _detect_target_face_box(photo_path, mean_embedding) if normalized_person else None
            people_boxes = _detect_person_boxes(photo_path)
            if target_box is not None:
                people_boxes = [target_box, *people_boxes]

            try:
                with Image.open(photo_path) as image:
                    image = ImageOps.exif_transpose(image)
                    rgb = image.convert("RGB")
                    cropped = _crop_image_to_ratio(
                        image=rgb,
                        ratio_w=ratio_w,
                        ratio_h=ratio_h,
                        target_box=target_box,
                        people_boxes=people_boxes,
                    )

                    # Optionales Metadaten-Overlay
                    if add_metadata_overlay:
                        metadata = _get_image_metadata(db_path, photo_path)
                        if metadata.get("date"):
                            place_text = metadata.get("place")
                            cropped = _draw_metadata_overlay(cropped, metadata["date"], place_text)

                buffer = io.BytesIO()
                cropped.save(buffer, format="JPEG", quality=92)
                archive.writestr(_safe_entry_name(index=index, photo_path=photo_path), buffer.getvalue())
                exported_count += 1
            except Exception:
                continue

    if exported_count == 0:
        raise ValueError("Keine exportierbaren Bilder im Album gefunden.")

    return AlbumZipExportResult(zip_path=zip_path, exported_count=exported_count)

