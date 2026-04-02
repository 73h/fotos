import hashlib
import json
import sqlite3
import shlex
from fractions import Fraction
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from PIL import Image

from ..ingest import ImageRecord


ADMIN_CONFIG_DEFAULTS: dict[str, object] = {
    # Index-Einstellungen
    "photo_roots": [],
    "force_reindex": False,
    "index_workers": 1,
    "near_duplicates": False,
    "phash_threshold": 6,
    "rematch_workers": 1,
    # YOLO-Objekterkennung (für maximale Qualität + GPU)
    "yolo_model": "yolov8n.pt",
    "yolo_confidence": 0.25,
    "yolo_device": "0",  # GPU-Device, "cpu" für CPU
    # Personen-Matching (für maximale Qualität)
    "person_backend": "insightface",  # "auto", "insightface" oder "histogram"
    "person_threshold": 0.38,
    "person_top_k": 3,
    "person_full_image_fallback": True,
    # InsightFace-Embeddings (GPU-optimiert)
    "insightface_model": "buffalo_l",
    "insightface_ctx": 0,  # GPU-Device, negative Werte für CPU
    "insightface_det_size": "640,640",
    # Timelapse-AI (GPU wenn verfügbar)
    "timelapse_ai_backend": "auto",
    "timelapse_superres_model": "",
    "timelapse_superres_name": "espcn",
    "timelapse_superres_scale": 2,
    "timelapse_face_onnx_model": "",
    "timelapse_face_onnx_provider": "auto",
    "timelapse_face_onnx_size": 256,
}


@dataclass(frozen=True)
class IndexedPhoto:
    path: str
    labels: list[str]
    size_bytes: int
    modified_ts: float
    duplicate_of_path: str | None = None
    duplicate_kind: str | None = None
    duplicate_score: float | None = None
    person_count: int = 0


def _make_json_serializable(obj):
    if isinstance(obj, Fraction):
        return float(obj)
    if isinstance(obj, dict):
        return {key: _make_json_serializable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(value) for value in obj]
    return obj


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                modified_ts REAL NOT NULL,
                taken_ts REAL,
                sha1 TEXT NOT NULL,
                phash TEXT,
                duplicate_of_path TEXT,
                duplicate_kind TEXT,
                duplicate_score REAL,
                labels_json TEXT NOT NULL,
                search_blob TEXT NOT NULL,
                person_count INTEGER NOT NULL DEFAULT 0,
                exif_json TEXT,
                exif_checked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_sha1 ON photos(sha1)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(phash)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS person_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                source_path TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                backend TEXT NOT NULL DEFAULT 'histogram',
                vector_dim INTEGER NOT NULL DEFAULT 96,
                created_ts REAL NOT NULL,
                FOREIGN KEY(person_id) REFERENCES persons(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photo_person_matches (
                photo_path TEXT NOT NULL,
                person_id INTEGER NOT NULL,
                score REAL NOT NULL,
                smile_score REAL,
                matched_ts REAL NOT NULL,
                person_version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(photo_path, person_id),
                FOREIGN KEY(person_id) REFERENCES persons(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_ts REAL NOT NULL,
                cover_photo_path TEXT,
                updated_ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS album_photos (
                album_id INTEGER NOT NULL,
                photo_path TEXT NOT NULL,
                added_ts REAL NOT NULL,
                PRIMARY KEY(album_id, photo_path),
                FOREIGN KEY(album_id) REFERENCES albums(id),
                FOREIGN KEY(photo_path) REFERENCES photos(path)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_album_photos_album ON album_photos(album_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_album_photos_photo ON album_photos(photo_path)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_config (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            )
            """
        )

        existing_album_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(albums)").fetchall()
        }
        if "cover_photo_path" not in existing_album_columns:
            conn.execute("ALTER TABLE albums ADD COLUMN cover_photo_path TEXT")
        if "updated_ts" not in existing_album_columns:
            conn.execute("ALTER TABLE albums ADD COLUMN updated_ts REAL")

        # Migration fuer bereits bestehende Datenbanken ohne Backend-Metadaten.
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(person_refs)").fetchall()
        }
        if "backend" not in existing_columns:
            conn.execute(
                "ALTER TABLE person_refs ADD COLUMN backend TEXT NOT NULL DEFAULT 'histogram'"
            )
        if "vector_dim" not in existing_columns:
            conn.execute(
                "ALTER TABLE person_refs ADD COLUMN vector_dim INTEGER NOT NULL DEFAULT 96"
            )

        existing_photo_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(photos)").fetchall()
        }
        if "phash" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN phash TEXT")
        if "duplicate_of_path" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN duplicate_of_path TEXT")
        if "duplicate_kind" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN duplicate_kind TEXT")
        if "duplicate_score" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN duplicate_score REAL")
        if "person_count" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN person_count INTEGER NOT NULL DEFAULT 0")
        if "taken_ts" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN taken_ts REAL")
        if "exif_json" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN exif_json TEXT")
        if "exif_checked" not in existing_photo_columns:
            conn.execute("ALTER TABLE photos ADD COLUMN exif_checked INTEGER NOT NULL DEFAULT 0")

        existing_person_match_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(photo_person_matches)").fetchall()
        }
        if "smile_score" not in existing_person_match_columns:
            conn.execute("ALTER TABLE photo_person_matches ADD COLUMN smile_score REAL")
        if "person_version" not in existing_person_match_columns:
            conn.execute("ALTER TABLE photo_person_matches ADD COLUMN person_version INTEGER NOT NULL DEFAULT 1")

        existing_person_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(persons)").fetchall()
        }
        if "version" not in existing_person_columns:
            conn.execute("ALTER TABLE persons ADD COLUMN version INTEGER NOT NULL DEFAULT 1")

        conn.execute(
            """
            UPDATE photos
            SET exif_checked = 1
            WHERE exif_checked = 0
              AND (exif_json IS NOT NULL OR taken_ts IS NOT NULL)
            """
        )
        
        # Erstelle Indizes nach allen Migrationen
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_taken_ts ON photos(taken_ts)")


def _normalize_admin_config(raw_config: dict[str, object]) -> dict[str, object]:
    normalized = dict(ADMIN_CONFIG_DEFAULTS)

    raw_roots = raw_config.get("photo_roots")
    if isinstance(raw_roots, list):
        normalized["photo_roots"] = [str(item).strip() for item in raw_roots if str(item).strip()]

    raw_backend = raw_config.get("person_backend")
    if raw_backend in (None, "", "auto", "insightface", "histogram"):
        normalized["person_backend"] = raw_backend or "insightface"

    normalized["force_reindex"] = bool(raw_config.get("force_reindex", normalized["force_reindex"]))
    normalized["near_duplicates"] = bool(raw_config.get("near_duplicates", normalized["near_duplicates"]))

    try:
        normalized["index_workers"] = max(1, int(raw_config.get("index_workers", normalized["index_workers"])))
    except (TypeError, ValueError):
        pass

    try:
        normalized["phash_threshold"] = max(0, min(64, int(raw_config.get("phash_threshold", normalized["phash_threshold"]))))
    except (TypeError, ValueError):
        pass

    try:
        normalized["rematch_workers"] = max(1, int(raw_config.get("rematch_workers", normalized["rematch_workers"])))
    except (TypeError, ValueError):
        pass

    # YOLO-Einstellungen
    yolo_model = raw_config.get("yolo_model", normalized["yolo_model"])
    if isinstance(yolo_model, str) and yolo_model.strip():
        normalized["yolo_model"] = yolo_model.strip()

    try:
        normalized["yolo_confidence"] = max(0.0, min(1.0, float(raw_config.get("yolo_confidence", normalized["yolo_confidence"]))))
    except (TypeError, ValueError):
        pass

    yolo_device = raw_config.get("yolo_device", normalized["yolo_device"])
    if isinstance(yolo_device, str):
        normalized["yolo_device"] = yolo_device.strip() or "0"

    # Personen-Backend (legacy support für None)
    if raw_config.get("person_backend") is None:
        normalized["person_backend"] = "insightface"

    try:
        normalized["person_threshold"] = max(0.0, min(1.0, float(raw_config.get("person_threshold", normalized["person_threshold"]))))
    except (TypeError, ValueError):
        pass

    try:
        normalized["person_top_k"] = max(1, int(raw_config.get("person_top_k", normalized["person_top_k"])))
    except (TypeError, ValueError):
        pass

    normalized["person_full_image_fallback"] = bool(raw_config.get("person_full_image_fallback", normalized["person_full_image_fallback"]))

    # InsightFace-Einstellungen
    insightface_model = raw_config.get("insightface_model", normalized["insightface_model"])
    if isinstance(insightface_model, str):
        normalized["insightface_model"] = insightface_model.strip() or "buffalo_l"

    try:
        normalized["insightface_ctx"] = int(raw_config.get("insightface_ctx", normalized["insightface_ctx"]))
    except (TypeError, ValueError):
        pass

    det_size = raw_config.get("insightface_det_size", normalized["insightface_det_size"])
    if isinstance(det_size, str):
        normalized["insightface_det_size"] = det_size.strip() or "640,640"

    # Timelapse-Einstellungen
    timelapse_backend = raw_config.get("timelapse_ai_backend", normalized["timelapse_ai_backend"])
    if isinstance(timelapse_backend, str):
        normalized["timelapse_ai_backend"] = timelapse_backend.strip() or "auto"

    sr_model = raw_config.get("timelapse_superres_model", normalized["timelapse_superres_model"])
    if isinstance(sr_model, str):
        normalized["timelapse_superres_model"] = sr_model.strip()

    sr_name = raw_config.get("timelapse_superres_name", normalized["timelapse_superres_name"])
    if isinstance(sr_name, str):
        normalized["timelapse_superres_name"] = sr_name.strip() or "espcn"

    try:
        normalized["timelapse_superres_scale"] = max(1, int(raw_config.get("timelapse_superres_scale", normalized["timelapse_superres_scale"])))
    except (TypeError, ValueError):
        pass

    face_onnx = raw_config.get("timelapse_face_onnx_model", normalized["timelapse_face_onnx_model"])
    if isinstance(face_onnx, str):
        normalized["timelapse_face_onnx_model"] = face_onnx.strip()

    onnx_provider = raw_config.get("timelapse_face_onnx_provider", normalized["timelapse_face_onnx_provider"])
    if isinstance(onnx_provider, str):
        normalized["timelapse_face_onnx_provider"] = onnx_provider.strip() or "auto"

    try:
        normalized["timelapse_face_onnx_size"] = max(32, int(raw_config.get("timelapse_face_onnx_size", normalized["timelapse_face_onnx_size"])))
    except (TypeError, ValueError):
        pass

    return normalized


def get_admin_config(db_path: Path) -> dict[str, object]:
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key, value_json FROM admin_config").fetchall()

    raw_config: dict[str, object] = {}
    for key, value_json in rows:
        try:
            raw_config[key] = json.loads(value_json)
        except json.JSONDecodeError:
            continue

    return _normalize_admin_config(raw_config)


def save_admin_config(db_path: Path, config_values: dict[str, object]) -> dict[str, object]:
    ensure_schema(db_path)
    current = get_admin_config(db_path)
    merged = dict(current)
    for key in ADMIN_CONFIG_DEFAULTS:
        if key in config_values:
            merged[key] = config_values[key]

    normalized = _normalize_admin_config(merged)

    with sqlite3.connect(db_path) as conn:
        for key, value in normalized.items():
            conn.execute(
                """
                INSERT INTO admin_config (key, value_json)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (key, json.dumps(value)),
            )

    return normalized


def sha1_of_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def phash_of_file(path: Path, hash_size: int = 8) -> str | None:
    try:
        with Image.open(path) as image:
            grayscale = image.convert("L").resize((hash_size, hash_size))
            pixels = list(grayscale.tobytes())
    except Exception:
        return None

    if not pixels:
        return None

    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if px >= avg else "0" for px in pixels)
    width = hash_size * hash_size // 4
    return format(int(bits, 2), f"0{width}x")


def _hamming_distance_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def resolve_duplicate_marker(
    db_path: Path,
    photo_path: Path,
    sha1: str,
    phash: str | None,
    near_duplicates: bool = False,
    phash_threshold: int = 6,
) -> tuple[str | None, str | None, float | None]:
    with sqlite3.connect(db_path) as conn:
        exact_row = conn.execute(
            """
            SELECT path
            FROM photos
            WHERE sha1 = ? AND path <> ?
            ORDER BY modified_ts ASC
            LIMIT 1
            """,
            (sha1, str(photo_path)),
        ).fetchone()
        if exact_row:
            return exact_row[0], "exact", 1.0

        if not near_duplicates or phash is None:
            return None, None, None

        rows = conn.execute(
            """
            SELECT path, phash
            FROM photos
            WHERE path <> ? AND phash IS NOT NULL
            """,
            (str(photo_path),),
        ).fetchall()

    best_path: str | None = None
    best_distance: int | None = None
    for path_value, other_phash in rows:
        if not other_phash:
            continue
        try:
            distance = _hamming_distance_hex(phash, other_phash)
        except Exception:
            continue

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_path = path_value

    if best_path is not None and best_distance is not None and best_distance <= phash_threshold:
        score = 1.0 - (best_distance / 64.0)
        return best_path, "near", round(score, 4)

    return None, None, None


def upsert_photo(
    db_path: Path,
    record: ImageRecord,
    labels: list[str],
    sha1: str | None = None,
    phash: str | None = None,
    duplicate_of_path: str | None = None,
    duplicate_kind: str | None = None,
    duplicate_score: float | None = None,
    person_count: int = 0,
) -> None:
    if sha1 is None:
        sha1 = sha1_of_file(record.path)
    if phash is None:
        phash = phash_of_file(record.path)
    search_blob = " ".join(
        [record.path.as_posix().lower(), record.path.stem.lower(), " ".join(labels)]
    )

    # EXIF-Daten als JSON speichern
    exif_json = None
    if record.exif_data:
        exif_dict = {
            k: v for k, v in record.exif_data.__dict__.items()
            if v is not None
        }
        if exif_dict:
            exif_json = json.dumps(_make_json_serializable(exif_dict))

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO photos (
                path,
                size_bytes,
                modified_ts,
                taken_ts,
                sha1,
                phash,
                duplicate_of_path,
                duplicate_kind,
                duplicate_score,
                labels_json,
                search_blob,
                person_count,
                exif_json,
                exif_checked
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size_bytes=excluded.size_bytes,
                modified_ts=excluded.modified_ts,
                taken_ts=excluded.taken_ts,
                sha1=excluded.sha1,
                phash=excluded.phash,
                duplicate_of_path=excluded.duplicate_of_path,
                duplicate_kind=excluded.duplicate_kind,
                duplicate_score=excluded.duplicate_score,
                labels_json=excluded.labels_json,
                search_blob=excluded.search_blob,
                person_count=excluded.person_count,
                exif_json=excluded.exif_json,
                exif_checked=excluded.exif_checked
            """,
            (
                str(record.path),
                record.size_bytes,
                record.modified_ts,
                record.taken_ts,
                sha1,
                phash,
                duplicate_of_path,
                duplicate_kind,
                duplicate_score,
                json.dumps(labels),
                search_blob,
                person_count,
                exif_json,
                1,
            ),
        )


def get_photo_metadata_map(
    db_path: Path,
    paths: list[Path],
) -> dict[str, tuple[int, float, bool]]:
    if not paths or not db_path.exists():
        return {}

    path_strings = [str(path) for path in paths]
    metadata_by_path: dict[str, tuple[int, float, bool]] = {}
    chunk_size = 900

    with sqlite3.connect(db_path) as conn:
        for start in range(0, len(path_strings), chunk_size):
            chunk = path_strings[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                SELECT path, size_bytes, modified_ts, exif_checked
                FROM photos
                WHERE path IN ({placeholders})
            """
            rows = conn.execute(sql, chunk).fetchall()
            for row in rows:
                metadata_by_path[row[0]] = (int(row[1]), float(row[2]), bool(row[3]))

    return metadata_by_path


def _safe_split_query(query: str) -> list[str]:
    try:
        return shlex.split(query)
    except ValueError:
        return query.split()


class SearchFilterDict(TypedDict):
    month: int | None
    year: int | None
    persons: list[str]
    smile_min: float | None


def parse_search_filters(query: str) -> tuple[list[str], SearchFilterDict]:
    """
    Extrahiert bekannte Filter aus der Query.

    Unterstuetzte Filter:
    - month:MM
    - year:YYYY
    - person:<name> (auch person:"Vorname Nachname") – mehrfach fuer UND-Verknuepfung
    - smile:<threshold> (0..1 oder 0..100)
    """
    tokens = [term.strip() for term in _safe_split_query(query) if term.strip()]

    month_filter: int | None = None
    year_filter: int | None = None
    persons_filter: list[str] = []
    smile_min: float | None = None
    filtered_terms: list[str] = []

    for token in tokens:
        term = token.lower()

        if term.startswith("month:"):
            try:
                month_val = int(term[6:])
                if 1 <= month_val <= 12:
                    month_filter = month_val
                continue
            except ValueError:
                pass

        if term.startswith("year:"):
            try:
                year_val = int(term[5:])
                if 1900 <= year_val <= 2100:
                    year_filter = year_val
                continue
            except ValueError:
                pass

        if term.startswith("person:"):
            person_value = token[7:].strip()
            if person_value and person_value.lower() not in [p.lower() for p in persons_filter]:
                persons_filter.append(person_value)
            continue

        if term.startswith("smile:"):
            raw = term[6:].strip()
            if raw.endswith("%"):
                raw = raw[:-1]
            try:
                smile_val = float(raw)
                if smile_val > 1.0:
                    smile_val = smile_val / 100.0
                smile_min = max(0.0, min(1.0, smile_val))
                continue
            except ValueError:
                pass

        filtered_terms.append(term)

    return filtered_terms, {
        "month": month_filter,
        "year": year_filter,
        "persons": persons_filter,
        "smile_min": smile_min,
    }


def _parse_date_filters(query: str) -> tuple[list[str], dict[str, object]]:
    terms, filters = parse_search_filters(query)
    return terms, {
        "month": filters["month"],
        "year": filters["year"],
    }


def search_photos_page(
    db_path: Path,
    query: str,
    limit: int = 20,
    offset: int = 0,
    person_count: int | None = None,
    album_id: int | None = None,
) -> tuple[list[IndexedPhoto], int]:
    import datetime

    terms, filters = parse_search_filters(query)
    month_filter = filters["month"]
    year_filter = filters["year"]
    persons_filter = filters["persons"]
    smile_min = filters["smile_min"]

    if (
        not terms
        and album_id is None
        and person_count is None
        and not (month_filter or year_filter)
        and not persons_filter
        and smile_min is None
    ):
        return [], 0

    where_parts = ["search_blob LIKE ?" for _ in terms]

    # Datumfilter hinzufügen
    start_date = None
    end_date = None
    month_str = None

    if month_filter is not None or year_filter is not None:
        if month_filter is not None and year_filter is not None:
            # Spezifischer Monat und Jahr
            start_date = datetime.datetime(year_filter, month_filter, 1)
            if month_filter == 12:
                end_date = datetime.datetime(year_filter + 1, 1, 1)
            else:
                end_date = datetime.datetime(year_filter, month_filter + 1, 1)
            where_parts.append("taken_ts >= ? AND taken_ts < ?")
        elif year_filter is not None:
            # Nur Jahr
            start_date = datetime.datetime(year_filter, 1, 1)
            end_date = datetime.datetime(year_filter + 1, 1, 1)
            where_parts.append("taken_ts >= ? AND taken_ts < ?")
        else:
            # Nur Monat - suche über alle Jahre
            month_str = f"{month_filter:02d}"
            where_parts.append(
                "(strftime('%m', datetime(taken_ts, 'unixepoch')) = ? OR "
                "(taken_ts IS NULL AND strftime('%m', datetime(modified_ts, 'unixepoch')) = ?))"
            )

    # Pro Person ein eigenes EXISTS (UND-Verknuepfung)
    for _person_name in persons_filter:
        where_parts.append(
            """
            EXISTS (
                SELECT 1
                FROM photo_person_matches m
                JOIN persons p ON p.id = m.person_id
                WHERE m.photo_path = photos.path
                  AND lower(p.name) = lower(?)
            )
            """
        )

    # smile_min als separater globaler Filter (mindestens eine Person muss laecheln)
    if smile_min is not None:
        where_parts.append(
            """
            EXISTS (
                SELECT 1
                FROM photo_person_matches m
                WHERE m.photo_path = photos.path
                  AND m.smile_score IS NOT NULL
                  AND m.smile_score >= ?
            )
            """
        )

    if person_count is not None:
        where_parts.append("person_count = ?")
    if album_id is not None:
        where_parts.append(
            "EXISTS (SELECT 1 FROM album_photos ap WHERE ap.photo_path = photos.path AND ap.album_id = ?)"
        )

    where_sql = " AND ".join(where_parts) if where_parts else "1=1"

    sql = f"""
        SELECT path, labels_json, size_bytes, modified_ts
               , duplicate_of_path, duplicate_kind, duplicate_score, person_count
        FROM photos
        WHERE {where_sql}
        ORDER BY COALESCE(taken_ts, modified_ts) DESC
        LIMIT ?
        OFFSET ?
    """
    count_sql = f"""
        SELECT COUNT(*)
        FROM photos
        WHERE {where_sql}
    """

    base_params: list[object] = [f"%{term}%" for term in terms]

    # Datumfilter-Parameter hinzufügen
    if start_date is not None and end_date is not None:
        base_params.append(start_date.timestamp())
        base_params.append(end_date.timestamp())
    elif month_str is not None:
        base_params.append(month_str)
        base_params.append(month_str)

    # Person-Parameter: ein Wert pro Person-EXISTS
    for person_name in persons_filter:
        base_params.append(person_name)

    if smile_min is not None:
        base_params.append(smile_min)

    if person_count is not None:
        base_params.append(person_count)
    if album_id is not None:
        base_params.append(album_id)
    params: list[object] = [*base_params, limit, offset]

    with sqlite3.connect(db_path) as conn:
        total_hits = conn.execute(count_sql, base_params).fetchone()[0]
        rows = conn.execute(sql, params).fetchall()

    items = [
        IndexedPhoto(
            path=row[0],
            labels=json.loads(row[1]),
            size_bytes=row[2],
            modified_ts=row[3],
            duplicate_of_path=row[4],
            duplicate_kind=row[5],
            duplicate_score=row[6],
            person_count=int(row[7]) if row[7] is not None else 0,
        )
        for row in rows
    ]
    return items, int(total_hits)


def search_photos(db_path: Path, query: str, limit: int = 20) -> list[IndexedPhoto]:
    items, _ = search_photos_page(db_path=db_path, query=query, limit=limit, offset=0)
    return items


def update_exif_only(db_path: Path, photo_paths: list[Path] | None = None) -> int:
    """
    Aktualisiert nur die EXIF-Daten für existierende Fotos in der Datenbank.
    Das ist viel schneller als eine komplette Neuerstellung.

    Args:
        db_path: Pfad zur Datenbank
        photo_paths: Liste der Fotos zum Aktualisieren. Wenn None, werden alle Fotos aktualisiert.

    Returns:
        Anzahl der aktualisierten Fotos
    """
    if not db_path.exists():
        return 0

    from ..ingest import _extract_exif_data
    with sqlite3.connect(db_path) as conn:
        # Hole alle Fotos aus der Datenbank
        if photo_paths:
            path_strings = [str(p) for p in photo_paths]
            placeholders = ",".join("?" for _ in path_strings)
            rows = conn.execute(
                f"SELECT path FROM photos WHERE path IN ({placeholders})",
                path_strings
            ).fetchall()
        else:
            rows = conn.execute("SELECT path FROM photos").fetchall()

    updated_count = 0
    for (photo_path_str,) in rows:
        photo_path = Path(photo_path_str)

        # Überspringe wenn Datei nicht existiert
        if not photo_path.exists():
            continue

        # Extrahiere EXIF-Daten
        exif_data = _extract_exif_data(photo_path)

        exif_dict = {
            k: v for k, v in exif_data.__dict__.items()
            if v is not None
        }
        exif_dict = _make_json_serializable(exif_dict)
        exif_json = json.dumps(exif_dict) if exif_dict else None
        taken_ts = exif_data.taken_ts if exif_dict else None

        # Aktualisiere in Datenbank
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE photos
                SET taken_ts = ?, exif_json = ?, exif_checked = 1
                WHERE path = ?
                """,
                (taken_ts, exif_json, photo_path_str)
            )
            updated_count += 1

    return updated_count


def search_photos_by_location(
    db_path: Path,
    latitude: float,
    longitude: float,
    radius_km: float = 1.0,
    limit: int = 100,
) -> tuple[list[IndexedPhoto], int]:
    """
    Sucht Fotos in einem Radius um einen Ort.
    
    Args:
        db_path: Pfad zur Datenbank
        latitude: Breitengrad (z.B. 50.1109)
        longitude: Längengrad (z.B. 14.4094)
        radius_km: Suchradius in Kilometern (default: 1km)
        limit: Maximale Anzahl Treffer
    
    Returns:
        Tupel aus (gefundene Fotos, Gesamtanzahl)
    """
    if not db_path.exists():
        return [], 0
    
    # Erde-Radius in km
    earth_radius_km = 6371.0
    # Berechne die Bounding Box in Grad
    from math import cos, radians

    lat_delta = (radius_km / earth_radius_km) * (180 / 3.14159)
    lon_divisor = max(0.01, cos(radians(latitude)))
    lon_delta = lat_delta / lon_divisor

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT path, labels_json, size_bytes, modified_ts,
                   duplicate_of_path, duplicate_kind, duplicate_score, person_count,
                   exif_json
            FROM photos
            WHERE json_extract(exif_json, '$.latitude') IS NOT NULL
              AND json_extract(exif_json, '$.longitude') IS NOT NULL
            LIMIT ?
            """,
            (limit * 2,)  # Hole mehr um zu filtern
        ).fetchall()
    
    import json as json_module
    
    items = []
    for row in rows:
        try:
            exif_json = row[8]
            if not exif_json:
                continue
            
            exif_data = json_module.loads(exif_json)
            photo_lat = exif_data.get("latitude")
            photo_lon = exif_data.get("longitude")
            
            if photo_lat is None or photo_lon is None:
                continue
            
            # Berechne Distanz (Haversine-Formel)
            from math import radians, sin, cos, sqrt, atan2
            
            lat1, lon1 = radians(latitude), radians(longitude)
            lat2, lon2 = radians(photo_lat), radians(photo_lon)
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            distance = earth_radius_km * c
            
            if distance <= radius_km:
                items.append(
                    IndexedPhoto(
                        path=row[0],
                        labels=json_module.loads(row[1]),
                        size_bytes=row[2],
                        modified_ts=row[3],
                        duplicate_of_path=row[4],
                        duplicate_kind=row[5],
                        duplicate_score=row[6],
                        person_count=int(row[7]) if row[7] is not None else 0,
                    )
                )
                if len(items) >= limit:
                    break
        except (json_module.JSONDecodeError, KeyError, TypeError):
            continue
    
    return items, len(items)

