import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from .config import AppConfig
from .doctor import run_doctor
from .detectors.labels import (
    configure_yolo_runtime,
    get_supported_yolo_classes,
    infer_labels_from_path,
    infer_fine_yolo_labels,
    summarize_object_detections,
)
from .index.store import (
    ensure_schema,
    get_admin_config,
    get_photo_labels_map,
    get_photo_metadata_map,
    phash_of_file,
    resolve_duplicate_marker,
    sha1_of_file,
    upsert_photo,
    update_exif_only,
    update_person_labels,
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

    index_parser.add_argument(
        "--include-fine-labels",
        action="store_true",
        help="Speichert auch feine YOLO-Klassenlabels (z.B. 'yolo:cat', 'yolo:chair') zusätzlich zu groben Labels",
    )
    index_parser.add_argument(
        "--merge-fine-labels",
        action="store_true",
        help="Behält bestehende 'yolo:*' Labels und ergänzt neue Treffer (nur mit --include-fine-labels)",
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

    detect_parser = subparsers.add_parser(
        "detect-objects",
        help="Detaillierte Tier-/Objekt-Erkennung via YOLO (ohne Indexierung)",
    )
    detect_parser.add_argument(
        "inputs",
        nargs="+",
        help="Bilddatei(en) oder Ordner mit Bildern",
    )
    detect_parser.add_argument(
        "--db",
        default=None,
        help="Optional: SQLite-DB zum Laden gespeicherter YOLO-Einstellungen",
    )
    detect_parser.add_argument(
        "--model",
        default=None,
        help="YOLO-Modellname oder Pfad (z.B. yolov8n.pt, yolov8m.pt)",
    )
    detect_parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="Konfidenzschwelle 0..1 (überschreibt DB/Default)",
    )
    detect_parser.add_argument(
        "--device",
        default=None,
        help='Device wie "cpu", "0" oder "auto"',
    )
    detect_parser.add_argument(
        "--labels",
        default=None,
        help="Kommagetrennte YOLO-Klassen filtern, z.B. cat,dog,car,chair",
    )
    detect_parser.add_argument(
        "--include-person",
        action="store_true",
        help="Nimmt auch die YOLO-Klasse 'person' in die Objektausgabe auf (ohne Personen-Identifikation)",
    )
    detect_parser.add_argument(
        "--json",
        action="store_true",
        help="Ausgabe als JSON statt als Textbericht",
    )
    detect_parser.add_argument(
        "--output",
        default=None,
        help="Optionaler Dateipfad für den Bericht (TXT oder JSON)",
    )

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
    include_fine_labels: bool = False,
    merge_fine_labels: bool = False,
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
    existing_labels_by_path: dict[str, list[str]] = {}
    fine_label_filter: set[str] | None = None

    if merge_fine_labels and not include_fine_labels:
        print("Hinweis: --merge-fine-labels wird ohne --include-fine-labels ignoriert.")

    if include_fine_labels:
        try:
            admin_config = get_admin_config(db_path)
            raw_csv = str(admin_config.get("yolo_label_allowlist_csv", "")).strip()
            parsed = {
                label.strip().lower()
                for label in raw_csv.split(",")
                if label.strip()
            }
            fine_label_filter = parsed or None
        except Exception:
            fine_label_filter = None

    def prepare_record(record):
        labels = set(infer_labels_from_path(record.path))
        
        if include_fine_labels:
            if merge_fine_labels:
                existing_labels = existing_labels_by_path.get(str(record.path), [])
                labels.update(label for label in existing_labels if label.startswith("yolo:"))
            fine_labels = infer_fine_yolo_labels(
                record.path,
                include_person=False,
                label_filter=fine_label_filter,
            )
            labels.update(fine_labels)
        
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

        existing_labels_by_path = (
            get_photo_labels_map(db_path=db_path, paths=[record.path for record in to_process])
            if include_fine_labels and merge_fine_labels
            else {}
        )

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


def _parse_label_filter(raw_labels: str | None) -> set[str] | None:
    if not raw_labels:
        return None
    labels = {part.strip().lower() for part in raw_labels.split(",") if part.strip()}
    return labels or None


def _collect_detection_targets(raw_inputs: list[str], supported_extensions: tuple[str, ...]) -> list[Path]:
    targets: list[Path] = []
    seen: set[str] = set()
    supported = {extension.lower() for extension in supported_extensions}

    for raw_input in raw_inputs:
        path = Path(raw_input)
        if path.is_dir():
            for record in scan_images(path, supported_extensions=supported_extensions):
                normalized = str(record.path.resolve())
                if normalized in seen:
                    continue
                seen.add(normalized)
                targets.append(record.path)
            continue

        if path.is_file():
            if path.suffix.lower() not in supported:
                print(f"Überspringe nicht unterstützte Datei: {path}", file=sys.stderr)
                continue

            normalized = str(path.resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            targets.append(path)
            continue

        print(f"Pfad nicht gefunden oder kein Bild/Ordner: {path}", file=sys.stderr)

    return targets


def _format_detection_report(summaries) -> str:
    lines: list[str] = []
    for index, summary in enumerate(summaries, start=1):
        if index > 1:
            lines.append("")

        labels = ", ".join(summary.labels) if summary.labels else "-"
        label_counts = ", ".join(
            f"{label}={count}" for label, count in summary.counts_by_label.items()
        ) or "-"
        kind_counts = ", ".join(
            f"{label}={count}" for label, count in summary.counts_by_kind.items()
        ) or "-"
        group_counts = ", ".join(
            f"{label}={count}" for label, count in summary.counts_by_group.items()
        ) or "-"

        lines.append(f"[{index}] {summary.path}")
        lines.append(
            f"  Modell: {summary.model_name} | Device: {summary.device} | Confidence: {summary.confidence_threshold:.2f}"
        )
        lines.append(f"  Klassen: {labels}")
        lines.append(f"  Counts nach Klasse: {label_counts}")
        lines.append(f"  Counts nach Art: {kind_counts}")
        lines.append(f"  Counts nach Gruppe: {group_counts}")

        if not summary.detections:
            lines.append("  Keine Treffer über der Konfidenzschwelle.")
            continue

        lines.append("  Treffer:")
        for detection in summary.detections:
            bbox_text = (
                f" @ {list(detection.bbox)}"
                if detection.bbox is not None
                else ""
            )
            lines.append(
                f"    - {detection.label} | art={detection.kind} | gruppe={detection.group} | conf={detection.confidence:.3f}{bbox_text}"
            )

    return "\n".join(lines)


def _detect_objects_command(
    config: AppConfig,
    raw_inputs: list[str],
    custom_db_path: str | None,
    model_name: str | None,
    confidence: float | None,
    device: str | None,
    raw_labels: str | None,
    include_person: bool,
    json_output: bool,
    output_path: str | None,
) -> int:
    from .detectors.labels import initialize_yolo_settings

    if custom_db_path is not None:
        initialize_yolo_settings(config.resolve_db_path(custom_db_path))

    configure_yolo_runtime(model_name=model_name, confidence=confidence, device=device)

    supported_classes = get_supported_yolo_classes()
    if not supported_classes:
        print(
            "YOLO konnte nicht geladen werden. Bitte Modellpfad, Device und installierte Abhängigkeiten prüfen.",
            file=sys.stderr,
        )
        return 1

    label_filter = _parse_label_filter(raw_labels)
    if label_filter is not None:
        unknown = sorted(label_filter.difference(set(supported_classes)))
        if unknown:
            print(
                "Hinweis: Diese Klassen kennt das geladene Modell nicht oder anders benannt: "
                + ", ".join(unknown),
                file=sys.stderr,
            )

    targets = _collect_detection_targets(raw_inputs, supported_extensions=config.supported_extensions)
    if not targets:
        print("Keine unterstützten Bilddateien zum Analysieren gefunden.", file=sys.stderr)
        return 1

    summaries = [
        summarize_object_detections(
            path,
            include_person=include_person,
            label_filter=label_filter,
        )
        for path in targets
    ]

    rendered_output = (
        json.dumps([summary.to_dict() for summary in summaries], ensure_ascii=False, indent=2)
        if json_output
        else _format_detection_report(summaries)
    )

    if output_path:
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered_output, encoding="utf-8")
        print(f"Bericht geschrieben: {target_path}")
        return 0

    print(rendered_output)
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
        print("Hinweis: --person-backend wird ignoriert; es gilt person_backend aus der Admin-Konfiguration (SQLite).")

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
            _, matches, person_count = _process(photo_path)
            persist_matches_for_photo(db_path=db_path, photo_path=photo_path, matches=matches)
            update_person_labels(
                db_path=db_path,
                photo_path=str(photo_path),
                person_matches=matches,
                person_count=person_count,
            )
            processed += 1
            if matches:
                matched += 1
    else:
        with ThreadPoolExecutor(max_workers=safe_workers) as executor:
            futures = [executor.submit(_process, p) for p in existing]
            progress = tqdm(as_completed(futures), total=len(futures), desc="Rematch", unit="Foto")
            for future in progress:
                photo_path, matches, person_count = future.result()
                persist_matches_for_photo(db_path=db_path, photo_path=photo_path, matches=matches)
                update_person_labels(
                    db_path=db_path,
                    photo_path=str(photo_path),
                    person_matches=matches,
                    person_count=person_count,
                )
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
            include_fine_labels=args.include_fine_labels,
            merge_fine_labels=args.merge_fine_labels,
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
    if args.command == "detect-objects":
        return _detect_objects_command(
            config=config,
            raw_inputs=args.inputs,
            custom_db_path=args.db,
            model_name=args.model,
            confidence=args.confidence,
            device=args.device,
            raw_labels=args.labels,
            include_person=args.include_person,
            json_output=args.json,
            output_path=args.output,
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
        return run_doctor(db_path=config.resolve_db_path(args.db))
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

