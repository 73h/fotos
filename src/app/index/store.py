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


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                modified_ts REAL NOT NULL,
                sha1 TEXT NOT NULL,
                phash TEXT,
                duplicate_of_path TEXT,
                duplicate_kind TEXT,
                duplicate_score REAL,
                labels_json TEXT NOT NULL,
                search_blob TEXT NOT NULL
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
) -> None:
    if sha1 is None:
        sha1 = sha1_of_file(record.path)
    if phash is None:
        phash = phash_of_file(record.path)
    search_blob = " ".join(
        [record.path.as_posix().lower(), record.path.stem.lower(), " ".join(labels)]
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO photos (
                path,
                size_bytes,
                modified_ts,
                sha1,
                phash,
                duplicate_of_path,
                duplicate_kind,
                duplicate_score,
                labels_json,
                search_blob
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size_bytes=excluded.size_bytes,
                modified_ts=excluded.modified_ts,
                sha1=excluded.sha1,
                phash=excluded.phash,
                duplicate_of_path=excluded.duplicate_of_path,
                duplicate_kind=excluded.duplicate_kind,
                duplicate_score=excluded.duplicate_score,
                labels_json=excluded.labels_json,
                search_blob=excluded.search_blob
            """,
            (
                str(record.path),
                record.size_bytes,
                record.modified_ts,
                sha1,
                phash,
                duplicate_of_path,
                duplicate_kind,
                duplicate_score,
                json.dumps(labels),
                search_blob,
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
) -> tuple[list[IndexedPhoto], int]:
    terms = [term.strip().lower() for term in query.split() if term.strip()]
    if not terms:
        return [], 0

    where_parts = ["search_blob LIKE ?" for _ in terms]
    sql = f"""
        SELECT path, labels_json, size_bytes, modified_ts
               , duplicate_of_path, duplicate_kind, duplicate_score
        FROM photos
        WHERE {' AND '.join(where_parts)}
        ORDER BY modified_ts DESC
        LIMIT ?
        OFFSET ?
    """
    count_sql = f"""
        SELECT COUNT(*)
        FROM photos
        WHERE {' AND '.join(where_parts)}
    """

    count_params: list[object] = [f"%{term}%" for term in terms]
    params: list[object] = [*count_params, limit, offset]

    with sqlite3.connect(db_path) as conn:
        total_hits = conn.execute(count_sql, count_params).fetchone()[0]
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
        )
        for row in rows
    ]
    return items, int(total_hits)


def search_photos(db_path: Path, query: str, limit: int = 20) -> list[IndexedPhoto]:
    items, _ = search_photos_page(db_path=db_path, query=query, limit=limit, offset=0)
    return items

