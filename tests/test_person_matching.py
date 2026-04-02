import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.persons import service  # noqa: E402
from src.app.persons import store  # noqa: E402
from src.app.persons import embeddings  # noqa: E402
from src.app.persons.embeddings import resolve_backend  # noqa: E402
from src.app.index.store import ensure_schema  # noqa: E402


class PersonMatchingTests(unittest.TestCase):
    def test_cosine_similarity_identical_vectors(self) -> None:
        left = [1.0, 2.0, 3.0]
        right = [1.0, 2.0, 3.0]
        score = service.cosine_similarity(left, right)
        self.assertAlmostEqual(score, 1.0, places=6)

    def test_extract_person_signatures_uses_image_fallback(self) -> None:
        original = service.yolo_labels.detect_person_boxes
        service.yolo_labels.detect_person_boxes = lambda _: []

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                image_path = Path(tmp_dir) / "sample_person.jpg"
                image = Image.new("RGB", (128, 128), color=(220, 120, 90))
                image.save(image_path)

                backend_name, signatures, box_count = service.extract_person_signatures(
                    image_path,
                    preferred_backend="histogram",
                )
                self.assertEqual(backend_name, "histogram")
                self.assertGreaterEqual(len(signatures), 1)
                self.assertEqual(len(signatures[0][0]), 96)
                self.assertIsNone(signatures[0][1])
                self.assertIsInstance(box_count, int)
        finally:
            service.yolo_labels.detect_person_boxes = original

    def test_auto_backend_resolves_to_valid_backend(self) -> None:
        backend = resolve_backend("auto")
        self.assertIn(backend.name, {"histogram", "insightface"})

    def test_strict_insightface_backend_raises_without_fallback(self) -> None:
        embeddings._BACKEND_CACHE.clear()
        with patch("src.app.persons.embeddings.InsightFaceBackend", side_effect=RuntimeError("missing insightface")):
            with self.assertRaises(RuntimeError):
                resolve_backend("insightface", strict=True)

    def test_match_persons_for_photo_keeps_only_best_person_per_detected_face(self) -> None:
        signatures = [([0.1], None), ([0.2], None)]

        def fake_score(signature, _references_by_person, smile_score=None):
            if signature == [0.1]:
                return [
                    service.PersonMatch(person_id=1, person_name="Marie", score=0.70, smile_score=smile_score),
                ]
            return [
                service.PersonMatch(person_id=1, person_name="Marie", score=0.95, smile_score=smile_score),
                service.PersonMatch(person_id=2, person_name="Albert", score=0.81, smile_score=smile_score),
            ]

        with (
            patch.object(service, "extract_person_signatures", return_value=("histogram", signatures, 2)),
            patch.object(service, "list_person_references", return_value=[object()]),
            patch.object(service, "_group_references_by_person", return_value={}),
            patch.object(service, "_score_signature_against_references", side_effect=fake_score),
            patch.object(service, "_PERSON_TOP_K", 3),
        ):
            matches, person_count = service.match_persons_for_photo(
                db_path=Path("dummy.db"),
                photo_path=Path("photo.jpg"),
            )

        self.assertEqual(person_count, 2)
        self.assertEqual([(match.person_name, round(match.score, 2)) for match in matches], [("Marie", 0.95)])

    def test_replace_photo_person_matches_removes_stale_matches(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / "photo_index.db"
            ensure_schema(db_path)

            marie_id = store.upsert_person(db_path, "Marie")
            albert_id = store.upsert_person(db_path, "Albert")

            store.replace_photo_person_matches(
                db_path=db_path,
                photo_path="photo.jpg",
                matches=[
                    (marie_id, 0.91, 0.77),
                    (albert_id, 0.82, 0.12),
                ],
            )
            store.replace_photo_person_matches(
                db_path=db_path,
                photo_path="photo.jpg",
                matches=[
                    (marie_id, 0.96, 0.88),
                ],
            )

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT person_id, score, smile_score FROM photo_person_matches WHERE photo_path = ? ORDER BY person_id",
                    ("photo.jpg",),
                ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0][0]), marie_id)
        self.assertAlmostEqual(float(rows[0][1]), 0.96, places=6)
        self.assertAlmostEqual(float(rows[0][2]), 0.88, places=6)


if __name__ == "__main__":
    unittest.main()
