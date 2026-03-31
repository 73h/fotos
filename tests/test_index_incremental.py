import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.app.cli as cli_module  # noqa: E402
from src.app.cli import _index_command  # noqa: E402
from src.app.config import AppConfig  # noqa: E402
from src.app.index.store import ensure_schema, get_photo_metadata_map  # noqa: E402
from src.app.ingest import ImageRecord  # noqa: E402


class IncrementalIndexTests(unittest.TestCase):
    def test_get_photo_metadata_map_returns_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_path = photos_dir / "sample.jpg"
            image = Image.new("RGB", (64, 64), color=(150, 60, 90))
            image.save(image_path)
            stat = image_path.stat()

            from src.app.index.store import upsert_photo

            upsert_photo(
                db_path=db_path,
                record=ImageRecord(
                    path=image_path,
                    size_bytes=stat.st_size,
                    modified_ts=stat.st_mtime,
                ),
                labels=["test"],
            )

            metadata = get_photo_metadata_map(db_path=db_path, paths=[image_path])
            self.assertIn(str(image_path), metadata)
            self.assertEqual(metadata[str(image_path)][0], stat.st_size)

    def test_index_skips_unchanged_files_on_reindex(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            image_path = photos_dir / "sample.jpg"
            image = Image.new("RGB", (128, 80), color=(80, 120, 200))
            image.save(image_path)

            config = AppConfig.from_workspace(workspace_root=workspace)
            calls = {"labels": 0, "match": 0}

            original_infer = cli_module.infer_labels_from_path
            original_match = cli_module.match_persons_for_photo

            def fake_infer_labels(path: Path) -> list[str]:
                calls["labels"] += 1
                return ["animal"]

            def fake_match(*args, **kwargs) -> list[object]:
                calls["match"] += 1
                return []

            cli_module.infer_labels_from_path = fake_infer_labels
            cli_module.match_persons_for_photo = fake_match

            try:
                first_rc = _index_command(
                    config=config,
                    roots=[photos_dir],
                    custom_db_path=None,
                    person_backend=None,
                )
                second_rc = _index_command(
                    config=config,
                    roots=[photos_dir],
                    custom_db_path=None,
                    person_backend=None,
                )

                # Unveraenderte Datei wird beim zweiten Lauf nicht erneut verarbeitet.
                self.assertEqual(first_rc, 0)
                self.assertEqual(second_rc, 0)
                self.assertEqual(calls["labels"], 1)
                self.assertEqual(calls["match"], 1)

                with image_path.open("ab") as file_obj:
                    file_obj.write(b"0")

                third_rc = _index_command(
                    config=config,
                    roots=[photos_dir],
                    custom_db_path=None,
                    person_backend=None,
                )
                self.assertEqual(third_rc, 0)
                self.assertEqual(calls["labels"], 2)
                self.assertEqual(calls["match"], 2)

                force_rc = _index_command(
                    config=config,
                    roots=[photos_dir],
                    custom_db_path=None,
                    person_backend=None,
                    force_reindex=True,
                )
                self.assertEqual(force_rc, 0)
                self.assertEqual(calls["labels"], 3)
                self.assertEqual(calls["match"], 3)
            finally:
                cli_module.infer_labels_from_path = original_infer
                cli_module.match_persons_for_photo = original_match

    def test_index_marks_exact_duplicates(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            source = photos_dir / "source.jpg"
            duplicate = photos_dir / "source_copy.jpg"

            image = Image.new("RGB", (140, 90), color=(30, 140, 210))
            image.save(source)
            duplicate.write_bytes(source.read_bytes())

            config = AppConfig.from_workspace(workspace_root=workspace)
            rc = _index_command(
                config=config,
                roots=[photos_dir],
                custom_db_path=None,
                person_backend=None,
                index_workers=1,
            )
            self.assertEqual(rc, 0)

            db_path = config.resolve_db_path()
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT path, duplicate_of_path, duplicate_kind
                    FROM photos
                    WHERE duplicate_kind IS NOT NULL
                    """
                ).fetchall()

            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(rows[0][2], "exact")


if __name__ == "__main__":
    unittest.main()

