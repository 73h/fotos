import sys
import base64
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.config import AppConfig  # noqa: E402
from src.app.index.store import ensure_schema, upsert_photo  # noqa: E402
from src.app.ingest import ExifData, ImageRecord  # noqa: E402
from src.app.persons.service import EnrollResult  # noqa: E402
from src.app.persons.ranking import AgingSelectionResult  # noqa: E402
from src.app.web import create_app  # noqa: E402
from src.app.web.admin_jobs import JobManager  # noqa: E402


class WebAppTests(unittest.TestCase):
    def test_admin_config_is_persisted_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            save_payload = {
                "photo_roots": [str(photos_dir)],
                "person_backend": "histogram",
                "force_reindex": True,
                "index_workers": 10,
                "near_duplicates": True,
                "phash_threshold": 8,
                "rematch_workers": 4,
            }
            save_response = client.post("/api/admin/config", json=save_payload)
            self.assertEqual(save_response.status_code, 200)
            saved = save_response.get_json()
            assert saved is not None
            self.assertEqual(saved["photo_roots"], [str(photos_dir)])
            self.assertEqual(saved["index_workers"], 10)
            self.assertEqual(saved["rematch_workers"], 4)

            load_response = client.get("/api/admin/config")
            self.assertEqual(load_response.status_code, 200)
            loaded = load_response.get_json()
            assert loaded is not None
            self.assertEqual(loaded["person_backend"], "histogram")
            self.assertTrue(loaded["force_reindex"])
            self.assertTrue(loaded["near_duplicates"])
            self.assertEqual(loaded["phash_threshold"], 8)

            start_response = client.post(
                "/api/admin/config/start-index",
                json={
                    "photo_roots": [str(photos_dir)],
                    "person_backend": "auto",
                    "force_reindex": False,
                    "index_workers": 2,
                    "near_duplicates": False,
                    "phash_threshold": 6,
                },
            )
            self.assertEqual(start_response.status_code, 200)

            after_start_response = client.get("/api/admin/config")
            self.assertEqual(after_start_response.status_code, 200)
            after_start = after_start_response.get_json()
            assert after_start is not None
            self.assertEqual(after_start["person_backend"], "auto")
            self.assertEqual(after_start["index_workers"], 2)

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
                upsert_photo(
                    db_path=db_path,
                    record=record,
                    labels=["animal", "urlaub"],
                    person_count=2,
                )

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

            default_response = client.get("/api/search?q=animal")
            self.assertEqual(default_response.status_code, 200)
            default_payload = default_response.get_json()
            assert default_payload is not None
            self.assertEqual(default_payload["per_page"], 24)

            create_album_response = client.post(
                "/albums",
                data={"name": "Marie Favoriten", "q": "animal", "per_page": 24},
            )
            self.assertEqual(create_album_response.status_code, 200)
            self.assertIn("Marie Favoriten", create_album_response.get_data(as_text=True))

            with sqlite3.connect(db_path) as conn:
                album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Marie Favoriten",)).fetchone()
            self.assertIsNotNone(album_row)
            album_id = int(album_row[0])

            photo_token = base64.urlsafe_b64encode(str(photos_dir / "sample_0.jpg").encode("utf-8")).decode("ascii").rstrip("=")
            add_photo_response = client.post(
                f"/albums/{album_id}/add-photo",
                data={"photo_token": photo_token},
            )
            self.assertEqual(add_photo_response.status_code, 200)
            add_payload = add_photo_response.get_json()
            assert add_payload is not None
            self.assertTrue(add_payload["ok"])
            self.assertEqual(add_payload["photo_count"], 1)

            album_filter_response = client.get(f"/api/search?album_id={album_id}")
            self.assertEqual(album_filter_response.status_code, 200)
            album_payload = album_filter_response.get_json()
            assert album_payload is not None
            self.assertEqual(album_payload["total"], 1)
            self.assertEqual(album_payload["active_album_id"], album_id)
            self.assertEqual(len(album_payload["items"]), 1)

            partial_response = client.get("/search?q=animal")
            self.assertEqual(partial_response.status_code, 200)
            html = partial_response.get_data(as_text=True)
            self.assertNotIn("Person(en)", html)
            self.assertNotIn("In Album ziehen", html)

    def test_search_reset_link_and_album_duplicate_menu(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_path = photos_dir / "menu_sample.jpg"
            Image.new("RGB", (220, 140), color=(100, 120, 180)).save(image_path)
            stat = image_path.stat()
            record = ImageRecord(
                path=image_path,
                size_bytes=stat.st_size,
                modified_ts=stat.st_mtime,
            )
            upsert_photo(
                db_path=db_path,
                record=record,
                labels=["urlaub"],
                person_count=2,
            )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            create_album_response = client.post(
                "/albums",
                data={"name": "Sommer", "q": "urlaub", "per_page": 24, "person_count": 2},
            )
            self.assertEqual(create_album_response.status_code, 200)

            with sqlite3.connect(db_path) as conn:
                album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Sommer",)).fetchone()
            self.assertIsNotNone(album_row)
            album_id = int(album_row[0])

            photo_token = base64.urlsafe_b64encode(str(image_path).encode("utf-8")).decode("ascii").rstrip("=")
            add_photo_response = client.post(
                f"/albums/{album_id}/add-photo",
                data={"photo_token": photo_token},
            )
            self.assertEqual(add_photo_response.status_code, 200)

            page_response = client.get(f"/?q=urlaub&album_id={album_id}&person_count=2")
            self.assertEqual(page_response.status_code, 200)
            page_html = page_response.get_data(as_text=True)
            self.assertIn('class="search-reset-link"', page_html)
            self.assertIn('href="/"', page_html)

            sidebar_response = client.get(f"/albums/sidebar?q=urlaub&album_id={album_id}&person_count=2")
            self.assertEqual(sidebar_response.status_code, 200)
            sidebar_html = sidebar_response.get_data(as_text=True)
            self.assertIn("album-menu-toggle", sidebar_html)
            self.assertIn("Album-Aktionen", sidebar_html)
            self.assertIn("Kopieren", sidebar_html)
            self.assertIn("Umbenennen", sidebar_html)
            self.assertIn("Löschen", sidebar_html)

            duplicate_response = client.post(f"/albums/{album_id}/duplicate")
            self.assertEqual(duplicate_response.status_code, 200)
            duplicate_payload = duplicate_response.get_json()
            assert duplicate_payload is not None
            self.assertTrue(duplicate_payload["ok"])
            self.assertEqual(duplicate_payload["photo_count"], 1)
            self.assertIn("Kopie", duplicate_payload["name"])

            with sqlite3.connect(db_path) as conn:
                copied_row = conn.execute(
                    "SELECT id FROM albums WHERE name = ?",
                    (str(duplicate_payload["name"]),),
                ).fetchone()
                self.assertIsNotNone(copied_row)
                copied_count = conn.execute(
                    "SELECT COUNT(*) FROM album_photos WHERE album_id = ?",
                    (int(copied_row[0]),),
                ).fetchone()
            self.assertIsNotNone(copied_count)
            self.assertEqual(int(copied_count[0]), 1)

    def test_reference_album_can_retrain_person_from_album_images(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_path = photos_dir / "ref_sample.jpg"
            Image.new("RGB", (220, 140), color=(120, 110, 180)).save(image_path)
            stat = image_path.stat()
            upsert_photo(
                db_path=db_path,
                record=ImageRecord(path=image_path, size_bytes=stat.st_size, modified_ts=stat.st_mtime),
                labels=["portrait"],
                person_count=1,
            )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            create_album_response = client.post(
                "/albums",
                data={"name": "Ref: Marie Curie", "q": "", "per_page": 24},
            )
            self.assertEqual(create_album_response.status_code, 200)

            with sqlite3.connect(db_path) as conn:
                album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Ref: Marie Curie",)).fetchone()
            self.assertIsNotNone(album_row)
            album_id = int(album_row[0])

            photo_token = base64.urlsafe_b64encode(str(image_path).encode("utf-8")).decode("ascii").rstrip("=")
            add_photo_response = client.post(
                f"/albums/{album_id}/add-photo",
                data={"photo_token": photo_token},
            )
            self.assertEqual(add_photo_response.status_code, 200)

            sidebar_response = client.get("/albums/sidebar")
            self.assertEqual(sidebar_response.status_code, 200)
            sidebar_html = sidebar_response.get_data(as_text=True)
            self.assertIn("Person anlernen", sidebar_html)
            self.assertIn('data-reference-person-name="Marie Curie"', sidebar_html)

            def _run_job_inline(self, job_id, task_func):
                job = self.get_job(job_id)
                if not job:
                    return
                self.set_job_running(job_id)
                try:
                    task_func(job)
                    if job.should_abort():
                        self.set_job_aborted(job_id)
                    else:
                        self.set_job_completed(job_id)
                except Exception as exc:
                    self.set_job_failed(job_id, str(exc))

            with patch(
                "src.app.web.routes.enroll_person_from_paths",
                return_value=EnrollResult(
                    person_name="Marie Curie",
                    backend="insightface",
                    image_count=1,
                    sample_count=2,
                ),
            ) as mocked_enroll, patch.object(JobManager, "run_job_async", _run_job_inline):
                train_response = client.post(f"/albums/{album_id}/train-reference")

            self.assertEqual(train_response.status_code, 202)
            train_payload = train_response.get_json()
            assert train_payload is not None
            self.assertTrue(train_payload["ok"])
            self.assertEqual(train_payload["person_name"], "Marie Curie")
            self.assertIn("job_id", train_payload)

            mocked_enroll.assert_called_once()
            call_kwargs = mocked_enroll.call_args.kwargs
            self.assertEqual(call_kwargs["person_name"], "Marie Curie")
            self.assertEqual(call_kwargs["preferred_backend"], "insightface")
            self.assertTrue(call_kwargs["strict_backend"])
            self.assertEqual([Path(path) for path in call_kwargs["image_paths"]], [image_path])

            job_status_response = client.get(f"/api/admin/job/{train_payload['job_id']}")
            self.assertEqual(job_status_response.status_code, 200)
            job_status_payload = job_status_response.get_json()
            assert job_status_payload is not None
            self.assertEqual(job_status_payload["job_type"], "train_reference_person")

            jobs_response = client.get("/api/admin/jobs")
            self.assertEqual(jobs_response.status_code, 200)
            jobs_payload = jobs_response.get_json()
            assert jobs_payload is not None
            self.assertTrue(any(job["job_id"] == train_payload["job_id"] for job in jobs_payload))

            non_ref_create_response = client.post(
                "/albums",
                data={"name": "Urlaub", "q": "", "per_page": 24},
            )
            self.assertEqual(non_ref_create_response.status_code, 200)
            with sqlite3.connect(db_path) as conn:
                non_ref_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Urlaub",)).fetchone()
            self.assertIsNotNone(non_ref_row)
            non_ref_album_id = int(non_ref_row[0])

            non_ref_response = client.post(f"/albums/{non_ref_album_id}/train-reference")
            self.assertEqual(non_ref_response.status_code, 400)

    def test_person_aging_album_can_be_built_via_web_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_path = photos_dir / "aging_source.jpg"
            Image.new("RGB", (220, 140), color=(110, 120, 180)).save(image_path)
            stat = image_path.stat()
            upsert_photo(
                db_path=db_path,
                record=ImageRecord(path=image_path, size_bytes=stat.st_size, modified_ts=stat.st_mtime),
                labels=["portrait"],
                person_count=1,
            )

            with sqlite3.connect(db_path) as conn:
                conn.execute("INSERT INTO persons (name) VALUES (?)", ("Marie Curie",))
                person_id = int(conn.execute("SELECT id FROM persons WHERE name = ?", ("Marie Curie",)).fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO photo_person_matches (photo_path, person_id, score, smile_score, matched_ts)
                    VALUES (?, ?, ?, ?, strftime('%s','now'))
                    """,
                    (str(image_path), person_id, 0.93, 0.6),
                )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            sidebar_response = client.get("/albums/sidebar")
            self.assertEqual(sidebar_response.status_code, 200)
            sidebar_html = sidebar_response.get_data(as_text=True)
            self.assertIn("Aging-Best-of Album", sidebar_html)
            self.assertIn("aging-album-start-btn", sidebar_html)
            self.assertIn("aging-album-quality-select", sidebar_html)
            self.assertIn("aging-album-target-select", sidebar_html)
            self.assertIn("aging-album-auto-timelapse", sidebar_html)

            create_target_album_response = client.post(
                "/albums",
                data={"name": "Bestehendes Aging", "q": "", "per_page": 24},
            )
            self.assertEqual(create_target_album_response.status_code, 200)

            with sqlite3.connect(db_path) as conn:
                target_album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Bestehendes Aging",)).fetchone()
            self.assertIsNotNone(target_album_row)
            target_album_id = int(target_album_row[0])

            def _run_job_inline(self, job_id, task_func):
                job = self.get_job(job_id)
                if not job:
                    return
                self.set_job_running(job_id)
                try:
                    task_func(job)
                    if job.should_abort():
                        self.set_job_aborted(job_id)
                    else:
                        self.set_job_completed(job_id)
                except Exception as exc:
                    self.set_job_failed(job_id, str(exc))

            with patch(
                "src.app.web.routes.select_aging_timelapse_photo_paths",
                return_value=AgingSelectionResult(
                    photo_paths=[str(image_path)],
                    considered_count=1,
                    used_gpu=True,
                ),
            ) as mocked_select, patch(
                "src.app.web.routes._start_album_timelapse_job",
                return_value={"job_id": "timelapse_test_1", "status": "started", "status_url": "/api/admin/job/timelapse_test_1"},
            ) as mocked_timelapse_start, patch.object(JobManager, "run_job_async", _run_job_inline):
                response = client.post(
                    f"/api/persons/{person_id}/build-aging-album",
                    json={
                        "max_photos": 12,
                        "strict_gpu": True,
                        "quality_bias": 1.0,
                        "target_album_id": target_album_id,
                        "auto_start_timelapse": True,
                    },
                )

            self.assertEqual(response.status_code, 202)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertIn("album_id", payload)
            self.assertIn("status_url", payload)
            self.assertEqual(int(payload["album_id"]), target_album_id)
            self.assertTrue(str(payload.get("timelapse_status_url") or "").startswith("/api/admin/job/timelapse_album_"))

            mocked_select.assert_called_once()
            call_kwargs = mocked_select.call_args.kwargs
            self.assertEqual(call_kwargs["person_name"], "Marie Curie")
            self.assertEqual(call_kwargs["max_photos"], 12)
            self.assertTrue(call_kwargs["strict_gpu"])
            self.assertEqual(float(call_kwargs["quality_bias"]), 1.0)

            mocked_timelapse_start.assert_called_once()
            self.assertEqual(int(mocked_timelapse_start.call_args.kwargs["album_id"]), target_album_id)
            self.assertEqual(str(mocked_timelapse_start.call_args.kwargs["person_name"]), "Marie Curie")
            self.assertTrue(str(mocked_timelapse_start.call_args.kwargs["job_id"]).startswith("timelapse_album_"))

            with sqlite3.connect(db_path) as conn:
                count_row = conn.execute(
                    "SELECT COUNT(*) FROM album_photos WHERE album_id = ?",
                    (int(payload["album_id"]),),
                ).fetchone()
            self.assertIsNotNone(count_row)
            self.assertEqual(int(count_row[0]), 1)

            with sqlite3.connect(db_path) as conn:
                album_count_row = conn.execute("SELECT COUNT(*) FROM albums").fetchone()
            self.assertIsNotNone(album_count_row)
            self.assertEqual(int(album_count_row[0]), 1)

            missing_person_response = client.post("/api/persons/999999/build-aging-album", json={})
            self.assertEqual(missing_person_response.status_code, 404)

    def test_map_api_and_geocoding_routes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_path = photos_dir / "geo_sample.jpg"
            image = Image.new("RGB", (200, 120), color=(120, 120, 180))
            image.save(image_path)
            stat = image_path.stat()
            record = ImageRecord(
                path=image_path,
                size_bytes=stat.st_size,
                modified_ts=stat.st_mtime,
                taken_ts=stat.st_mtime,
                exif_data=ExifData(
                    taken_ts=stat.st_mtime,
                    latitude=48.1372,
                    longitude=11.5756,
                    camera_model="TestCam",
                ),
            )
            upsert_photo(
                db_path=db_path,
                record=record,
                labels=["urlaub", "stadt"],
                person_count=0,
            )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            map_response = client.get("/map?q=urlaub")
            self.assertEqual(map_response.status_code, 200)
            map_html = map_response.get_data(as_text=True)
            self.assertIn("Foto-Karte", map_html)
            self.assertIn("urlaub", map_html)

            photos_response = client.get("/api/photos-with-location?q=urlaub")
            self.assertEqual(photos_response.status_code, 200)
            photos_payload = photos_response.get_json()
            assert photos_payload is not None
            self.assertEqual(len(photos_payload["photos"]), 1)
            self.assertEqual(photos_payload["photos"][0]["camera"], "TestCam")
            self.assertIn("image_url", photos_payload["photos"][0])

            with patch("src.app.web.routes._geocode_place_cached", return_value=[{"display_name": "München", "lat": 48.1372, "lon": 11.5756}]):
                geocode_response = client.get("/api/geocode?q=München")
            self.assertEqual(geocode_response.status_code, 200)
            geocode_payload = geocode_response.get_json()
            assert geocode_payload is not None
            self.assertEqual(geocode_payload["results"][0]["display_name"], "München")

            with patch("src.app.web.routes._reverse_geocode_cached", return_value={"display_name": "Marienplatz", "lat": 48.1372, "lon": 11.5756}):
                reverse_response = client.get("/api/reverse-geocode?lat=48.1372&lon=11.5756")
            self.assertEqual(reverse_response.status_code, 200)
            reverse_payload = reverse_response.get_json()
            assert reverse_payload is not None
            self.assertEqual(reverse_payload["result"]["display_name"], "Marienplatz")

    def test_search_ui_uses_person_count_input_and_preserves_map_filters(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_path = photos_dir / "album_map_sample.jpg"
            Image.new("RGB", (200, 120), color=(100, 130, 180)).save(image_path)
            stat = image_path.stat()
            record = ImageRecord(
                path=image_path,
                size_bytes=stat.st_size,
                modified_ts=stat.st_mtime,
                exif_data=ExifData(latitude=48.1372, longitude=11.5756),
            )
            upsert_photo(
                db_path=db_path,
                record=record,
                labels=["urlaub"],
                person_count=2,
            )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            create_album_response = client.post(
                "/albums",
                data={"name": "Filter Album", "q": "urlaub", "per_page": 24, "person_count": 2},
            )
            self.assertEqual(create_album_response.status_code, 200)

            with sqlite3.connect(db_path) as conn:
                album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Filter Album",)).fetchone()
            self.assertIsNotNone(album_row)
            album_id = int(album_row[0])

            photo_token = base64.urlsafe_b64encode(str(image_path).encode("utf-8")).decode("ascii").rstrip("=")
            add_photo_response = client.post(
                f"/albums/{album_id}/add-photo",
                data={"photo_token": photo_token},
            )
            self.assertEqual(add_photo_response.status_code, 200)

            search_response = client.get(f"/?q=urlaub&album_id={album_id}&person_count=2")
            self.assertEqual(search_response.status_code, 200)
            search_html = search_response.get_data(as_text=True)
            self.assertNotIn("album-filter-select", search_html)
            self.assertNotIn("solo-toggle", search_html)
            self.assertIn('name="person_count"', search_html)
            self.assertIn('id="person-count-input"', search_html)
            self.assertIn(f'name="album_id" value="{album_id}"', search_html)
            self.assertIn("data-base-url=\"/map\"", search_html)
            self.assertIn(f"album_id={album_id}", search_html)
            self.assertIn("person_count=2", search_html)

            map_response = client.get(f"/map?q=urlaub&album_id={album_id}&person_count=2")
            self.assertEqual(map_response.status_code, 200)
            map_html = map_response.get_data(as_text=True)
            self.assertIn('q: "urlaub"', map_html)
            self.assertIn(f"album_id: {album_id}", map_html)
            self.assertIn("person_count: 2", map_html)
            self.assertIn(f'href="/?q=urlaub&amp;album_id={album_id}&amp;person_count=2"', map_html)

            legacy_map_response = client.get(f"/map?q=urlaub&album_id={album_id}&max_persons=2")
            self.assertEqual(legacy_map_response.status_code, 200)
            legacy_map_html = legacy_map_response.get_data(as_text=True)
            self.assertIn("person_count: 2", legacy_map_html)

    def test_person_count_filter_matches_exact_number_of_detected_people(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            for index, people in enumerate((1, 2, 3), start=1):
                image_path = photos_dir / f"people_{people}.jpg"
                Image.new("RGB", (220, 140), color=(90 + index * 10, 120, 170)).save(image_path)
                stat = image_path.stat()
                record = ImageRecord(
                    path=image_path,
                    size_bytes=stat.st_size,
                    modified_ts=stat.st_mtime + index,
                )
                upsert_photo(
                    db_path=db_path,
                    record=record,
                    labels=["gruppe"],
                    person_count=people,
                )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            response = client.get("/api/search?q=gruppe&person_count=2")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["person_count"], 2)
            self.assertIn("people_2.jpg", payload["items"][0]["path"])

            legacy_response = client.get("/api/search?q=gruppe&max_persons=2")
            self.assertEqual(legacy_response.status_code, 200)
            legacy_payload = legacy_response.get_json()
            assert legacy_payload is not None
            self.assertEqual(legacy_payload["total"], 1)
            self.assertIn("people_2.jpg", legacy_payload["items"][0]["path"])

    def test_person_count_filter_works_without_text_query(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            for people in (1, 2):
                image_path = photos_dir / f"blank_query_{people}.jpg"
                Image.new("RGB", (240, 160), color=(100 + people * 10, 120, 180)).save(image_path)
                stat = image_path.stat()
                record = ImageRecord(
                    path=image_path,
                    size_bytes=stat.st_size,
                    modified_ts=stat.st_mtime + people,
                )
                upsert_photo(
                    db_path=db_path,
                    record=record,
                    labels=["leer"],
                    person_count=people,
                )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            response = client.get("/api/search?person_count=2")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["person_count"], 2)
            self.assertIn("blank_query_2.jpg", payload["items"][0]["path"])

            page_response = client.get("/?person_count=2")
            self.assertEqual(page_response.status_code, 200)
            page_html = page_response.get_data(as_text=True)
            self.assertIn("blank_query_2.jpg", page_html)
            self.assertNotIn("Bitte Suchbegriff eingeben.", page_html)

    def test_api_search_supports_person_and_smile_filters(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_a = photos_dir / "person_smile_high.jpg"
            image_b = photos_dir / "person_smile_low.jpg"
            Image.new("RGB", (200, 120), color=(100, 120, 180)).save(image_a)
            Image.new("RGB", (200, 120), color=(110, 120, 170)).save(image_b)

            for image_path in (image_a, image_b):
                stat = image_path.stat()
                record = ImageRecord(
                    path=image_path,
                    size_bytes=stat.st_size,
                    modified_ts=stat.st_mtime,
                )
                upsert_photo(
                    db_path=db_path,
                    record=record,
                    labels=["urlaub"],
                    person_count=1,
                )

            with sqlite3.connect(db_path) as conn:
                conn.execute("INSERT INTO persons (name) VALUES (?)", ("Marie Curie",))
                person_id = int(conn.execute("SELECT id FROM persons WHERE name = ?", ("Marie Curie",)).fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO photo_person_matches (photo_path, person_id, score, smile_score, matched_ts)
                    VALUES (?, ?, ?, ?, strftime('%s','now'))
                    """,
                    (str(image_a), person_id, 0.92, 0.85),
                )
                conn.execute(
                    """
                    INSERT INTO photo_person_matches (photo_path, person_id, score, smile_score, matched_ts)
                    VALUES (?, ?, ?, ?, strftime('%s','now'))
                    """,
                    (str(image_b), person_id, 0.88, 0.20),
                )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            response = client.get('/api/search?q=person:"Marie Curie" smile:0.7')
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertEqual(payload["total"], 1)
            self.assertEqual(len(payload["items"]), 1)
            self.assertIn("person_smile_high.jpg", payload["items"][0]["path"])

            response_person_only = client.get('/api/search?q=person:"Marie Curie"')
            self.assertEqual(response_person_only.status_code, 200)
            payload_person_only = response_person_only.get_json()
            assert payload_person_only is not None
            self.assertEqual(payload_person_only["total"], 2)
            self.assertEqual(len(payload_person_only["items"]), 2)
            found_paths = {Path(item["path"]).name for item in payload_person_only["items"]}
            self.assertEqual(found_paths, {"person_smile_high.jpg", "person_smile_low.jpg"})

    def test_timelapse_rebuilds_existing_video_instead_of_using_cache(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            ensure_schema(db_path)

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            # Album anlegen, damit das Timelapse-Panel im Sidebar erscheint
            create_album_response = client.post(
                "/albums",
                data={"name": "Aging Test", "q": "", "per_page": 24},
            )
            self.assertEqual(create_album_response.status_code, 200)

            with sqlite3.connect(db_path) as conn:
                album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Aging Test",)).fetchone()
            self.assertIsNotNone(album_row)
            album_id = int(album_row[0])

            sidebar_response = client.get(f"/albums/sidebar?album_id={album_id}")
            self.assertEqual(sidebar_response.status_code, 200)
            sidebar_html = sidebar_response.get_data(as_text=True)
            self.assertIn("Aging-Timelapse", sidebar_html)
            self.assertIn("timelapse-start-btn", sidebar_html)
            self.assertIn("timelapse-ai-backend-input", sidebar_html)
            self.assertIn("timelapse-ai-hint", sidebar_html)

            # Validierung: person fehlt
            bad_response = client.post(
                f"/api/albums/{album_id}/timelapse",
                json={"fps": 24},
            )
            self.assertEqual(bad_response.status_code, 400)

            export_path = cache_dir / "exports" / f"album_{album_id}_album_{album_id}_marie_curie.mp4"
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_bytes(b"fake-mp4")

            def _run_job_inline(self, job_id, task_func):
                job = self.get_job(job_id)
                if not job:
                    return
                self.set_job_running(job_id)
                try:
                    task_func(job)
                    if job.should_abort():
                        self.set_job_aborted(job_id)
                    else:
                        self.set_job_completed(job_id)
                except Exception as exc:
                    self.set_job_failed(job_id, str(exc))

            def _fake_generate(db_path, album_id, person_name, output_path, config, progress_cb):
                self.assertFalse(output_path.exists(), "Alte MP4 sollte vor dem Rebuild gelöscht werden.")
                self.assertEqual(config.quality_profile, "max")
                self.assertEqual(config.interpolator, "flow")
                self.assertAlmostEqual(float(config.temporal_smooth), 0.3, places=3)
                self.assertAlmostEqual(float(config.detail_boost), 0.4, places=3)
                self.assertTrue(config.enhance_faces)
                self.assertEqual(config.ai_mode, "auto")
                self.assertEqual(config.ai_backend, "onnx")
                self.assertAlmostEqual(float(config.ai_strength), 0.7, places=3)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"new-mp4")
                if progress_cb:
                    progress_cb(1, 1, "fertig")
                return 3

            with patch("src.app.albums.timelapse.generate_aging_timelapse", side_effect=_fake_generate), patch.object(
                JobManager, "run_job_async", _run_job_inline
            ):
                start_response = client.post(
                    f"/api/albums/{album_id}/timelapse",
                    json={
                        "person": "Marie Curie",
                        "fps": 24,
                        "hold": 24,
                        "morph": 48,
                        "size": 512,
                        "quality": "max",
                        "interpolator": "flow",
                        "temporal_smooth": 0.3,
                        "detail_boost": 0.4,
                        "enhance_faces": True,
                        "ai_mode": "auto",
                        "ai_backend": "onnx",
                        "ai_strength": 0.7,
                    },
                )

            self.assertEqual(start_response.status_code, 202)
            start_payload = start_response.get_json()
            assert start_payload is not None
            self.assertEqual(start_payload["status"], "started")
            self.assertIn("/api/admin/job/", start_payload["status_url"])

            status_response = client.get(str(start_payload["status_url"]))
            self.assertEqual(status_response.status_code, 200)
            status_payload = status_response.get_json()
            assert status_payload is not None
            self.assertEqual(status_payload["status"], "completed")

            download_token = base64.urlsafe_b64encode(str(export_path).encode("utf-8")).decode("ascii").rstrip("=")
            download_response = client.get(f"/api/albums/timelapse/download/{download_token}")

            self.assertEqual(download_response.status_code, 200)
            self.assertEqual(download_response.mimetype, "video/mp4")
            self.assertEqual(download_response.data, b"new-mp4")

    def test_album_zip_export_endpoint_with_ratio_crop(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            db_path = workspace / "data" / "photo_index.db"
            cache_dir = workspace / "data" / "cache"
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ensure_schema(db_path)

            image_a = photos_dir / "landscape_a.jpg"
            image_b = photos_dir / "landscape_b.jpg"
            Image.new("RGB", (1200, 800), color=(90, 100, 180)).save(image_a)
            Image.new("RGB", (1000, 1000), color=(120, 110, 170)).save(image_b)

            for image_path in (image_a, image_b):
                stat = image_path.stat()
                record = ImageRecord(
                    path=image_path,
                    size_bytes=stat.st_size,
                    modified_ts=stat.st_mtime,
                )
                upsert_photo(
                    db_path=db_path,
                    record=record,
                    labels=["album", "export"],
                    person_count=1,
                )

            app = create_app(
                app_config=AppConfig.from_workspace(workspace_root=workspace),
                custom_db_path=str(db_path),
                custom_cache_dir=str(cache_dir),
            )
            client = app.test_client()

            create_album_response = client.post(
                "/albums",
                data={"name": "Export Test", "q": "", "per_page": 24},
            )
            self.assertEqual(create_album_response.status_code, 200)

            with sqlite3.connect(db_path) as conn:
                album_row = conn.execute("SELECT id FROM albums WHERE name = ?", ("Export Test",)).fetchone()
            self.assertIsNotNone(album_row)
            album_id = int(album_row[0])

            for photo_path in (image_a, image_b):
                token = base64.urlsafe_b64encode(str(photo_path).encode("utf-8")).decode("ascii").rstrip("=")
                add_response = client.post(
                    f"/albums/{album_id}/add-photo",
                    data={"photo_token": token},
                )
                self.assertEqual(add_response.status_code, 200)

            bad_ratio_response = client.post(
                f"/api/albums/{album_id}/export-zip",
                json={"ratio": "2:1"},
            )
            self.assertEqual(bad_ratio_response.status_code, 400)

            export_response = client.post(
                f"/api/albums/{album_id}/export-zip",
                json={"ratio": "16:9", "person": "Marie Curie"},
            )
            self.assertEqual(export_response.status_code, 200)
            export_payload = export_response.get_json()
            assert export_payload is not None
            self.assertTrue(export_payload["ok"])
            self.assertEqual(export_payload["count"], 2)

            download_response = client.get(str(export_payload["download_url"]))
            self.assertEqual(download_response.status_code, 200)
            self.assertEqual(download_response.mimetype, "application/zip")
            self.assertIsInstance(download_response.data, bytes)

            with zipfile.ZipFile(io.BytesIO(download_response.data), "r") as archive:
                names = archive.namelist()
                self.assertEqual(len(names), 2)
                for name in names:
                    with archive.open(name, "r") as image_file:
                        with Image.open(image_file) as img:
                            self.assertEqual(img.width * 9, img.height * 16)


if __name__ == "__main__":
    unittest.main()

