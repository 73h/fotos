import sys
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.config import AppConfig  # noqa: E402
from src.app.index.store import ensure_schema, upsert_photo  # noqa: E402
from src.app.ingest import ExifData, ImageRecord  # noqa: E402
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

    def test_timelapse_ui_and_cached_download_endpoints(self) -> None:
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

            # Validierung: person fehlt
            bad_response = client.post(
                f"/api/albums/{album_id}/timelapse",
                json={"fps": 24},
            )
            self.assertEqual(bad_response.status_code, 400)

            # Simuliere bereits fertiges Video im Cache
            job_id = f"album_{album_id}_marie_curie"
            export_path = cache_dir / "exports" / f"{job_id}.mp4"
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_bytes(b"fake-mp4")

            start_response = client.post(
                f"/api/albums/{album_id}/timelapse",
                json={"person": "Marie Curie", "fps": 24, "hold": 24, "morph": 48, "size": 512},
            )
            self.assertEqual(start_response.status_code, 200)
            start_payload = start_response.get_json()
            assert start_payload is not None
            self.assertTrue(start_payload["ok"])
            self.assertIn(f"/api/albums/timelapse/download/{job_id}", start_payload["download_url"])

            status_response = client.get(f"/api/albums/timelapse/status/{job_id}")
            self.assertEqual(status_response.status_code, 200)
            status_payload = status_response.get_json()
            assert status_payload is not None
            self.assertEqual(status_payload["status"], "done")

            download_response = client.get(f"/api/albums/timelapse/download/{job_id}")
            self.assertEqual(download_response.status_code, 200)
            self.assertEqual(download_response.mimetype, "video/mp4")
            self.assertEqual(download_response.data, b"fake-mp4")


if __name__ == "__main__":
    unittest.main()

