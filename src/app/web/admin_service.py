"""
Admin-Service für Indexierung, Rematch und EXIF-Updates über die Web-UI.
Integriert mit dem Job-Manager für Progress-Tracking.
"""
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from ..detectors.labels import infer_labels_from_path
from ..index.store import (
    ensure_schema,
    get_photo_metadata_map,
    phash_of_file,
    resolve_duplicate_marker,
    sha1_of_file,
    update_exif_only,
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
    ) -> str:
        """Startet Full-Index-Job und gibt Job-ID zurück."""
        job_id = f"index_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "full_index")

        def _do_index():
            try:
                self._execute_full_index(
                    job=job,
                    photo_roots=photo_roots,
                    person_backend=person_backend,
                    force_reindex=force_reindex,
                    index_workers=index_workers,
                    near_duplicates=near_duplicates,
                    phash_threshold=phash_threshold,
                )
            except Exception as e:
                raise Exception(f"Index-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_index)
        return job_id

    def start_exif_update(self) -> str:
        """Startet EXIF-Update-Job und gibt Job-ID zurück."""
        job_id = f"exif_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "exif_update")

        def _do_exif():
            try:
                self._execute_exif_update(job)
            except Exception as e:
                raise Exception(f"EXIF-Update-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_exif)
        return job_id

    def start_rematch_persons(
        self,
        person_backend: Optional[str] = None,
        workers: int = 1,
    ) -> str:
        """Startet Rematch-Job und gibt Job-ID zurück."""
        job_id = f"rematch_{uuid.uuid4().hex[:8]}"
        job = self.job_manager.create_job(job_id, "rematch_persons")

        def _do_rematch():
            try:
                self._execute_rematch_persons(
                    job=job,
                    person_backend=person_backend,
                    workers=workers,
                )
            except Exception as e:
                raise Exception(f"Rematch-Fehler: {str(e)}")

        self.job_manager.run_job_async(job_id, _do_rematch)
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
    ) -> None:
        """Führt Full-Index aus."""
        db_path = self.app_config.resolve_db_path()
        ensure_schema(db_path)

        safe_workers = max(1, index_workers)
        safe_threshold = max(0, min(64, phash_threshold))

        def prepare_record(record):
            if job.should_abort():
                return None
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
            with ThreadPoolExecutor(max_workers=safe_workers) as executor:
                futures = [executor.submit(prepare_record, record) for record in to_process]
                for idx, future in enumerate(as_completed(futures)):
                    if job.should_abort():
                        job.message = "Abbruch angefordert"
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
    ) -> None:
        """Führt Rematch aus."""
        db_path = self.app_config.resolve_db_path()
        if not db_path.exists():
            raise ValueError(f"Index nicht gefunden: {db_path}")

        ensure_schema(db_path)

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT path FROM photos ORDER BY path").fetchall()

        photo_paths = [Path(row[0]) for row in rows]
        existing = [p for p in photo_paths if p.exists()]
        missing = len(photo_paths) - len(existing)

        if not existing:
            job.message = "Keine indizierten Fotos gefunden"
            return

        job.total = len(existing)
        job.message = (
            f"Berechne Smile-Scores für {len(existing)} Fotos"
            + (f" ({missing} nicht mehr vorhanden, werden übersprungen)" if missing else "")
        )

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
                _, matches, _ = _process(photo_path)
                persist_matches_for_photo(db_path=db_path, photo_path=photo_path, matches=matches)
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
            with ThreadPoolExecutor(max_workers=safe_workers) as executor:
                futures = [executor.submit(_process, p) for p in existing]
                for future in as_completed(futures):
                    if job.should_abort():
                        job.message = "Abbruch angefordert"
                        return
                    photo_path, matches, _ = future.result()
                    persist_matches_for_photo(db_path=db_path, photo_path=photo_path, matches=matches)
                    processed += 1
                    if matches:
                        matched += 1
                    self.job_manager.update_progress(
                        job.job_id,
                        processed,
                        job.total,
                        f"Verarbeitet: {processed}, mit Treffer: {matched}",
                    )

        job.message = f"✓ {processed} Fotos verarbeitet, {matched} mit Personen-Treffer"

