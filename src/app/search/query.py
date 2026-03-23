from pathlib import Path

from app.index.store import IndexedPhoto, search_photos


def run_search(db_path: Path, query: str, limit: int = 20) -> list[IndexedPhoto]:
    return search_photos(db_path=db_path, query=query, limit=limit)

