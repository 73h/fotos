from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AlbumSummary:
    id: int
    name: str
    photo_count: int


def create_album(db_path: Path, name: str) -> AlbumSummary:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Albumname darf nicht leer sein.")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO albums (name, created_ts)
            VALUES (?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (normalized_name, time.time()),
        )
        row = conn.execute(
            """
            SELECT a.id, a.name, COUNT(ap.photo_path)
            FROM albums a
            LEFT JOIN album_photos ap ON ap.album_id = a.id
            WHERE lower(a.name) = lower(?)
            GROUP BY a.id, a.name
            """,
            (normalized_name,),
        ).fetchone()

    if row is None:
        raise RuntimeError("Album konnte nicht gespeichert werden.")
    return AlbumSummary(id=int(row[0]), name=str(row[1]), photo_count=int(row[2]))


def get_album(db_path: Path, album_id: int) -> AlbumSummary | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT a.id, a.name, COUNT(ap.photo_path)
            FROM albums a
            LEFT JOIN album_photos ap ON ap.album_id = a.id
            WHERE a.id = ?
            GROUP BY a.id, a.name
            """,
            (album_id,),
        ).fetchone()
    if row is None:
        return None
    return AlbumSummary(id=int(row[0]), name=str(row[1]), photo_count=int(row[2]))


def list_albums(db_path: Path) -> list[AlbumSummary]:
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.name, COUNT(ap.photo_path)
            FROM albums a
            LEFT JOIN album_photos ap ON ap.album_id = a.id
            GROUP BY a.id, a.name
            ORDER BY lower(a.name) ASC, a.id ASC
            """
        ).fetchall()

    return [AlbumSummary(id=int(row[0]), name=str(row[1]), photo_count=int(row[2])) for row in rows]


def add_photo_to_album(db_path: Path, album_id: int, photo_path: str) -> int:
    normalized_path = photo_path.strip()
    if not normalized_path:
        raise ValueError("Fotopfad darf nicht leer sein.")

    with sqlite3.connect(db_path) as conn:
        album_row = conn.execute("SELECT id FROM albums WHERE id = ?", (album_id,)).fetchone()
        if album_row is None:
            raise ValueError("Album nicht gefunden.")

        photo_row = conn.execute("SELECT path FROM photos WHERE path = ?", (normalized_path,)).fetchone()
        if photo_row is None:
            raise ValueError("Foto ist nicht im Index vorhanden.")

        conn.execute(
            """
            INSERT INTO album_photos (album_id, photo_path, added_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(album_id, photo_path) DO NOTHING
            """,
            (album_id, normalized_path, time.time()),
        )
        # Zähle nur die Anzahl der hinzugefügten Zeilen
        added = conn.total_changes
    return int(added)


def rename_album(db_path: Path, album_id: int, new_name: str) -> AlbumSummary:
    """Benennt ein Album um."""
    normalized_name = new_name.strip()
    if not normalized_name:
        raise ValueError("Albumname darf nicht leer sein.")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE albums
            SET name = ?, updated_ts = ?
            WHERE id = ?
            """,
            (normalized_name, time.time(), album_id),
        )
        row = conn.execute(
            """
            SELECT a.id, a.name, COUNT(ap.photo_path)
            FROM albums a
            LEFT JOIN album_photos ap ON ap.album_id = a.id
            WHERE a.id = ?
            GROUP BY a.id, a.name
            """,
            (album_id,),
        ).fetchone()

    if row is None:
        raise ValueError("Album nicht gefunden.")
    return AlbumSummary(id=int(row[0]), name=str(row[1]), photo_count=int(row[2]))


def delete_album(db_path: Path, album_id: int) -> bool:
    """Löscht ein Album (nur Zuordnungen, nicht die Bilder selbst)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM album_photos WHERE album_id = ?", (album_id,))
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        return conn.total_changes > 0


def set_album_cover(db_path: Path, album_id: int, photo_path: str | None) -> bool:
    """Setzt oder löscht das Coverbild eines Albums."""
    with sqlite3.connect(db_path) as conn:
        if photo_path is not None:
            album_row = conn.execute("SELECT id FROM albums WHERE id = ?", (album_id,)).fetchone()
            if album_row is None:
                raise ValueError("Album nicht gefunden.")
            photo_row = conn.execute("SELECT path FROM photos WHERE path = ?", (photo_path,)).fetchone()
            if photo_row is None:
                raise ValueError("Foto ist nicht im Index vorhanden.")

        conn.execute(
            "UPDATE albums SET cover_photo_path = ?, updated_ts = ? WHERE id = ?",
            (photo_path, time.time(), album_id),
        )
        return conn.total_changes > 0


def add_photos_to_album_batch(db_path: Path, album_id: int, photo_paths: list[str]) -> int:
    """Fügt mehrere Fotos auf einmal zu einem Album hinzu."""
    if not photo_paths:
        return 0

    with sqlite3.connect(db_path) as conn:
        album_row = conn.execute("SELECT id FROM albums WHERE id = ?", (album_id,)).fetchone()
        if album_row is None:
            raise ValueError("Album nicht gefunden.")

        now = time.time()
        added_count = 0
        for photo_path in photo_paths:
            normalized_path = str(photo_path).strip()
            if not normalized_path:
                continue
            photo_row = conn.execute("SELECT path FROM photos WHERE path = ?", (normalized_path,)).fetchone()
            if photo_row is None:
                continue
            conn.execute(
                """
                INSERT INTO album_photos (album_id, photo_path, added_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(album_id, photo_path) DO NOTHING
                """,
                (album_id, normalized_path, now),
            )
            # Zähle nur die Änderungen von dieser INSERT-Operation
            added_count += conn.total_changes

    return int(added_count)


def remove_photo_from_album(db_path: Path, album_id: int, photo_path: str) -> bool:
    """Entfernt ein Foto aus einem Album."""
    normalized_path = photo_path.strip()
    if not normalized_path:
        raise ValueError("Fotopfad darf nicht leer sein.")

    with sqlite3.connect(db_path) as conn:
        album_row = conn.execute("SELECT id FROM albums WHERE id = ?", (album_id,)).fetchone()
        if album_row is None:
            raise ValueError("Album nicht gefunden.")

        conn.execute(
            "DELETE FROM album_photos WHERE album_id = ? AND photo_path = ?",
            (album_id, normalized_path),
        )
        return conn.total_changes > 0
