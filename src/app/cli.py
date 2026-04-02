import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from .config import AppConfig
from .doctor import run_doctor
from .detectors.labels import infer_labels_from_path
from .index.store import (
    ensure_schema,
    get_photo_metadata_map,
    phash_of_file,
    resolve_duplicate_marker,
    sha1_of_file,
    upsert_photo,
    update_exif_only,
)
from .ingest import scan_images
from .persons.service import (
    enroll_person,
    match_persons_for_photo,
    persist_matches_for_photo,
    search_person_photos,
)
from .search.query import run_search
from .web import create_app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokale Foto-Suche (MVP)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Diagnose aller Komponenten")

    index_parser = subparsers.add_parser("index", help="Fotos indexieren")
    index_parser.add_argument("--root", required=True, action="append", help="Wurzelordner(s) mit Fotos (kann mehrmals angegeben werden)")
    index_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    index_parser.add_argument("--force-reindex", action="store_true", help="Alle Dateien erneut verarbeiten (Skip-Check deaktivieren)")
    index_parser.add_argument(
        "--index-workers",
        type=int,
        default=max(1, min(8, os.cpu_count() or 4)),
        help="Anzahl paralleler Worker fuer Vorverarbeitung (Default: min(8, CPU))",
    )
    index_parser.add_argument(
        "--near-duplicates",
        action="store_true",
        help="Nahe Duplikate per pHash markieren (langsamer als exakte Duplikate)",
    )
    index_parser.add_argument(
        "--phash-threshold",
        type=int,
        default=6,
        help="Maximale Hamming-Distanz fuer near duplicates (0-64, Default: 6)",
    )
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
    person_search_parser.add_argument(
        "--max-persons",
        type=int,
        default=None,
        metavar="N",
        help="Nur Fotos anzeigen, auf denen maximal N Personen erkannt wurden (z.B. 1 fuer Solo-Bilder)",
    )

    web_parser = subparsers.add_parser("web", help="Weboberflaeche starten")
    web_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    web_parser.add_argument("--cache-dir", default=None, help="Pfad zum Thumbnail-Cache")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host fuer den Webserver")
    web_parser.add_argument("--port", type=int, default=5000, help="Port fuer den Webserver")
    web_parser.add_argument("--debug", action="store_true", help="Flask Debug-Modus")

    exif_parser = subparsers.add_parser("update-exif", help="EXIF-Daten schnell aktualisieren (ohne Neu-Indexierung)")
    exif_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")

    timelapse_parser = subparsers.add_parser(
        "album-timelapse",
        help="Aging-Timelapse-Video fuer eine Person aus einem Album generieren",
    )
    timelapse_parser.add_argument("--album-id", required=True, type=int, help="Album-ID")
    timelapse_parser.add_argument("--person", required=True, help="Name der Person")
    timelapse_parser.add_argument("--output", required=True, help="Ausgabedatei (z.B. aging.mp4)")
    timelapse_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    timelapse_parser.add_argument("--fps", type=int, default=24, help="Frames pro Sekunde (Default: 24)")
    timelapse_parser.add_argument("--hold", type=int, default=24,
                                  help="Frames pro Originalfoto (Default: 24 = 1 s)")
    timelapse_parser.add_argument("--morph", type=int, default=48,
                                  help="Uebergangsframes zwischen Fotos (Default: 48 = 2 s)")
    timelapse_parser.add_argument("--size", type=int, default=512,
                                  help="Ausgabegroesse in Pixeln, quadratisch (Default: 512)")
    timelapse_parser.add_argument("--person-backend", default=None,
                                  choices=["auto", "insightface", "histogram"],
                                  help="Embedding-Backend fuer Gesichtserkennung")
    timelapse_parser.add_argument(
        "--quality",
        default="compat",
        choices=["compat", "balanced", "max"],
        help="Qualitaetsprofil (compat=altes Morphing, balanced=maximale Qualitaet pro Laufzeit, max=beste Qualitaet)",
    )
    timelapse_parser.add_argument(
        "--interpolator",
        default="morph",
        choices=["morph", "flow", "auto"],
        help="Uebergangsverfahren zwischen Fotos",
    )
    timelapse_parser.add_argument(
        "--temporal-smooth",
        type=float,
        default=0.0,
        help="Zeitliches Glattziehen von Frames (0..0.95)",
    )
    timelapse_parser.add_argument(
        "--detail-boost",
        type=float,
        default=0.0,
        help="Face-Detail-Boost fuer Enhancement (0..1)",
    )
    timelapse_parser.add_argument(
        "--enhance-faces",
        action="store_true",
        help="Aktiviert lokales Face-Enhancement vor dem Rendern",
    )
    timelapse_parser.add_argument(
        "--ai-mode",
        default="off",
        choices=["off", "auto", "max"],
        help="Experimenteller KI-Hook fuer max-Profil",
    )
    timelapse_parser.add_argument(
        "--ai-backend",
        default="auto",
        choices=["auto", "local", "onnx", "superres"],
        help="Backend fuer KI-Hook (auto/local/onnx/superres)",
    )
    timelapse_parser.add_argument(
        "--ai-strength",
        type=float,
        default=0.5,
        help="Staerke des KI-Hooks (0..1)",
    )

    rematch_parser = subparsers.add_parser(
        "rematch-persons",
        help="Personen-Matching (inkl. Smile-Score) fuer alle indizierten Fotos neu berechnen",
    )
    rematch_parser.add_argument("--db", default=None, help="Pfad zur SQLite-DB")
    rematch_parser.add_argument(
        "--person-backend",
        default=None,
        choices=["auto", "insightface", "histogram"],
        help="Embedding-Backend fuer Personenmatching",
    )
    rematch_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Anzahl paralleler Worker (Default: 1)",
    )

    return parser


def _index_command(
    config: AppConfig,
    roots: list[Path],
    custom_db_path: str | None,
    person_backend: str | None,
    force_reindex: bool = False,
    index_workers: int = 1,
    near_duplicates: bool = False,
    phash_threshold: int = 6,
) -> int:
    from .persons.embeddings import initialize_insightface_settings
    from .detectors.labels import initialize_yolo_settings

    db_path = config.resolve_db_path(custom_db_path)
    ensure_schema(db_path)

    # Lade alle Einstellungen aus der Datenbank
    initialize_yolo_settings(db_path)
    initialize_insightface_settings(db_path)

    safe_workers = max(1, index_workers)
    safe_threshold = max(0, min(64, phash_threshold))

    def prepare_record(record):
        labels = set(infer_labels_from_path(record.path))
        person_matches, person_count = match_persons_for_photo(
            db_path=db_path,
            photo_path=record.path,
            preferred_backend=person_backend,
        )
        if person_matches:
            labels.add("person")
            for person_match in person_matches:
                labels.add(f"person:{person_match.person_name.lower()}")

        return (
            record,
            sorted(labels),
            person_matches,
            sha1_of_file(record.path),
            phash_of_file(record.path),
            person_count,
        )

    total_images = 0
    total_processed = 0
    total_skipped = 0
    total_duplicates = 0
    for root in roots:
        images = scan_images(root=root, supported_extensions=config.supported_extensions)
        if not images:
            print(f"Keine Bilder gefunden unter: {root}")
            continue

        existing_metadata = (
            {}
            if force_reindex
            else get_photo_metadata_map(
                db_path=db_path,
                paths=[record.path for record in images],
            )
        )

        to_process = []
        for record in images:
            previous_metadata = existing_metadata.get(str(record.path))
            if previous_metadata is not None:
                previous_size, previous_modified_ts, exif_checked = previous_metadata
                if (
                    previous_size == record.size_bytes
                    and previous_modified_ts == record.modified_ts
                    and exif_checked
                ):
                    total_skipped += 1
                    continue
            to_process.append(record)

        processed_for_root = 0
        duplicates_for_root = 0

        if safe_workers == 1:
            iterator = (prepare_record(record) for record in to_process)
            progress = tqdm(iterator, total=len(to_process), desc=f"Indexiere Fotos aus {root.name}", unit="Foto")
            for record, labels, person_matches, sha1, phash, person_count in progress:
                duplicate_of_path, duplicate_kind, duplicate_score = resolve_duplicate_marker(
                    db_path=db_path,
                    photo_path=record.path,
                    sha1=sha1,
                    phash=phash,
                    near_duplicates=near_duplicates,
                    phash_threshold=safe_threshold,
                )
                if duplicate_kind is not None:
                    duplicates_for_root += 1

                upsert_photo(
                    db_path=db_path,
                    record=record,
                    labels=labels,
                    sha1=sha1,
                    phash=phash,
                    duplicate_of_path=duplicate_of_path,
                    duplicate_kind=duplicate_kind,
                    duplicate_score=duplicate_score,
                    person_count=person_count,
                )
                persist_matches_for_photo(db_path=db_path, photo_path=record.path, matches=person_matches)
                processed_for_root += 1
        else:
            with ThreadPoolExecutor(max_workers=safe_workers) as executor:
                futures = [executor.submit(prepare_record, record) for record in to_process]
                progress = tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=f"Indexiere Fotos aus {root.name}",
                    unit="Foto",
                )
                for future in progress:
                    record, labels, person_matches, sha1, phash, person_count = future.result()
                    duplicate_of_path, duplicate_kind, duplicate_score = resolve_duplicate_marker(
                        db_path=db_path,
                        photo_path=record.path,
                        sha1=sha1,
                        phash=phash,
                        near_duplicates=near_duplicates,
                        phash_threshold=safe_threshold,
                    )
                    if duplicate_kind is not None:
                        duplicates_for_root += 1

                    upsert_photo(
                        db_path=db_path,
                        record=record,
                        labels=labels,
                        sha1=sha1,
                        phash=phash,
                        duplicate_of_path=duplicate_of_path,
                        duplicate_kind=duplicate_kind,
                        duplicate_score=duplicate_score,
                        person_count=person_count,
                    )
                    persist_matches_for_photo(db_path=db_path, photo_path=record.path, matches=person_matches)
                    processed_for_root += 1

        total_images += len(images)
        total_processed += processed_for_root
        total_duplicates += duplicates_for_root

    print(
        f"Index abgeschlossen: {total_images} Dateien gescannt, "
        f"{total_processed} verarbeitet, {total_skipped} uebersprungen, "
        f"{total_duplicates} als Duplikat markiert in {db_path}"
    )
    return 0


def _enroll_command(
    config: AppConfig,
    person_name: str,
    root: Path,
    custom_db_path: str | None,
    person_backend: str | None,
) -> int:
    from .persons.embeddings import initialize_insightface_settings

    db_path = config.resolve_db_path(custom_db_path)
    ensure_schema(db_path)

    # Lade InsightFace-Einstellungen aus der Datenbank
    initialize_insightface_settings(db_path)

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
    max_persons: int | None = None,
) -> int:
    db_path = config.resolve_db_path(custom_db_path)
    if not db_path.exists():
        print(f"Index nicht gefunden: {db_path}")
        print("Bitte zuerst: python src/main.py index --root <dein_foto_ordner>")
        return 1

    hits = search_person_photos(db_path=db_path, person_name=person_name, limit=limit, max_persons=max_persons)
    if not hits:
        filter_hint = f" (max. {max_persons} Person(en) im Bild)" if max_persons is not None else ""
        print(f"Keine Treffer fuer Person '{person_name}'{filter_hint}.")
        return 0

    filter_hint = f" (max. {max_persons} Person(en) im Bild)" if max_persons is not None else ""
    print(f"Treffer fuer Person '{person_name}'{filter_hint}:")
    for index, hit in enumerate(hits, start=1):
        print(f"{index:>2}. {hit.path} | score: {hit.score:.3f}")

    return 0


def _web_command(
    config: AppConfig,
    custom_db_path: str | None,
    custom_cache_dir: str | None,
    host: str,
    port: int,
    debug: bool,
) -> int:
    app = create_app(
        app_config=config,
        custom_db_path=custom_db_path,
        custom_cache_dir=custom_cache_dir,
    )
    app.run(host=host, port=port, debug=debug)
    return 0


def _album_timelapse_command(
    config: AppConfig,
    album_id: int,
    person_name: str,
    output_file: str,
    custom_db_path: str | None,
    fps: int,
    hold_frames: int,
    morph_frames: int,
    output_size: int,
    person_backend: str | None,
    quality_profile: str,
    interpolator: str,
    temporal_smooth: float,
    detail_boost: float,
    enhance_faces: bool,
    ai_mode: str,
    ai_backend: str,
    ai_strength: float,
) -> int:
    from .albums.timelapse import TimelapseConfig, generate_aging_timelapse
    from .persons.embeddings import initialize_insightface_settings

    db_path = config.resolve_db_path(custom_db_path)
    if not db_path.exists():
        print(f"Index nicht gefunden: {db_path}")
        return 1

    # Lade InsightFace-Einstellungen aus der Datenbank
    initialize_insightface_settings(db_path)

    if person_backend:
        import os
        os.environ["FOTOS_PERSON_BACKEND"] = person_backend

    output_path = Path(output_file)
    cfg = TimelapseConfig(
        fps=fps,
        hold_frames=hold_frames,
        morph_frames=morph_frames,
        output_size=output_size,
        quality_profile=quality_profile,
        interpolator=interpolator,
        temporal_smooth=temporal_smooth,
        detail_boost=detail_boost,
        enhance_faces=enhance_faces,
        ai_mode=ai_mode,
        ai_backend=ai_backend,
        ai_strength=ai_strength,
    )

    def _cb(step: int, total: int, msg: str) -> None:
        print(f"  [{step}/{total}] {msg}")

    try:
        count = generate_aging_timelapse(
            db_path=db_path,
            album_id=album_id,
            person_name=person_name,
            output_path=output_path,
            config=cfg,
            progress_cb=_cb,
        )
    except ImportError as exc:
        print(f"Fehler: {exc}")
        return 1
    except ValueError as exc:
        print(f"Fehler: {exc}")
        return 1

    duration_s = (count * hold_frames + (count - 1) * morph_frames) / fps
    print(
        f"\n✓ Timelapse erstellt: {output_path.resolve()}\n"
        f"  Fotos: {count}  |  Dauer: {duration_s:.1f} s  |  "
        f"{fps} fps, {output_size}×{output_size} px"
    )
    return 0


def _rematch_persons_command(
    config: AppConfig,
    custom_db_path: str | None,
    person_backend: str | None,
    workers: int = 1,
) -> int:
    """
    Berechnet Personen-Matching und Smile-Score fuer alle bereits indizierten Fotos neu.
    Viel schneller als ein vollstaendiger Re-Index, da SHA1/pHash/Labels/EXIF uebersprungen werden.
    """
    import sqlite3
    from .persons.embeddings import initialize_insightface_settings
    from .persons.store import get_photos_needing_rematch

    db_path = config.resolve_db_path(custom_db_path)
    if not db_path.exists():
        print(f"Index nicht gefunden: {db_path}")
        return 1

    ensure_schema(db_path)

    # Lade InsightFace-Einstellungen aus der Datenbank, damit GPU-Device korrekt gesetzt ist
    initialize_insightface_settings(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT path FROM photos ORDER BY path").fetchall()

    photo_paths_all = [Path(row[0]) for row in rows]
    photo_paths_existing = [p for p in photo_paths_all if p.exists()]
    photo_paths_str = [str(p) for p in photo_paths_existing]
    missing = len(photo_paths_all) - len(photo_paths_existing)

    # Filtere nur die Fotos, die ein Rematch brauchen
    photos_needing_rematch = get_photos_needing_rematch(db_path, photo_paths_str)
    existing = [Path(p) for p in photos_needing_rematch]

    if not existing:
        print("Keine Fotos brauchen Rematch.")
        return 0

    msg = f"Berechne Personen-Matches fuer {len(existing)} Fotos (uebrig von {len(photo_paths_existing)}"
    if missing:
        msg += f", {missing} nicht mehr vorhanden, werden uebersprungen)"
    else:
        msg += ")"
    print(msg)

    safe_workers = max(1, workers)

    def _process(photo_path: Path) -> tuple[Path, list, int]:
        matches, person_count = match_persons_for_photo(
            db_path=db_path,
            photo_path=photo_path,
            preferred_backend=person_backend,
        )
        return photo_path, matches, person_count

    processed = 0
    matched = 0

    if safe_workers == 1:
        progress = tqdm(existing, desc="Rematch", unit="Foto")
        for photo_path in progress:
            _, matches, _ = _process(photo_path)
            persist_matches_for_photo(db_path=db_path, photo_path=photo_path, matches=matches)
            processed += 1
            if matches:
                matched += 1
    else:
        with ThreadPoolExecutor(max_workers=safe_workers) as executor:
            futures = [executor.submit(_process, p) for p in existing]
            progress = tqdm(as_completed(futures), total=len(futures), desc="Rematch", unit="Foto")
            for future in progress:
                photo_path, matches, _ = future.result()
                persist_matches_for_photo(db_path=db_path, photo_path=photo_path, matches=matches)
                processed += 1
                if matches:
                    matched += 1

    print(f"✓ {processed} Fotos verarbeitet, {matched} mit Personen-Treffer.")
    return 0


def _update_exif_command(
    config: AppConfig,
    custom_db_path: str | None,
) -> int:
    """Aktualisiert nur die EXIF-Daten für alle Fotos in der Datenbank."""
    db_path = config.resolve_db_path(custom_db_path)
    if not db_path.exists():
        print(f"Index nicht gefunden: {db_path}")
        return 1

    # Stelle sicher, dass die neuen Spalten existieren
    ensure_schema(db_path)

    print(f"Aktualisiere EXIF-Daten für alle Fotos in {db_path}...")
    updated = update_exif_only(db_path=db_path)
    print(f"✓ {updated} Fotos aktualisiert")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parents[2]
    config = AppConfig.from_workspace(workspace_root=workspace_root)

    if args.command == "index":
        return _index_command(
            config=config,
            roots=[Path(r) for r in args.root],
            custom_db_path=args.db,
            person_backend=args.person_backend,
            force_reindex=args.force_reindex,
            index_workers=args.index_workers,
            near_duplicates=args.near_duplicates,
            phash_threshold=args.phash_threshold,
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
            max_persons=args.max_persons,
        )
    if args.command == "doctor":
        return run_doctor()
    if args.command == "web":
        return _web_command(
            config=config,
            custom_db_path=args.db,
            custom_cache_dir=args.cache_dir,
            host=args.host,
            port=args.port,
            debug=args.debug,
        )
    if args.command == "album-timelapse":
        return _album_timelapse_command(
            config=config,
            album_id=args.album_id,
            person_name=args.person,
            output_file=args.output,
            custom_db_path=args.db,
            fps=args.fps,
            hold_frames=args.hold,
            morph_frames=args.morph,
            output_size=args.size,
            person_backend=args.person_backend,
            quality_profile=args.quality,
            interpolator=args.interpolator,
            temporal_smooth=args.temporal_smooth,
            detail_boost=args.detail_boost,
            enhance_faces=args.enhance_faces,
            ai_mode=args.ai_mode,
            ai_backend=args.ai_backend,
            ai_strength=args.ai_strength,
        )
    if args.command == "update-exif":
        return _update_exif_command(
            config=config,
            custom_db_path=args.db,
        )
    if args.command == "rematch-persons":
        return _rematch_persons_command(
            config=config,
            custom_db_path=args.db,
            person_backend=args.person_backend,
            workers=args.workers,
        )

    parser.print_help()
    return 1

