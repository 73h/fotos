import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PersonReference:
    person_id: int
    person_name: str
    backend: str
    vector_dim: int
    vector: list[float]


@dataclass(frozen=True)
class PersonPhotoHit:
    path: str
    score: float
    modified_ts: float


def upsert_person(db_path: Path, name: str) -> int:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Personenname darf nicht leer sein.")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO persons (name)
            VALUES (?)
            ON CONFLICT(name) DO NOTHING
            """,
            (normalized_name,),
        )
        row = conn.execute(
            "SELECT id FROM persons WHERE name = ?",
            (normalized_name,),
        ).fetchone()

    if row is None:
        raise RuntimeError("Person konnte nicht gespeichert werden.")
    return int(row[0])


def replace_person_references(
    db_path: Path,
    person_id: int,
    source_vectors: list[tuple[str, list[float]]],
    backend: str,
    vector_dim: int,
) -> None:
    now_ts = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM person_refs WHERE person_id = ?", (person_id,))
        conn.executemany(
            """
            INSERT INTO person_refs (person_id, source_path, vector_json, backend, vector_dim, created_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (person_id, source_path, json.dumps(vector), backend, vector_dim, now_ts)
                for source_path, vector in source_vectors
            ],
        )


def list_person_references(
    db_path: Path,
    backend_filter: str | None = None,
) -> list[PersonReference]:
    backend_sql = ""
    params: list[object] = []
    if backend_filter:
        backend_sql = "WHERE lower(r.backend) = lower(?)"
        params.append(backend_filter)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.name, r.backend, r.vector_dim, r.vector_json
            FROM persons p
            JOIN person_refs r ON r.person_id = p.id
            {backend_sql}
            ORDER BY p.name ASC, r.id ASC
            """,
            params,
        ).fetchall()

    refs: list[PersonReference] = []
    for person_id, person_name, backend, vector_dim, vector_json in rows:
        refs.append(
            PersonReference(
                person_id=int(person_id),
                person_name=str(person_name),
                backend=str(backend),
                vector_dim=int(vector_dim),
                vector=[float(value) for value in json.loads(vector_json)],
            )
        )
    return refs


def replace_photo_person_matches(
    db_path: Path,
    photo_path: str,
    matches: list[tuple[int, float]],
) -> None:
    now_ts = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM photo_person_matches WHERE photo_path = ?", (photo_path,))
        conn.executemany(
            """
            INSERT INTO photo_person_matches (photo_path, person_id, score, matched_ts)
            VALUES (?, ?, ?, ?)
            """,
            [(photo_path, person_id, score, now_ts) for person_id, score in matches],
        )


def search_photos_by_person_name(db_path: Path, person_name: str, limit: int) -> list[PersonPhotoHit]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.photo_path, m.score, COALESCE(ph.modified_ts, 0)
            FROM photo_person_matches m
            JOIN persons p ON p.id = m.person_id
            LEFT JOIN photos ph ON ph.path = m.photo_path
            WHERE lower(p.name) = lower(?)
            ORDER BY m.score DESC, COALESCE(ph.modified_ts, 0) DESC
            LIMIT ?
            """,
            (person_name.strip(), limit),
        ).fetchall()

    return [
        PersonPhotoHit(path=str(path), score=float(score), modified_ts=float(modified_ts))
        for path, score, modified_ts in rows
    ]

