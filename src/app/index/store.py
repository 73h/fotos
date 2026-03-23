import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..ingest import ImageRecord


@dataclass(frozen=True)
class IndexedPhoto:
    path: str
    labels: list[str]
    size_bytes: int
    modified_ts: float


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
                labels_json TEXT NOT NULL,
                search_blob TEXT NOT NULL
            )
            """
        )
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


def _sha1_of_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def upsert_photo(db_path: Path, record: ImageRecord, labels: list[str]) -> None:
    sha1 = _sha1_of_file(record.path)
    search_blob = " ".join(
        [record.path.as_posix().lower(), record.path.stem.lower(), " ".join(labels)]
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO photos (path, size_bytes, modified_ts, sha1, labels_json, search_blob)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size_bytes=excluded.size_bytes,
                modified_ts=excluded.modified_ts,
                sha1=excluded.sha1,
                labels_json=excluded.labels_json,
                search_blob=excluded.search_blob
            """,
            (
                str(record.path),
                record.size_bytes,
                record.modified_ts,
                sha1,
                json.dumps(labels),
                search_blob,
            ),
        )


def search_photos(db_path: Path, query: str, limit: int = 20) -> list[IndexedPhoto]:
    terms = [term.strip().lower() for term in query.split() if term.strip()]
    if not terms:
        return []

    where_parts = ["search_blob LIKE ?" for _ in terms]
    sql = f"""
        SELECT path, labels_json, size_bytes, modified_ts
        FROM photos
        WHERE {' AND '.join(where_parts)}
        ORDER BY modified_ts DESC
        LIMIT ?
    """

    params: list[object] = [f"%{term}%" for term in terms]
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        IndexedPhoto(
            path=row[0],
            labels=json.loads(row[1]),
            size_bytes=row[2],
            modified_ts=row[3],
        )
        for row in rows
    ]

