import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.persons import service  # noqa: E402
from src.app.persons import embeddings  # noqa: E402
from src.app.persons.embeddings import resolve_backend  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
