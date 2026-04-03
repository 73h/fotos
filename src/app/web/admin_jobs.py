"""
Admin-Job-Management für Index-, Rematch- und EXIF-Operationen.
Bietet Progress-Tracking und Abort-Funktionalität für lange laufende Tasks.
"""
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class JobStatus(str, Enum):
    """Status eines Jobs."""
    PENDING = "pending"      # Noch nicht gestartet
    RUNNING = "running"      # Läuft gerade
    COMPLETED = "completed"  # Erfolgreich abgeschlossen
    FAILED = "failed"         # Mit Fehler beendet
    ABORTED = "aborted"       # Vom Nutzer abgebrochen


@dataclass
class JobProgress:
    """Progress-Informationen eines Jobs."""
    job_id: str
    job_type: str  # "index", "rematch", "exif", etc.
    status: JobStatus = JobStatus.PENDING
    current: int = 0
    total: int = 0
    percentage: float = 0.0
    message: str = ""
    error: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    _abort_flag: bool = field(default=False, init=False)

    def to_dict(self) -> dict:
        """Serialisierung zu JSON-kompatiblem Dict."""
        elapsed = time.time() - self.start_time
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "current": self.current,
            "total": self.total,
            "percentage": round(self.percentage, 2),
            "message": self.message,
            "error": self.error,
            "start_time": self.start_time,
            "elapsed_seconds": round(elapsed, 1),
            "end_time": self.end_time,
        }

    def should_abort(self) -> bool:
        """Prüft, ob Abbruch angefordert wurde."""
        return self._abort_flag

    def request_abort(self) -> None:
        """Fordert Abbruch an."""
        self._abort_flag = True


class JobManager:
    """Verwaltet Jobs und deren Progress."""

    def __init__(self):
        self._jobs: dict[str, JobProgress] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def create_job(
        self,
        job_id: str,
        job_type: str,
        total: int = 0,
    ) -> JobProgress:
        """Erstellt einen neuen Job."""
        with self._lock:
            progress = JobProgress(
                job_id=job_id,
                job_type=job_type,
                total=total,
            )
            self._jobs[job_id] = progress
        return progress

    def get_job(self, job_id: str) -> Optional[JobProgress]:
        """Holt Job-Progress."""
        with self._lock:
            return self._jobs.get(job_id)

    def update_progress(
        self,
        job_id: str,
        current: int,
        total: int,
        message: str = "",
    ) -> None:
        """Aktualisiert Progress eines Jobs."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.current = current
                job.total = max(total, current)
                job.percentage = (current / total * 100) if total > 0 else 0
                job.message = message

    def set_job_running(self, job_id: str) -> None:
        """Setzt Job-Status auf RUNNING."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.RUNNING

    def set_job_completed(self, job_id: str, message: str = "") -> None:
        """Markiert Job als abgeschlossen."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.COMPLETED
                job.percentage = 100.0
                job.current = job.total
                job.message = message
                job.end_time = time.time()

    def set_job_failed(self, job_id: str, error: str) -> None:
        """Markiert Job als fehlgeschlagen."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error = error
                job.end_time = time.time()

    def set_job_aborted(self, job_id: str) -> None:
        """Markiert Job als abgebrochen."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.ABORTED
                job.end_time = time.time()

    def request_abort(self, job_id: str) -> bool:
        """Fordert Abbruch eines Jobs an."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status == JobStatus.RUNNING:
                job.request_abort()
                return True
        return False

    def run_job_async(
        self,
        job_id: str,
        task_func: Callable[[JobProgress], None],
    ) -> None:
        """Führt einen Job in einem separaten Thread aus."""
        job = self.get_job(job_id)
        if not job:
            return

        def _run():
            self.set_job_running(job_id)
            try:
                task_func(job)
                # Prüfe ob Abbruch angefordert wurde
                if job.should_abort():
                    self.set_job_aborted(job_id)
                else:
                    self.set_job_completed(job_id)
            except Exception as e:
                self.set_job_failed(job_id, str(e))

        thread = threading.Thread(target=_run, daemon=False)
        with self._lock:
            self._threads[job_id] = thread
        thread.start()

    def get_all_jobs(self) -> list[JobProgress]:
        """Gibt alle Jobs zurück."""
        with self._lock:
            return list(self._jobs.values())

    def cleanup_old_jobs(self, max_age_seconds: int = 3600) -> None:
        """Entfernt alte abgeschlossene Jobs (Standard: 1 Stunde)."""
        now = time.time()
        with self._lock:
            to_remove = []
            for job_id, job in self._jobs.items():
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED):
                    if job.end_time and now - job.end_time > max_age_seconds:
                        to_remove.append(job_id)
            for job_id in to_remove:
                del self._jobs[job_id]
                self._threads.pop(job_id, None)


# Globale Job-Manager-Instanz
_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    """Gibt die globale JobManager-Instanz zurück."""
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager

