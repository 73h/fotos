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


@dataclass(frozen=True)
class PersonSummary:
    id: int
    name: str
    photo_count: int


def upsert_person(db_path: Path, name: str) -> int:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Personenname darf nicht leer sein.")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO persons (name, version)
            VALUES (?, 1)
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
        # Inkrementiere die Person-Version
        conn.execute(
            "UPDATE persons SET version = version + 1 WHERE id = ?",
            (person_id,)
        )
        # Lösche alte References
        conn.execute("DELETE FROM person_refs WHERE person_id = ?", (person_id,))
        # Füge neue References ein
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
        # Lösche alte Match-Einträge für diese Person, um erzwungenes Rematch zu triggern
        conn.execute(
            "DELETE FROM photo_person_matches WHERE person_id = ?",
            (person_id,)
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
    matches: list[tuple[int, float, float | None]],
) -> None:
    now_ts = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM photo_person_matches WHERE photo_path = ?",
            (photo_path,),
        )

        # Hole die aktuelle Version der Person(en) um die Versionen zu speichern
        for person_id, score, smile_score in matches:
            version_row = conn.execute(
                "SELECT version FROM persons WHERE id = ?",
                (person_id,)
            ).fetchone()
            person_version = version_row[0] if version_row else 1

            conn.execute(
                """
                INSERT OR REPLACE INTO photo_person_matches 
                (photo_path, person_id, score, smile_score, matched_ts, person_version)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (photo_path, person_id, score, smile_score, now_ts, person_version),
            )


def search_photos_by_person_name(
    db_path: Path,
    person_name: str,
    limit: int,
    max_persons: int | None = None,
) -> list[PersonPhotoHit]:
    extra_where = ""
    params: list[object] = [person_name.strip()]
    if max_persons is not None:
        extra_where = "AND (ph.person_count IS NULL OR ph.person_count <= ?)"
        params.append(max_persons)
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT m.photo_path, m.score, COALESCE(ph.modified_ts, 0)
            FROM photo_person_matches m
            JOIN persons p ON p.id = m.person_id
            LEFT JOIN photos ph ON ph.path = m.photo_path
            WHERE lower(p.name) = lower(?)
            {extra_where}
            ORDER BY m.score DESC, COALESCE(ph.modified_ts, 0) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [
        PersonPhotoHit(path=str(path), score=float(score), modified_ts=float(modified_ts))
        for path, score, modified_ts in rows
    ]


def list_persons(db_path: Path) -> list[PersonSummary]:
    """Listet alle Personen mit der Anzahl ihrer Fotos auf."""
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, COUNT(m.photo_path)
            FROM persons p
            LEFT JOIN photo_person_matches m ON m.person_id = p.id
            GROUP BY p.id, p.name
            ORDER BY lower(p.name) ASC, p.id ASC
            """
        ).fetchall()

    return [PersonSummary(id=int(row[0]), name=str(row[1]), photo_count=int(row[2])) for row in rows]


def get_person_current_version(db_path: Path, person_id: int) -> int:
    """Gibt die aktuelle Version einer Person zurück."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT version FROM persons WHERE id = ?",
            (person_id,)
        ).fetchone()
    return row[0] if row else 1


def get_photos_needing_rematch(
    db_path: Path,
    photo_paths: list[str],
) -> list[str]:
    """
    Gibt eine Liste von Foto-Pfaden zurück, die noch nicht gemacht wurden oder
    bei denen die Person-Version neuer ist als die gespeicherte Version.
    """
    if not photo_paths:
        return []

    with sqlite3.connect(db_path) as conn:
        # Hole alle Fotos, deren Person-Version veraltet ist oder nicht existiert
        placeholders = ",".join(["?" for _ in photo_paths])
        rows = conn.execute(
            f"""
            SELECT DISTINCT ph.path
            FROM (
                SELECT ? as path
                UNION ALL
                SELECT ? as path
            ) ph
            LEFT JOIN photo_person_matches m ON m.photo_path = ph.path
            WHERE m.photo_path IS NULL
               OR (SELECT version FROM persons WHERE id = m.person_id) > m.person_version
            """,
            photo_paths + photo_paths,
        ).fetchall()

    # Vereinfachte Version - alle Photos zurückgeben, die noch nicht gecacht sind
    # oder deren Person-Version älter ist
    needs_rematch = set()
    with sqlite3.connect(db_path) as conn:
        for photo_path in photo_paths:
            # Prüfe, ob dieses Foto überhaupt im Cache ist
            existing = conn.execute(
                "SELECT person_id, person_version FROM photo_person_matches WHERE photo_path = ?",
                (photo_path,)
            ).fetchall()

            if not existing:
                # Foto hat noch nie ein Rematch gehabt
                needs_rematch.add(photo_path)
            else:
                # Prüfe, ob die Person-Version veraltet ist
                for person_id, cached_version in existing:
                    current_version = get_person_current_version(db_path, person_id)
                    if current_version > cached_version:
                        needs_rematch.add(photo_path)
                        break

    return list(needs_rematch)
