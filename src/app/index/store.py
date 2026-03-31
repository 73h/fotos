import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..ingest import ImageRecord


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
                exif_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_sha1 ON photos(sha1)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(phash)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
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
                matched_ts REAL NOT NULL,
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
        
        # Erstelle Indizes nach allen Migrationen
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_taken_ts ON photos(taken_ts)")


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
            exif_json = json.dumps(exif_dict)

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
                exif_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                exif_json=excluded.exif_json
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
            ),
        )


def get_photo_metadata_map(
    db_path: Path,
    paths: list[Path],
) -> dict[str, tuple[int, float]]:
    if not paths or not db_path.exists():
        return {}

    path_strings = [str(path) for path in paths]
    metadata_by_path: dict[str, tuple[int, float]] = {}
    chunk_size = 900

    with sqlite3.connect(db_path) as conn:
        for start in range(0, len(path_strings), chunk_size):
            chunk = path_strings[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                SELECT path, size_bytes, modified_ts
                FROM photos
                WHERE path IN ({placeholders})
            """
            rows = conn.execute(sql, chunk).fetchall()
            for row in rows:
                metadata_by_path[row[0]] = (int(row[1]), float(row[2]))

    return metadata_by_path


def search_photos_page(
    db_path: Path,
    query: str,
    limit: int = 20,
    offset: int = 0,
    max_persons: int | None = None,
    album_id: int | None = None,
) -> tuple[list[IndexedPhoto], int]:
    terms = [term.strip().lower() for term in query.split() if term.strip()]
    if not terms and album_id is None:
        return [], 0

    where_parts = ["search_blob LIKE ?" for _ in terms]
    if max_persons is not None:
        where_parts.append("person_count <= ?")
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
    if max_persons is not None:
        base_params.append(max_persons)
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
    from fractions import Fraction

    def make_json_serializable(obj):
        """Konvertiert nicht-serialisierbare Objekte zu JSON-kompatiblen Typen."""
        if isinstance(obj, Fraction):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: make_json_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [make_json_serializable(v) for v in obj]
        return obj

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

        if not exif_data or not any(exif_data.__dict__.values()):
            continue

        # Konvertiere zu JSON-serialisierbarem Format
        exif_dict = {
            k: v for k, v in exif_data.__dict__.items()
            if v is not None
        }
        exif_dict = make_json_serializable(exif_dict)
        exif_json = json.dumps(exif_dict) if exif_dict else None

        # Aktualisiere in Datenbank
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE photos
                SET taken_ts = ?, exif_json = ?
                WHERE path = ?
                """,
                (exif_data.taken_ts, exif_json, photo_path_str)
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
    
    # Berechne die Bounding Box in Grad (vereinfacht)
    lat_delta = (radius_km / earth_radius_km) * (180 / 3.14159)
    lon_delta = (radius_km / earth_radius_km) * (180 / 3.14159) / (lat_delta / lat_delta if lat_delta else 1)
    
    min_lat = latitude - lat_delta
    max_lat = latitude + lat_delta
    min_lon = longitude - lon_delta
    max_lon = longitude + lon_delta
    
    with sqlite3.connect(db_path) as conn:
        # Hole Fotos mit gültigen GPS-Daten in der Bounding Box
        rows = conn.execute(
            """
            SELECT path, labels_json, size_bytes, modified_ts,
                   duplicate_of_path, duplicate_kind, duplicate_score, person_count
            FROM photos
            WHERE exif_json IS NOT NULL
            LIMIT ?
            """,
            (limit * 2,)  # Hole mehr um zu filtern
        ).fetchall()
    
    import json as json_module
    
    items = []
    for row in rows:
        try:
            exif_json = row[8] if len(row) > 8 else None
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

