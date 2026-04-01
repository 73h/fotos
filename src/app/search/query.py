from pathlib import Path

from ..index.store import IndexedPhoto, search_photos, search_photos_page


def run_search(db_path: Path, query: str, limit: int = 20) -> list[IndexedPhoto]:
    return search_photos(db_path=db_path, query=query, limit=limit)


def run_search_page(
    db_path: Path,
    query: str,
    limit: int = 20,
    offset: int = 0,
    person_count: int | None = None,
    album_id: int | None = None,
) -> tuple[list[IndexedPhoto], int]:
    return search_photos_page(
        db_path=db_path,
        query=query,
        limit=limit,
        offset=offset,
        person_count=person_count,
        album_id=album_id,
    )


