"""
Tests für Rematch-Label-Konsistenz:

- CLI _rematch_persons_command aktualisiert person_count UND labels_json
- AdminService._execute_rematch_persons aktualisiert person_count UND labels_json
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.cli import _rematch_persons_command  # noqa: E402
from src.app.config import AppConfig  # noqa: E402
from src.app.index.store import ensure_schema, upsert_photo  # noqa: E402
from src.app.ingest import ImageRecord  # noqa: E402
from src.app.persons.service import PersonMatch  # noqa: E402
from src.app.persons.store import upsert_person  # noqa: E402
from src.app.web.admin_jobs import JobManager  # noqa: E402
from src.app.web.admin_service import AdminService  # noqa: E402


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_photo(photos_dir: Path, filename: str = "sample.jpg") -> Path:
    """Erstellt eine echte JPEG-Datei im tmp-Verzeichnis."""
    p = photos_dir / filename
    Image.new("RGB", (64, 64), color=(100, 150, 200)).save(p)
    return p


def _insert_photo(
    db_path: Path,
    photo_path: Path,
    labels: list[str],
    person_count: int = 0,
) -> None:
    """Fügt ein Foto mit vorgegebenen Labels in die DB ein."""
    stat = photo_path.stat()
    upsert_photo(
        db_path=db_path,
        record=ImageRecord(
            path=photo_path,
            size_bytes=stat.st_size,
            modified_ts=stat.st_mtime,
        ),
        labels=labels,
        person_count=person_count,
    )


def _read_photo_row(db_path: Path, photo_path: str) -> dict:
    """Liest labels_json, person_count und search_blob aus der DB."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT labels_json, person_count, search_blob FROM photos WHERE path = ?",
            (photo_path,),
        ).fetchone()
    assert row is not None, f"Foto nicht in DB: {photo_path}"
    return {
        "labels": json.loads(row[0]),
        "person_count": int(row[1]),
        "search_blob": row[2],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class RematchLabelConsistencyTests(unittest.TestCase):
    # -----------------------------------------------------------------------
    # CLI-Tests
    # -----------------------------------------------------------------------

    def test_cli_rematch_updates_person_count_and_labels(self) -> None:
        """`_rematch_persons_command` muss person_count und person-Labels setzen."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            photo_path = _make_photo(photos_dir)
            config = AppConfig.from_workspace(workspace_root=workspace)
            db_path = config.resolve_db_path()
            ensure_schema(db_path)

            _insert_photo(db_path, photo_path, labels=["outdoor", "animal"])

            marie_id = upsert_person(db_path, "Marie")
            fake_match = PersonMatch(
                person_id=marie_id, person_name="Marie", score=0.92, smile_score=None
            )

            with patch("src.app.cli.match_persons_for_photo", return_value=([fake_match], 1)):
                rc = _rematch_persons_command(
                    config=config,
                    custom_db_path=None,
                    person_backend=None,
                    workers=1,
                )

            self.assertEqual(rc, 0)
            row = _read_photo_row(db_path, str(photo_path))
            self.assertEqual(row["person_count"], 1)
            self.assertIn("person", row["labels"])
            self.assertIn("person:marie", row["labels"])
            # Nicht-Personen-Labels müssen erhalten bleiben
            self.assertIn("outdoor", row["labels"])
            self.assertIn("animal", row["labels"])
            self.assertIn("marie", row["search_blob"])

    def test_cli_rematch_clears_old_person_labels_on_no_match(self) -> None:
        """Bei leerem Rematch-Ergebnis: person-Labels entfernen, person_count=0."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            photo_path = _make_photo(photos_dir)
            config = AppConfig.from_workspace(workspace_root=workspace)
            db_path = config.resolve_db_path()
            ensure_schema(db_path)

            # Foto hat bereits veraltete Personen-Labels
            _insert_photo(
                db_path,
                photo_path,
                labels=["outdoor", "person", "person:old"],
                person_count=2,
            )

            with patch("src.app.cli.match_persons_for_photo", return_value=([], 0)):
                rc = _rematch_persons_command(
                    config=config,
                    custom_db_path=None,
                    person_backend=None,
                    workers=1,
                )

            self.assertEqual(rc, 0)
            row = _read_photo_row(db_path, str(photo_path))
            self.assertEqual(row["person_count"], 0)
            self.assertNotIn("person", row["labels"])
            self.assertNotIn("person:old", row["labels"])
            # Nicht-Personen-Labels bleiben erhalten
            self.assertIn("outdoor", row["labels"])

    def test_cli_rematch_multi_worker_updates_person_count_and_labels(self) -> None:
        """`_rematch_persons_command` mit workers>1 muss ebenfalls Labels aktualisieren."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            photo_path = _make_photo(photos_dir)
            config = AppConfig.from_workspace(workspace_root=workspace)
            db_path = config.resolve_db_path()
            ensure_schema(db_path)

            _insert_photo(db_path, photo_path, labels=["city"])

            albert_id = upsert_person(db_path, "Albert")
            fake_match = PersonMatch(
                person_id=albert_id, person_name="Albert", score=0.88, smile_score=0.55
            )

            with patch("src.app.cli.match_persons_for_photo", return_value=([fake_match], 1)):
                rc = _rematch_persons_command(
                    config=config,
                    custom_db_path=None,
                    person_backend=None,
                    workers=2,
                )

            self.assertEqual(rc, 0)
            row = _read_photo_row(db_path, str(photo_path))
            self.assertEqual(row["person_count"], 1)
            self.assertIn("person:albert", row["labels"])
            self.assertIn("city", row["labels"])

    # -----------------------------------------------------------------------
    # AdminService-Tests
    # -----------------------------------------------------------------------

    def test_admin_service_rematch_updates_person_count_and_labels(self) -> None:
        """`AdminService._execute_rematch_persons` muss person_count und Labels aktualisieren."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            photo_path = _make_photo(photos_dir)
            config = AppConfig.from_workspace(workspace_root=workspace)
            db_path = config.resolve_db_path()
            ensure_schema(db_path)

            _insert_photo(db_path, photo_path, labels=["landscape"])

            albert_id = upsert_person(db_path, "Albert")
            fake_match = PersonMatch(
                person_id=albert_id, person_name="Albert", score=0.88, smile_score=0.61
            )

            job_manager = JobManager()
            job = job_manager.create_job("test_job", "rematch_persons")
            admin_service = AdminService(config, job_manager)

            with patch(
                "src.app.web.admin_service.match_persons_for_photo",
                return_value=([fake_match], 1),
            ):
                admin_service._execute_rematch_persons(
                    job=job, person_backend=None, workers=1
                )

            row = _read_photo_row(db_path, str(photo_path))
            self.assertEqual(row["person_count"], 1)
            self.assertIn("person", row["labels"])
            self.assertIn("person:albert", row["labels"])
            self.assertIn("landscape", row["labels"])

    def test_admin_service_rematch_multi_worker_updates_person_count(self) -> None:
        """`AdminService._execute_rematch_persons` mit workers>1 aktualisiert ebenfalls Labels."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            photo_path = _make_photo(photos_dir)
            config = AppConfig.from_workspace(workspace_root=workspace)
            db_path = config.resolve_db_path()
            ensure_schema(db_path)

            _insert_photo(db_path, photo_path, labels=["nature"])

            marie_id = upsert_person(db_path, "Marie")
            fake_match = PersonMatch(
                person_id=marie_id, person_name="Marie", score=0.95, smile_score=None
            )

            job_manager = JobManager()
            job = job_manager.create_job("test_job_multi", "rematch_persons")
            admin_service = AdminService(config, job_manager)

            with patch(
                "src.app.web.admin_service.match_persons_for_photo",
                return_value=([fake_match], 1),
            ):
                admin_service._execute_rematch_persons(
                    job=job, person_backend=None, workers=2
                )

            row = _read_photo_row(db_path, str(photo_path))
            self.assertEqual(row["person_count"], 1)
            self.assertIn("person:marie", row["labels"])
            self.assertIn("nature", row["labels"])

    def test_rematch_order_is_mixed_for_early_progress(self) -> None:
        """Die ersten Schritte sollen alte und neue Fotos mischen."""
        photos = [Path(f"photo_{idx:02d}.jpg") for idx in range(20)]
        sort_ts = {str(path): float(idx) for idx, path in enumerate(photos)}

        mixed = AdminService._build_mixed_rematch_order(photos, sort_ts, seed="job_test")

        self.assertEqual(len(mixed), len(photos))
        self.assertEqual({str(p) for p in mixed}, {str(p) for p in photos})

        first_ten_indices = [int(str(path.stem).split("_")[1]) for path in mixed[:10]]
        older_half = sum(1 for idx in first_ten_indices if idx < 10)
        newer_half = sum(1 for idx in first_ten_indices if idx >= 10)
        self.assertGreaterEqual(older_half, 3)
        self.assertGreaterEqual(newer_half, 3)

    def test_rematch_order_chrono_sorts_oldest_first(self) -> None:
        """Chronologisch soll streng nach Zeitstempel sortieren."""
        photos = [Path("photo_b.jpg"), Path("photo_a.jpg"), Path("photo_c.jpg")]
        sort_ts = {
            str(photos[0]): 20.0,
            str(photos[1]): 10.0,
            str(photos[2]): 30.0,
        }

        ordered = AdminService._order_rematch_paths(photos, sort_ts, order_mode="chrono", seed="job_test")
        self.assertEqual(ordered, [photos[1], photos[0], photos[2]])

    def test_rematch_order_random_is_seeded_but_not_chronological(self) -> None:
        """Voll zufällig soll reproduzierbar sein, aber nicht einfach chronologisch."""
        photos = [Path(f"photo_{idx:02d}.jpg") for idx in range(8)]
        sort_ts = {str(path): float(idx) for idx, path in enumerate(photos)}

        ordered_a = AdminService._order_rematch_paths(photos, sort_ts, order_mode="random", seed="job_test")
        ordered_b = AdminService._order_rematch_paths(photos, sort_ts, order_mode="random", seed="job_test")

        self.assertEqual(ordered_a, ordered_b)
        self.assertEqual({str(p) for p in ordered_a}, {str(p) for p in photos})
        self.assertNotEqual(ordered_a, photos)


if __name__ == "__main__":
    unittest.main()

