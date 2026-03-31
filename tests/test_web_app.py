import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.config import AppConfig  # noqa: E402
from src.app.index.store import ensure_schema, upsert_photo  # noqa: E402
from src.app.ingest import ImageRecord  # noqa: E402
from src.app.web import create_app  # noqa: E402


class WebAppTests(unittest.TestCase):
    def test_api_search_pagination_and_thumbnail_cache(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            for index in range(2):
                image_path = photos_dir / f"sample_{index}.jpg"
                image = Image.new("RGB", (200, 120), color=(80 + index * 20, 120, 180))
                image.save(image_path)
                stat = image_path.stat()
                record = ImageRecord(
                    path=image_path,
                    size_bytes=stat.st_size,
                    modified_ts=stat.st_mtime + index,
                )
                upsert_photo(db_path=db_path, record=record, labels=["animal", "urlaub"])

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            response = client.get("/api/search?q=animal&page=1&per_page=1")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None

            self.assertEqual(payload["total"], 2)
            self.assertEqual(payload["pages"], 2)
            self.assertTrue(payload["has_next"])
            self.assertEqual(len(payload["items"]), 1)

            thumb_url = payload["items"][0]["thumb_url"]
            thumb_response = client.get(thumb_url)
            self.assertEqual(thumb_response.status_code, 200)
            thumb_response.close()

            cached_files = list((cache_dir / "thumbnails").rglob("*.jpg"))
            self.assertGreaterEqual(len(cached_files), 1)


if __name__ == "__main__":
    unittest.main()

