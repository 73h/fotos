"""
Admin-Service für Indexierung, Rematch und EXIF-Updates über die Web-UI.
Integriert mit dem Job-Manager für Progress-Tracking.
"""
import random
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from ..detectors.labels import infer_fine_yolo_labels, infer_labels_from_path
from ..index.store import (
    ensure_schema,
    get_admin_config,
    get_photo_labels_map,
    get_photo_metadata_map,
    phash_of_file,
    resolve_duplicate_marker,
    sha1_of_file,
    update_exif_only,
    update_photo_labels_only,
    update_person_labels,
    upsert_photo,
)
from ..ingest import scan_images
from ..persons.service import (
    match_persons_for_photo,
    persist_matches_for_photo,
)
from .admin_jobs import JobManager, JobProgress


class AdminService:
    """Service für Admin-Operationen mit Progress-Tracking."""

    def __init__(self, app_config: AppConfig, job_manager: JobManager):
        self.app_config = app_config
        self.job_manager = job_manager

    def start_full_index(
        self,
        photo_roots: list[str],
        person_backend: Optional[str] = None,
        force_reindex: bool = False,
        index_workers: int = 1,
        near_duplicates: bool = False,
        phash_threshold: int = 6,
        include_fine_labels: bool = False,
        merge_fine_labels: bool = False,
    ) -> str:
        """Startet Full-Index-Job und gibt Job-ID zurück."""
        job_id = f"index_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "full_index")

        def _do_index(progress_job):
            try:
                self._execute_full_index(
                    job=progress_job,
                    photo_roots=photo_roots,
                    person_backend=person_backend,
                    force_reindex=force_reindex,
                    index_workers=index_workers,
                    near_duplicates=near_duplicates,
                    phash_threshold=phash_threshold,
                    include_fine_labels=include_fine_labels,
                    merge_fine_labels=merge_fine_labels,
                )
            except Exception as e:
                raise Exception(f"Index-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_index)
        return job_id

    def start_exif_update(self) -> str:
        """Startet EXIF-Update-Job und gibt Job-ID zurück."""
        job_id = f"exif_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "exif_update")

        def _do_exif(progress_job):
            try:
                self._execute_exif_update(progress_job)
            except Exception as e:
                raise Exception(f"EXIF-Update-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_exif)
        return job_id

    def start_rematch_persons(
        self,
        person_backend: Optional[str] = None,
        workers: int = 1,
        order_mode: str = "mixed",
    ) -> str:
        """Startet Rematch-Job und gibt Job-ID zurück."""
        job_id = f"rematch_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "rematch_persons")

        def _do_rematch(progress_job):
            try:
                self._execute_rematch_persons(
                    job=progress_job,
                    person_backend=person_backend,
                    workers=workers,
                    order_mode=order_mode,
                )
            except Exception as e:
                raise Exception(f"Rematch-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_rematch)
        return job_id

    def start_detect_objects(
        self,
        photo_roots: list[str],
        model_name: Optional[str] = None,
        confidence: Optional[float] = None,
        device: Optional[str] = None,
        labels_filter: Optional[str] = None,
        include_person: bool = False,
    ) -> str:
        """Startet Objekt-Erkennungs-Job und gibt Job-ID zurück."""
        job_id = f"detect_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "detect_objects")

        def _do_detect(progress_job):
            try:
                self._execute_detect_objects(
                    job=progress_job,
                    photo_roots=photo_roots,
                    model_name=model_name,
                    confidence=confidence,
                    device=device,
                    labels_filter=labels_filter,
                    include_person=include_person,
                )
            except Exception as e:
                raise Exception(f"Objekt-Erkennungs-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_detect)
        return job_id

    def start_backfill_fine_labels(self, photo_roots: list[str]) -> str:
        """Startet Backfill-Job für fehlende Fine-Labels."""
        job_id = f"backfill_fine_{uuid.uuid4().hex[:8]}"
        self.job_manager.create_job(job_id, "backfill_fine_labels")

        def _do_backfill(progress_job):
            try:
                self._execute_backfill_fine_labels(job=progress_job, photo_roots=photo_roots)
            except Exception as e:
                raise Exception(f"Fine-Label-Backfill-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_backfill)
        return job_id

    def _execute_full_index(
        self,
        job: JobProgress,
        photo_roots: list[str],
        person_backend: Optional[str] = None,
        force_reindex: bool = False,
        index_workers: int = 1,
        near_duplicates: bool = False,
        phash_threshold: int = 6,
        include_fine_labels: bool = False,
        merge_fine_labels: bool = False,
    ) -> None:
        """Führt Full-Index aus."""
        from ..detectors.labels import initialize_yolo_settings
        from ..persons.embeddings import initialize_insightface_settings

        db_path = self.app_config.resolve_db_path()
        ensure_schema(db_path)

        # Lade alle Einstellungen aus der Datenbank (IM WORKER-THREAD!)
        initialize_yolo_settings(db_path)
        initialize_insightface_settings(db_path)

        safe_workers = max(1, index_workers)
        safe_threshold = max(0, min(64, phash_threshold))
        merge_fine_labels = bool(merge_fine_labels and include_fine_labels)
        existing_labels_by_path: dict[str, list[str]] = {}
        fine_label_filter: set[str] | None = None

        if include_fine_labels:
            admin_config = get_admin_config(db_path)
            raw_csv = str(admin_config.get("yolo_label_allowlist_csv", "")).strip()
            parsed = {
                label.strip().lower()
                for label in raw_csv.split(",")
                if label.strip()
            }
            fine_label_filter = parsed or None

        def prepare_record(record):
            if job.should_abort():
                return None
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

        # Sammle alle Bilder
        all_images = []
        for root_str in photo_roots:
            if job.should_abort():
                job.message = "Abbruch angefordert (Scan-Phase)"
                return
            root = Path(root_str)
            images = scan_images(root=root, supported_extensions=self.app_config.supported_extensions)
            all_images.extend(images)

        total_images = len(all_images)
        job.total = total_images
        job.message = f"Starte Index von {total_images} Bildern..."

        if not all_images:
            job.message = "Keine Bilder gefunden"
            return

        # Filter für Skip-Check
        existing_metadata = (
            {}
            if force_reindex
            else get_photo_metadata_map(
                db_path=db_path,
                paths=[record.path for record in all_images],
            )
        )

        to_process = []
        for record in all_images:
            if job.should_abort():
                job.message = "Abbruch angefordert (Filter-Phase)"
                return
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

        job.total = len(to_process)
        job.message = f"Verarbeite {len(to_process)} Bilder (übersprungen: {total_skipped})"

        # Verarbeitung
        if safe_workers == 1:
            iterator = (prepare_record(record) for record in to_process)
            for idx, result in enumerate(iterator):
                if job.should_abort():
                    job.message = "Abbruch angefordert"
                    return
                if result is None:
                    continue

                record, labels, person_matches, sha1, phash, person_count = result
                duplicate_of_path, duplicate_kind, duplicate_score = resolve_duplicate_marker(
                    db_path=db_path,
                    photo_path=record.path,
                    sha1=sha1,
                    phash=phash,
                    near_duplicates=near_duplicates,
                    phash_threshold=safe_threshold,
                )
                if duplicate_kind is not None:
                    total_duplicates += 1

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
                total_processed += 1
                self.job_manager.update_progress(
                    job.job_id,
                    total_processed,
                    job.total,
                    f"Verarbeitet: {total_processed}, Duplikate: {total_duplicates}",
                )
        else:
            executor = ThreadPoolExecutor(max_workers=safe_workers)
            try:
                futures = [executor.submit(prepare_record, record) for record in to_process]
                for idx, future in enumerate(as_completed(futures)):
                    if job.should_abort():
                        job.message = "Abbruch angefordert"
                        executor.shutdown(wait=False, cancel_futures=True)
                        return

                    result = future.result()
                    if result is None:
                        continue

                    record, labels, person_matches, sha1, phash, person_count = result
                    duplicate_of_path, duplicate_kind, duplicate_score = resolve_duplicate_marker(
                        db_path=db_path,
                        photo_path=record.path,
                        sha1=sha1,
                        phash=phash,
                        near_duplicates=near_duplicates,
                        phash_threshold=safe_threshold,
                    )
                    if duplicate_kind is not None:
                        total_duplicates += 1

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
                    total_processed += 1
                    self.job_manager.update_progress(
                        job.job_id,
                        total_processed,
                        job.total,
                        f"Verarbeitet: {total_processed}, Duplikate: {total_duplicates}",
                    )
            finally:
                executor.shutdown(wait=False)

        job.message = (
            f"✓ Abgeschlossen: {total_images} Dateien gescannt, "
            f"{total_processed} verarbeitet, {total_skipped} übersprungen, "
            f"{total_duplicates} als Duplikat markiert"
        )

    def _execute_exif_update(self, job: JobProgress) -> None:
        """Führt EXIF-Update aus."""
        db_path = self.app_config.resolve_db_path()
        if not db_path.exists():
            raise ValueError(f"Index nicht gefunden: {db_path}")

        ensure_schema(db_path)
        job.message = "Aktualisiere EXIF-Daten..."
        job.total = 1
        job.current = 0

        updated = update_exif_only(db_path=db_path)
        job.message = f"✓ {updated} Fotos aktualisiert"
        job.current = 1

    def _execute_rematch_persons(
        self,
        job: JobProgress,
        person_backend: Optional[str] = None,
        workers: int = 1,
        order_mode: str = "mixed",
    ) -> None:
        """Führt Rematch aus."""
        from ..persons.embeddings import initialize_insightface_settings
        from ..persons.store import get_photos_needing_rematch

        db_path = self.app_config.resolve_db_path()
        if not db_path.exists():
            raise ValueError(f"Index nicht gefunden: {db_path}")

        ensure_schema(db_path)

        # Lade InsightFace-Einstellungen aus der Datenbank (IM WORKER-THREAD!)
        initialize_insightface_settings(db_path)

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT path, COALESCE(taken_ts, modified_ts, 0)
                FROM photos
                ORDER BY COALESCE(taken_ts, modified_ts, 0) ASC, path ASC
                """
            ).fetchall()

        photo_paths_all = [Path(row[0]) for row in rows]
        sort_ts_by_path = {str(row[0]): float(row[1] or 0.0) for row in rows}
        photo_paths_existing = [p for p in photo_paths_all if p.exists()]
        photo_paths_str = [str(p) for p in photo_paths_existing]
        missing = len(photo_paths_all) - len(photo_paths_existing)

        # Filtere nur die Fotos, die ein Rematch brauchen
        photos_needing_rematch = get_photos_needing_rematch(db_path, photo_paths_str)
        existing = [Path(p) for p in photos_needing_rematch]
        existing = self._order_rematch_paths(existing, sort_ts_by_path, order_mode=order_mode, seed=job.job_id)

        if not existing:
            job.message = "Keine Fotos brauchen Rematch"
            return

        job.total = len(existing)
        if missing:
            job.message = (
                f"Berechne Personen-Matches für {len(existing)} Fotos (übrig von {len(photo_paths_existing)}, "
                f"{missing} nicht mehr vorhanden)"
            )
        else:
            job.message = f"Berechne Personen-Matches für {len(existing)} Fotos (übrig von {len(photo_paths_existing)})"

        safe_workers = max(1, workers)

        def _process(photo_path: Path):
            matches, person_count = match_persons_for_photo(
                db_path=db_path,
                photo_path=photo_path,
                preferred_backend=person_backend,
            )
            return photo_path, matches, person_count

        processed = 0
        matched = 0

        if safe_workers == 1:
            for photo_path in existing:
                if job.should_abort():
                    job.message = "Abbruch angefordert"
                    return
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
                self.job_manager.update_progress(
                    job.job_id,
                    processed,
                    job.total,
                    f"Verarbeitet: {processed}, mit Treffer: {matched}",
                )
        else:
            executor = ThreadPoolExecutor(max_workers=safe_workers)
            try:
                futures = [executor.submit(_process, p) for p in existing]
                for future in as_completed(futures):
                    if job.should_abort():
                        job.message = "Abbruch angefordert"
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
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
                    self.job_manager.update_progress(
                        job.job_id,
                        processed,
                        job.total,
                        f"Verarbeitet: {processed}, mit Treffer: {matched}",
                    )
            finally:
                executor.shutdown(wait=False)

        job.message = f"✓ {processed} Fotos verarbeitet, {matched} mit Personen-Treffer"

    @staticmethod
    def _build_mixed_rematch_order(
        photo_paths: list[Path],
        sort_ts_by_path: dict[str, float],
        seed: str,
    ) -> list[Path]:
        """Mischt alte und neue Fotos, damit frueher Fortschritt repräsentativ ist."""
        if len(photo_paths) <= 2:
            return list(photo_paths)

        rng = random.Random(seed)
        sorted_paths = sorted(photo_paths, key=lambda p: (sort_ts_by_path.get(str(p), 0.0), str(p)))
        bucket_count = min(10, len(sorted_paths))
        buckets: list[list[Path]] = [[] for _ in range(bucket_count)]

        for idx, photo_path in enumerate(sorted_paths):
            bucket_idx = min(bucket_count - 1, int((idx * bucket_count) / len(sorted_paths)))
            buckets[bucket_idx].append(photo_path)

        for bucket in buckets:
            rng.shuffle(bucket)

        mixed: list[Path] = []
        cursor = rng.randrange(bucket_count) if bucket_count > 1 else 0

        while len(mixed) < len(sorted_paths):
            emitted = False
            for offset in range(bucket_count):
                idx = (cursor + offset) % bucket_count
                if not buckets[idx]:
                    continue
                mixed.append(buckets[idx].pop())
                emitted = True
            if not emitted:
                break
            cursor = (cursor + 1) % bucket_count

        return mixed

    @staticmethod
    def _order_rematch_paths(
        photo_paths: list[Path],
        sort_ts_by_path: dict[str, float],
        order_mode: str,
        seed: str,
    ) -> list[Path]:
        normalized_mode = str(order_mode or "mixed").strip().lower()
        if normalized_mode == "chrono":
            return sorted(photo_paths, key=lambda p: (sort_ts_by_path.get(str(p), 0.0), str(p)))
        if normalized_mode == "random":
            rng = random.Random(seed)
            shuffled = list(photo_paths)
            rng.shuffle(shuffled)
            return shuffled
        return AdminService._build_mixed_rematch_order(photo_paths, sort_ts_by_path, seed=seed)

    def _execute_detect_objects(
        self,
        job: JobProgress,
        photo_roots: list[str],
        model_name: Optional[str] = None,
        confidence: Optional[float] = None,
        device: Optional[str] = None,
        labels_filter: Optional[str] = None,
        include_person: bool = False,
    ) -> None:
        """Führt Objekt-Erkennung aus."""
        from ..detectors.labels import (
            configure_yolo_runtime,
            initialize_yolo_settings,
            summarize_object_detections,
        )

        db_path = self.app_config.resolve_db_path()
        if db_path.exists():
            initialize_yolo_settings(db_path)

        configure_yolo_runtime(model_name=model_name, confidence=confidence, device=device)

        # Sammle alle Bilder
        all_images = []
        for root_str in photo_roots:
            if job.should_abort():
                job.message = "Abbruch angefordert (Scan-Phase)"
                return
            root = Path(root_str)
            images = scan_images(root=root, supported_extensions=self.app_config.supported_extensions)
            all_images.extend(images)

        total_images = len(all_images)
        job.total = total_images
        job.message = f"Erkenne Objekte in {total_images} Bildern..."

        if not all_images:
            job.message = "Keine Bilder gefunden"
            return

        label_filter = None
        if labels_filter:
            label_filter = set(part.strip().lower() for part in labels_filter.split(",") if part.strip())

        processed = 0
        processed_with_detections = 0

        for record in all_images:
            if job.should_abort():
                job.message = "Abbruch angefordert"
                return

            try:
                summary = summarize_object_detections(
                    record.path,
                    include_person=include_person,
                    label_filter=label_filter,
                )
                if summary.labels:
                    processed_with_detections += 1
            except Exception as e:
                job.message = f"Fehler bei {record.path}: {str(e)}"

            processed += 1
            self.job_manager.update_progress(
                job.job_id,
                processed,
                job.total,
                f"Verarbeitet: {processed}, mit Treffer: {processed_with_detections}",
            )

        job.message = f"✓ {processed} Bilder analysiert, {processed_with_detections} mit Objekten gefunden"

    def _execute_backfill_fine_labels(self, job: JobProgress, photo_roots: list[str]) -> None:
        """Füllt fehlende yolo:* Labels nach, ohne kompletten Reindex."""
        from ..detectors.labels import initialize_yolo_settings

        db_path = self.app_config.resolve_db_path()
        if not db_path.exists():
            raise ValueError(f"Index nicht gefunden: {db_path}")

        ensure_schema(db_path)
        initialize_yolo_settings(db_path)

        admin_config = get_admin_config(db_path)
        raw_csv = str(admin_config.get("yolo_label_allowlist_csv", "")).strip()
        fine_label_filter = {
            label.strip().lower()
            for label in raw_csv.split(",")
            if label.strip()
        } or None

        all_images = []
        for root_str in photo_roots:
            if job.should_abort():
                job.message = "Abbruch angefordert (Scan-Phase)"
                return
            root = Path(root_str)
            all_images.extend(scan_images(root=root, supported_extensions=self.app_config.supported_extensions))

        if not all_images:
            job.message = "Keine Bilder gefunden"
            return

        labels_by_path = get_photo_labels_map(db_path=db_path, paths=[record.path for record in all_images])
        to_backfill = [
            record
            for record in all_images
            if not any(label.startswith("yolo:") for label in labels_by_path.get(str(record.path), []))
        ]

        if not to_backfill:
            job.total = 0
            job.message = "Keine fehlenden Fine-Labels gefunden"
            return

        job.total = len(to_backfill)
        processed = 0
        updated = 0

        for record in to_backfill:
            if job.should_abort():
                job.message = "Abbruch angefordert"
                return

            existing = set(labels_by_path.get(str(record.path), []))
            fine_labels = infer_fine_yolo_labels(
                record.path,
                include_person=False,
                label_filter=fine_label_filter,
            )
            merged = sorted(existing.union(fine_labels))

            if merged != sorted(existing):
                update_photo_labels_only(db_path=db_path, photo_path=str(record.path), labels=merged)
                updated += 1

            processed += 1
            self.job_manager.update_progress(
                job.job_id,
                processed,
                job.total,
                f"Backfill: {processed}/{job.total}, aktualisiert: {updated}",
            )

        job.message = f"✓ Fine-Label-Backfill fertig: {processed} geprüft, {updated} aktualisiert"

