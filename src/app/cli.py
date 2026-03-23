import argparse
from pathlib import Path

from tqdm import tqdm

from .config import AppConfig
from .doctor import run_doctor
from .detectors.labels import infer_labels_from_path
from .index.store import ensure_schema, upsert_photo
from .ingest import scan_images
from .persons.service import (
    enroll_person,
    match_persons_for_photo,
    persist_matches_for_photo,
    search_person_photos,
)
from .search.query import run_search


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokale Foto-Suche (MVP)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Diagnose aller Komponenten")

    index_parser = subparsers.add_parser("index", help="Fotos indexieren")
    index_parser.add_argument("--root", required=True, help="Wurzelordner mit Fotos")
    index_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    index_parser.add_argument(
        "--person-backend",
        default=None,
        choices=["auto", "insightface", "histogram"],
        help="Embedding-Backend fuer Personenmatching",
    )

    enroll_parser = subparsers.add_parser("enroll", help="Referenzbilder fuer eine Person einlernen")
    enroll_parser.add_argument("--name", required=True, help="Personenname")
    enroll_parser.add_argument("--root", required=True, help="Ordner mit Referenzbildern")
    enroll_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    enroll_parser.add_argument(
        "--person-backend",
        default=None,
        choices=["auto", "insightface", "histogram"],
        help="Embedding-Backend fuer Enrollment",
    )

    search_parser = subparsers.add_parser("search", help="Fotos durchsuchen")
    search_parser.add_argument("--query", required=True, help="Suchtext")
    search_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    search_parser.add_argument("--limit", type=int, default=20, help="Maximale Treffer")

    person_search_parser = subparsers.add_parser(
        "search-person", help="Treffer zu einer bekannten Person anzeigen"
    )
    person_search_parser.add_argument("--name", required=True, help="Personenname")
    person_search_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    person_search_parser.add_argument("--limit", type=int, default=20, help="Maximale Treffer")

    return parser


def _index_command(
    config: AppConfig,
    root: Path,
    custom_db_path: str | None,
    person_backend: str | None,
) -> int:
    db_path = config.resolve_db_path(custom_db_path)
    ensure_schema(db_path)

    images = scan_images(root=root, supported_extensions=config.supported_extensions)
    if not images:
        print(f"Keine Bilder gefunden unter: {root}")
        return 0

    for record in tqdm(images, desc="Indexiere Fotos", unit="Foto"):
        labels = set(infer_labels_from_path(record.path))
        person_matches = match_persons_for_photo(
            db_path=db_path,
            photo_path=record.path,
            preferred_backend=person_backend,
        )
        if person_matches:
            labels.add("person")
            for person_match in person_matches:
                labels.add(f"person:{person_match.person_name.lower()}")

        upsert_photo(db_path=db_path, record=record, labels=sorted(labels))
        persist_matches_for_photo(db_path=db_path, photo_path=record.path, matches=person_matches)

    print(f"Index abgeschlossen: {len(images)} Dateien in {db_path}")
    return 0


def _enroll_command(
    config: AppConfig,
    person_name: str,
    root: Path,
    custom_db_path: str | None,
    person_backend: str | None,
) -> int:
    db_path = config.resolve_db_path(custom_db_path)
    ensure_schema(db_path)

    try:
        result = enroll_person(
            db_path=db_path,
            person_name=person_name,
            root=root,
            supported_extensions=config.supported_extensions,
            preferred_backend=person_backend,
        )
    except ValueError as error:
        print(str(error))
        return 1

    print(
        f"Person '{result.person_name}' eingelernt: "
        f"{result.sample_count} Samples aus {result.image_count} Bildern "
        f"(backend: {result.backend})"
    )
    return 0


def _search_command(config: AppConfig, query: str, custom_db_path: str | None, limit: int) -> int:
    db_path = config.resolve_db_path(custom_db_path)
    if not db_path.exists():
        print(f"Index nicht gefunden: {db_path}")
        print("Bitte zuerst: python src/main.py index --root <dein_foto_ordner>")
        return 1

    results = run_search(db_path=db_path, query=query, limit=limit)
    if not results:
        print("Keine Treffer.")
        return 0

    print(f"Treffer fuer '{query}':")
    for index, item in enumerate(results, start=1):
        labels = ", ".join(item.labels) if item.labels else "-"
        print(f"{index:>2}. {item.path} | labels: {labels}")

    return 0


def _search_person_command(
    config: AppConfig,
    person_name: str,
    custom_db_path: str | None,
    limit: int,
) -> int:
    db_path = config.resolve_db_path(custom_db_path)
    if not db_path.exists():
        print(f"Index nicht gefunden: {db_path}")
        print("Bitte zuerst: python src/main.py index --root <dein_foto_ordner>")
        return 1

    hits = search_person_photos(db_path=db_path, person_name=person_name, limit=limit)
    if not hits:
        print(f"Keine Treffer fuer Person '{person_name}'.")
        return 0

    print(f"Treffer fuer Person '{person_name}':")
    for index, hit in enumerate(hits, start=1):
        print(f"{index:>2}. {hit.path} | score: {hit.score:.3f}")

    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parents[2]
    config = AppConfig.from_workspace(workspace_root=workspace_root)

    if args.command == "index":
        return _index_command(
            config=config,
            root=Path(args.root),
            custom_db_path=args.db,
            person_backend=args.person_backend,
        )
    if args.command == "enroll":
        return _enroll_command(
            config=config,
            person_name=args.name,
            root=Path(args.root),
            custom_db_path=args.db,
            person_backend=args.person_backend,
        )
    if args.command == "search":
        return _search_command(
            config=config,
            query=args.query,
            custom_db_path=args.db,
            limit=args.limit,
        )
    if args.command == "search-person":
        return _search_person_command(
            config=config,
            person_name=args.name,
            custom_db_path=args.db,
            limit=args.limit,
        )
    if args.command == "doctor":
        return run_doctor()

    parser.print_help()
    return 1

