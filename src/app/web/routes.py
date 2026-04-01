import base64
from functools import lru_cache
import json
import math
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from ..config import AppConfig
from ..albums.store import add_photo_to_album, create_album, get_album, list_albums
from ..albums.store import (
    add_photos_to_album_batch,
    delete_album,
    duplicate_album,
    list_album_photo_paths,
    parse_reference_album_name,
    remove_photo_from_album,
    rename_album,
    set_album_cover,
)
from ..albums.export import export_album_zip, parse_ratio
from ..index.store import ensure_schema, get_admin_config, parse_search_filters, save_admin_config
from ..persons import list_persons
from ..persons.service import enroll_person_from_paths
from ..search.query import run_search_page

from .thumbnails import ensure_thumbnail

web_blueprint = Blueprint("web", __name__)

# ---------------------------------------------------------------------------
# Timelapse-Job-Verwaltung (einfacher In-Process-Status)
# ---------------------------------------------------------------------------

_timelapse_jobs: dict[str, dict] = {}
_timelapse_lock = threading.Lock()


def _make_job_id(album_id: int, person_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", person_name.lower().strip())
    return f"album_{album_id}_{slug}"


def _encode_path(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_path(token: str) -> Path:
    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode((token + padding).encode("ascii")).decode("utf-8")
    except Exception as error:
        raise ValueError("invalid token") from error
    return Path(decoded)


def _build_photo_filter_clause(
    query: str,
    person_count: int | None = None,
    album_id: int | None = None,
) -> tuple[str, list[object]]:
    terms, filters = parse_search_filters(query)
    where_parts = ["search_blob LIKE ?" for _ in terms]
    params: list[object] = [f"%{term}%" for term in terms]

    person_filter = filters["person"]
    smile_min = filters["smile_min"]

    if person_filter is not None and smile_min is not None:
        where_parts.append(
            """
            EXISTS (
                SELECT 1
                FROM photo_person_matches m
                JOIN persons p ON p.id = m.person_id
                WHERE m.photo_path = photos.path
                  AND lower(p.name) = lower(?)
                  AND m.smile_score IS NOT NULL
                  AND m.smile_score >= ?
            )
            """
        )
        params.extend([person_filter, smile_min])
    elif person_filter is not None:
        where_parts.append(
            """
            EXISTS (
                SELECT 1
                FROM photo_person_matches m
                JOIN persons p ON p.id = m.person_id
                WHERE m.photo_path = photos.path
                  AND lower(p.name) = lower(?)
            )
            """
        )
        params.append(person_filter)
    elif smile_min is not None:
        where_parts.append(
            """
            EXISTS (
                SELECT 1
                FROM photo_person_matches m
                WHERE m.photo_path = photos.path
                  AND m.smile_score IS NOT NULL
                  AND m.smile_score >= ?
            )
            """
        )
        params.append(smile_min)

    if person_count is not None:
        where_parts.append("person_count = ?")
        params.append(person_count)
    if album_id is not None:
        where_parts.append(
            "EXISTS (SELECT 1 FROM album_photos ap WHERE ap.photo_path = photos.path AND ap.album_id = ?)"
        )
        params.append(album_id)

    return (" AND ".join(where_parts) if where_parts else "1=1", params)


@lru_cache(maxsize=256)
def _geocode_place_cached(query: str) -> list[dict[str, object]]:
    if not query.strip():
        return []

    url = (
        "https://nominatim.openstreetmap.org/search"
        f"?q={quote_plus(query.strip())}&format=jsonv2&limit=5&addressdetails=1"
    )
    request_obj = Request(
        url,
        headers={
            "User-Agent": "fotos-app/1.0 (local photo search)",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request_obj, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    results: list[dict[str, object]] = []
    for item in payload:
        try:
            results.append(
                {
                    "display_name": str(item.get("display_name", "")),
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return results


@lru_cache(maxsize=512)
def _reverse_geocode_cached(latitude: float, longitude: float) -> dict[str, object] | None:
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={latitude:.6f}&lon={longitude:.6f}&format=jsonv2&zoom=14"
    )
    request_obj = Request(
        url,
        headers={
            "User-Agent": "fotos-app/1.0 (local photo search)",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request_obj, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    try:
        return {
            "display_name": str(payload.get("display_name", "")),
            "lat": float(payload["lat"]),
            "lon": float(payload["lon"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _build_album_payload(
    query: str,
    per_page: int,
    person_count: int | None = None,
    album_id: int | None = None,
) -> dict[str, object]:
    db_path: Path = current_app.config["DB_PATH"]
    albums = list_albums(db_path=db_path) if db_path.exists() else []
    active_album = get_album(db_path=db_path, album_id=album_id) if album_id is not None and db_path.exists() else None
    persons = list_persons(db_path=db_path) if db_path.exists() else []
    return {
        "albums": [
            {
                "id": album.id,
                "name": album.name,
                "photo_count": album.photo_count,
                "active": album.id == album_id,
                "reference_person_name": parse_reference_album_name(album.name),
                "is_reference": parse_reference_album_name(album.name) is not None,
            }
            for album in albums
        ],
        "active_album_id": album_id,
        "active_album_name": active_album.name if active_album is not None else None,
        "query": query,
        "per_page": per_page,
        "person_count": person_count,
        "persons": [
            {
                "id": person.id,
                "name": person.name,
                "photo_count": person.photo_count,
            }
            for person in persons
        ],
    }


def _build_page_payload(
    query: str,
    page: int,
    per_page: int,
    person_count: int | None = None,
    album_id: int | None = None,
) -> dict[str, object]:
    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]
    thumb_size: int = int(current_app.config.get("THUMB_SIZE", 360))

    if not db_path.exists():
        return {
            "items": [],
            "query": query,
            "page": 1,
            "per_page": per_page,
            "total": 0,
            "pages": 0,
            "has_prev": False,
            "has_next": False,
            "active_album_id": album_id,
            "active_album_name": None,
            "message": f"Index nicht gefunden: {db_path}",
        }

    active_album = get_album(db_path=db_path, album_id=album_id) if album_id is not None else None

    if not query.strip() and album_id is None and person_count is None:
        return {
            "items": [],
            "query": "",
            "page": 1,
            "per_page": per_page,
            "total": 0,
            "pages": 0,
            "has_prev": False,
            "has_next": False,
            "active_album_id": None,
            "active_album_name": None,
            "message": "Bitte Suchbegriff eingeben.",
        }

    safe_page = max(page, 1)
    safe_per_page = max(1, min(per_page, 200))
    offset = (safe_page - 1) * safe_per_page
    rows, total = run_search_page(
        db_path=db_path,
        query=query,
        limit=safe_per_page,
        offset=offset,
        person_count=person_count,
        album_id=album_id,
    )
    pages = math.ceil(total / safe_per_page) if total else 0

    items: list[dict[str, object]] = []
    for row in rows:
        token = _encode_path(row.path)
        image_path = Path(row.path)
        thumb_path = ensure_thumbnail(image_path=image_path, cache_root=cache_dir, size=thumb_size)
        items.append(
        {
            "path": row.path,
            "labels": row.labels,
            "modified_ts": row.modified_ts,
            "size_bytes": row.size_bytes,
            "duplicate_of_path": row.duplicate_of_path,
            "duplicate_kind": row.duplicate_kind,
            "duplicate_score": row.duplicate_score,
            "person_count": row.person_count,
            "token": token,
            "thumb_available": thumb_path is not None,
            "thumb_url": f"/thumb/{token}",
            "image_url": f"/photo/{token}",
        }
        )

    return {
        "items": items,
        "query": query,
        "page": safe_page,
        "per_page": safe_per_page,
        "total": total,
        "pages": pages,
        "has_prev": safe_page > 1,
        "has_next": safe_page < pages,
        "person_count": person_count,
        "active_album_id": album_id,
        "active_album_name": active_album.name if active_album is not None else None,
        "message": "Keine Treffer." if total == 0 else "",
    }


def _get_person_count_param() -> int | None:
    person_count = request.args.get("person_count", default=None, type=int)
    if person_count is None:
        person_count = request.args.get("max_persons", default=None, type=int)
    return person_count


def _get_person_count_form_param() -> int | None:
    person_count = request.form.get("person_count", default=None, type=int)
    if person_count is None:
        person_count = request.form.get("max_persons", default=None, type=int)
    return person_count


@web_blueprint.get("/")
def home():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    person_count = _get_person_count_param()
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_page_payload(
        query=query,
        page=page,
        per_page=per_page,
        person_count=person_count,
        album_id=album_id,
    )
    payload.update(_build_album_payload(query=query, per_page=per_page, person_count=person_count, album_id=album_id))

    # Füge Personen hinzu
    db_path: Path = current_app.config["DB_PATH"]
    persons = list_persons(db_path=db_path) if db_path.exists() else []
    payload["persons"] = [
        {
            "id": person.id,
            "name": person.name,
            "photo_count": person.photo_count,
        }
        for person in persons
    ]

    return render_template("search.html", **payload)


@web_blueprint.get("/search")
def search_partial():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    person_count = _get_person_count_param()
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_page_payload(
        query=query,
        page=page,
        per_page=per_page,
        person_count=person_count,
        album_id=album_id,
    )
    payload.update(_build_album_payload(query=query, per_page=per_page, person_count=person_count, album_id=album_id))

    # Füge Personen hinzu
    db_path: Path = current_app.config["DB_PATH"]
    persons = list_persons(db_path=db_path) if db_path.exists() else []
    payload["persons"] = [
        {
            "id": person.id,
            "name": person.name,
            "photo_count": person.photo_count,
        }
        for person in persons
    ]

    return render_template("_results.html", **payload)


@web_blueprint.get("/api/search")
def search_api():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    person_count = _get_person_count_param()
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_page_payload(
        query=query,
        page=page,
        per_page=per_page,
        person_count=person_count,
        album_id=album_id,
    )
    return jsonify(payload)


@web_blueprint.get("/albums/sidebar")
def albums_sidebar():
    query = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", default=24, type=int)
    person_count = _get_person_count_param()
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_album_payload(query=query, per_page=per_page, person_count=person_count, album_id=album_id)
    return render_template("_albums.html", **payload)


@web_blueprint.post("/albums")
def create_album_route():
    db_path: Path = current_app.config["DB_PATH"]
    name = request.form.get("name", "")
    try:
        if name.strip():
            create_album(db_path=db_path, name=name)
    except ValueError:
        pass

    query = request.form.get("q", "").strip()
    per_page = request.form.get("per_page", default=24, type=int)
    person_count = _get_person_count_form_param()
    album_id = request.form.get("album_id", default=None, type=int)
    payload = _build_album_payload(query=query, per_page=per_page, person_count=person_count, album_id=album_id)
    return render_template("_albums.html", **payload)


@web_blueprint.post("/albums/<int:album_id>/add-photo")
def add_photo_to_album_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    token = request.form.get("photo_token", "").strip()
    if not token:
        return jsonify({"error": "photo_token fehlt"}), 400

    try:
        photo_path = str(_decode_path(token))
        photo_count = add_photo_to_album(db_path=db_path, album_id=album_id, photo_path=photo_path)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album_id, "photo_count": photo_count})


@web_blueprint.put("/albums/<int:album_id>/rename")
def rename_album_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    new_name = request.form.get("name", "").strip()
    if not new_name:
        return jsonify({"error": "Album-Name erforderlich"}), 400

    try:
        album = rename_album(db_path=db_path, album_id=album_id, new_name=new_name)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album.id, "name": album.name})


@web_blueprint.post("/albums/<int:album_id>/duplicate")
def duplicate_album_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    try:
        album = duplicate_album(db_path=db_path, album_id=album_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album.id, "name": album.name, "photo_count": album.photo_count})


@web_blueprint.post("/albums/<int:album_id>/train-reference")
def train_reference_album_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    ensure_schema(db_path)

    album = get_album(db_path=db_path, album_id=album_id)
    if album is None:
        return jsonify({"error": "Album nicht gefunden."}), 404

    person_name = parse_reference_album_name(album.name)
    if person_name is None:
        return jsonify({"error": "Nur Alben mit Präfix 'Ref:' können als Personenreferenz angelernt werden."}), 400

    try:
        photo_paths = list_album_photo_paths(db_path=db_path, album_id=album_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    if not photo_paths:
        return jsonify({"error": "Ref-Album enthält keine Bilder."}), 400

    from .admin_jobs import get_job_manager

    job_manager = get_job_manager()
    job_id = f"train_ref_{album_id}_{uuid.uuid4().hex[:8]}"
    # Stufenmodell: Vorbereitung -> Embeddings/Training -> Persistenz
    job = job_manager.create_job(job_id, "train_reference_person", total=3)

    def _run_train_reference(progress_job):
        job_manager.update_progress(
            progress_job.job_id,
            1,
            progress_job.total,
            f"Vorbereitung abgeschlossen: {len(photo_paths)} Bilder im Ref-Album gefunden.",
        )

        job_manager.update_progress(
            progress_job.job_id,
            2,
            progress_job.total,
            f"Lerne Person '{person_name}' mit InsightFace an ({len(photo_paths)} Bilder)...",
        )

        result = enroll_person_from_paths(
            db_path=db_path,
            person_name=person_name,
            image_paths=photo_paths,
            preferred_backend="insightface",
            strict_backend=True,
        )

        job_manager.update_progress(
            progress_job.job_id,
            3,
            progress_job.total,
            "Persistiere neue Referenzen...",
        )

        progress_job.message = (
            f"Person '{result.person_name}' neu angelernt: "
            f"{result.sample_count} Samples aus {result.image_count} Bildern "
            f"(Backend: {result.backend})."
        )

    job_manager.run_job_async(job.job_id, _run_train_reference)

    return jsonify(
        {
            "ok": True,
            "album_id": album_id,
            "person_name": person_name,
            "job_id": job.job_id,
            "status": "started",
            "status_url": f"/api/admin/job/{job.job_id}",
        }
    ), 202


@web_blueprint.delete("/albums/<int:album_id>")
def delete_album_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    try:
        delete_album(db_path=db_path, album_id=album_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album_id})


@web_blueprint.post("/albums/<int:album_id>/set-cover")
def set_album_cover_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    token = request.form.get("photo_token", "").strip()
    if not token:
        return jsonify({"error": "photo_token fehlt"}), 400

    try:
        photo_path = str(_decode_path(token))
        set_album_cover(db_path=db_path, album_id=album_id, photo_path=photo_path)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album_id})


@web_blueprint.post("/albums/<int:album_id>/add-photos-batch")
def add_photos_batch_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    photo_tokens = request.form.getlist("photo_tokens[]")
    if not photo_tokens:
        return jsonify({"error": "photo_tokens leer"}), 400

    try:
        photo_paths = [str(_decode_path(token)) for token in photo_tokens]
        count = add_photos_to_album_batch(db_path=db_path, album_id=album_id, photo_paths=photo_paths)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album_id, "photo_count": count})


@web_blueprint.delete("/albums/<int:album_id>/remove-photo")
def remove_photo_from_album_route(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    token = request.form.get("photo_token", "").strip()
    if not token:
        return jsonify({"error": "photo_token fehlt"}), 400

    try:
        photo_path = str(_decode_path(token))
        remove_photo_from_album(db_path=db_path, album_id=album_id, photo_path=photo_path)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"ok": True, "album_id": album_id})


@web_blueprint.get("/photo/<token>")
def photo_file(token: str):
    try:
        path = _decode_path(token)
    except ValueError:
        abort(404)

    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(path)


@web_blueprint.get("/thumb/<token>")
def thumb_file(token: str):
    try:
        path = _decode_path(token)
    except ValueError:
        abort(404)

    if not path.exists() or not path.is_file():
        abort(404)

    cache_dir: Path = current_app.config["CACHE_DIR"]
    thumb_size: int = int(current_app.config.get("THUMB_SIZE", 360))
    thumb_path = ensure_thumbnail(image_path=path, cache_root=cache_dir, size=thumb_size)
    if thumb_path is None or not thumb_path.exists():
        return send_file(path)
    return send_file(thumb_path)


@web_blueprint.get("/map")
def map_view():
    """Zeigt eine Karte mit Foto-Positionen."""
    query = request.args.get("q", "").strip()
    album_id = request.args.get("album_id", default=None, type=int)
    person_count = _get_person_count_param()
    return render_template(
        "map.html",
        query=query,
        active_album_id=album_id,
        person_count=person_count,
    )


@web_blueprint.get("/api/photos-with-location")
def api_photos_with_location():
    """API-Endpoint für Fotos mit GPS-Daten."""
    db_path: Path = current_app.config["DB_PATH"]
    query = request.args.get("q", "").strip()
    person_count = _get_person_count_param()
    album_id = request.args.get("album_id", default=None, type=int)
    limit = request.args.get("limit", default=20000, type=int)
    safe_limit = max(1, min(limit, 50000))

    if not db_path.exists():
        return jsonify({"photos": []})

    import sqlite3

    filter_sql, filter_params = _build_photo_filter_clause(
        query=query,
        person_count=person_count,
        album_id=album_id,
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                path,
                COALESCE(taken_ts, modified_ts) AS sort_ts,
                json_extract(exif_json, '$.latitude') AS latitude,
                json_extract(exif_json, '$.longitude') AS longitude,
                COALESCE(json_extract(exif_json, '$.camera_model'), 'Unbekannt') AS camera
            FROM photos
            WHERE {filter_sql}
              AND json_extract(exif_json, '$.latitude') IS NOT NULL
              AND json_extract(exif_json, '$.longitude') IS NOT NULL
            ORDER BY COALESCE(taken_ts, modified_ts) DESC
            LIMIT ?
            """
            ,
            (*filter_params, safe_limit),
        ).fetchall()

    photos = []
    for row in rows:
        try:
            token = _encode_path(row[0])

            photos.append({
                "path": row[0],
                "token": token,
                "latitude": float(row[2]),
                "longitude": float(row[3]),
                "thumb_url": f"/thumb/{token}",
                "image_url": f"/photo/{token}",
                "captured_ts": float(row[1]) if row[1] is not None else None,
                "camera": row[4] or "Unbekannt",
            })
        except (TypeError, ValueError):
            continue

    return jsonify({"photos": photos})


@web_blueprint.get("/api/geocode")
def api_geocode():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"results": []})
    return jsonify({"results": _geocode_place_cached(query)})


@web_blueprint.get("/api/reverse-geocode")
def api_reverse_geocode():
    latitude = request.args.get("lat", type=float)
    longitude = request.args.get("lon", type=float)
    if latitude is None or longitude is None:
        return jsonify({"result": None}), 400
    return jsonify({"result": _reverse_geocode_cached(latitude, longitude)})


@web_blueprint.get("/api/photo-details/<token>")
def api_photo_details(token: str):
    """API-Endpoint für Foto-Details einschließlich EXIF-Daten."""
    import sqlite3
    from PIL import Image

    try:
        path = _decode_path(token)
    except ValueError:
        return jsonify({"error": "invalid token"}), 400

    if not path.exists() or not path.is_file():
        return jsonify({"error": "photo not found"}), 404

    db_path: Path = current_app.config["DB_PATH"]

    result = {
        "file_info": {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "modified_ts": path.stat().st_mtime,
        },
        "image_info": {},
        "labels": [],
        "exif": {},
    }

    # Get image info (dimensions, format)
    try:
        with Image.open(path) as img:
            result["image_info"]["width"] = img.width
            result["image_info"]["height"] = img.height
            result["image_info"]["format"] = img.format or "Unknown"
    except Exception:
        pass

    # Get labels and EXIF from database
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT labels_json, exif_json FROM photos WHERE path = ?",
                    (str(path),)
                ).fetchone()

                if row:
                    # Parse labels
                    try:
                        labels = json.loads(row["labels_json"] or "[]")
                        result["labels"] = labels if isinstance(labels, list) else []
                    except (json.JSONDecodeError, TypeError):
                        result["labels"] = []

                    # Parse EXIF data
                    try:
                        exif_data = json.loads(row["exif_json"] or "{}")
                        if isinstance(exif_data, dict):
                            # Map EXIF fields to friendly names
                            exif_mapping = {
                                "camera_make": "camera_make",
                                "camera_model": "camera_model",
                                "lens": "lens",
                                "focal_length": "focal_length",
                                "f_number": "f_number",
                                "exposure_time": "exposure_time",
                                "iso": "iso",
                                "datetime": "datetime",
                                "latitude": "latitude",
                                "longitude": "longitude",
                            }

                            for db_key, result_key in exif_mapping.items():
                                if db_key in exif_data:
                                    result["exif"][result_key] = exif_data[db_key]
                    except (json.JSONDecodeError, TypeError):
                        result["exif"] = {}
        except Exception:
            pass

    return jsonify(result)


@web_blueprint.post("/api/albums/<int:album_id>/export-zip")
def api_album_export_zip(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]

    if not db_path.exists():
        return jsonify({"error": "Index nicht gefunden"}), 404

    body = request.get_json(silent=True) or {}
    ratio = str(body.get("ratio", "1:1")).strip()
    person_name = str(body.get("person", "")).strip()

    try:
        parse_ratio(ratio)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    try:
        result = export_album_zip(
            db_path=db_path,
            cache_dir=cache_dir,
            album_id=album_id,
            ratio_text=ratio,
            person_name=person_name or None,
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    token = _encode_path(str(result.zip_path))
    return jsonify(
        {
            "ok": True,
            "count": result.exported_count,
            "download_url": f"/api/albums/export/download/{token}",
            "file_name": result.zip_path.name,
        }
    )


@web_blueprint.get("/api/albums/export/download/<token>")
def api_album_export_download(token: str):
    cache_dir: Path = current_app.config["CACHE_DIR"]
    exports_dir = (cache_dir / "exports").resolve()

    try:
        archive_path = _decode_path(token).resolve()
    except ValueError:
        abort(404)

    if exports_dir not in archive_path.parents:
        abort(404)
    if not archive_path.exists() or not archive_path.is_file():
        abort(404)

    return send_file(
        archive_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive_path.name,
    )


# ---------------------------------------------------------------------------
# Timelapse-Endpunkte
# ---------------------------------------------------------------------------

@web_blueprint.post("/api/albums/<int:album_id>/timelapse")
def api_album_timelapse(album_id: int):
    """
    Startet die Timelapse-Generierung im Hintergrund.

    JSON-Body (alle optional außer person):
      { "person": "Marie", "fps": 24, "hold": 24, "morph": 48, "size": 512 }

    Antwort:
      202 { "job_id": "...", "status_url": "..." }   – Generierung läuft
    """
    from ..albums.timelapse import TimelapseConfig, generate_aging_timelapse

    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]

    if not db_path.exists():
        return jsonify({"error": "Index nicht gefunden"}), 404

    body = request.get_json(silent=True) or {}
    person_name: str = str(body.get("person", "")).strip()
    if not person_name:
        return jsonify({"error": "Feld 'person' fehlt"}), 400

    fps   = int(body.get("fps",   24))
    hold  = int(body.get("hold",  24))
    morph = int(body.get("morph", 48))
    size  = int(body.get("size",  512))

    job_id = _make_job_id(album_id, person_name)
    exports_dir = cache_dir / "exports"
    output_path = exports_dir / f"{job_id}.mp4"

    with _timelapse_lock:
        job = _timelapse_jobs.get(job_id)
        if job and job["status"] == "running":
            return jsonify({"job_id": job_id, "status": "running",
                            "status_url": f"/api/albums/timelapse/status/{job_id}"}), 202
        if job and job["status"] in {"done", "error"}:
            _timelapse_jobs.pop(job_id, None)

    if output_path.exists():
        try:
            output_path.unlink()
        except OSError as error:
            return jsonify({"error": f"Vorhandenes Video konnte nicht gelöscht werden: {error}"}), 500

    # Neuen Job starten
    cfg = TimelapseConfig(fps=fps, hold_frames=hold, morph_frames=morph, output_size=size)

    with _timelapse_lock:
        _timelapse_jobs[job_id] = {"status": "running", "step": 0, "total": 0, "message": "Starte …"}

    def _run() -> None:
        try:
            def _cb(step: int, total: int, msg: str) -> None:
                with _timelapse_lock:
                    _timelapse_jobs[job_id].update({"step": step, "total": total, "message": msg})

            count = generate_aging_timelapse(
                db_path=db_path,
                album_id=album_id,
                person_name=person_name,
                output_path=output_path,
                config=cfg,
                progress_cb=_cb,
            )
            with _timelapse_lock:
                _timelapse_jobs[job_id].update({
                    "status": "done",
                    "count": count,
                    "message": f"✓ {count} Fotos verarbeitet",
                })
        except Exception as exc:
            with _timelapse_lock:
                _timelapse_jobs[job_id].update({"status": "error", "message": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "running",
                    "status_url": f"/api/albums/timelapse/status/{job_id}"}), 202


@web_blueprint.get("/api/albums/timelapse/status/<job_id>")
def api_timelapse_status(job_id: str):
    """Gibt den aktuellen Status eines Timelapse-Jobs zurück."""
    cache_dir: Path = current_app.config["CACHE_DIR"]
    output_path = cache_dir / "exports" / f"{job_id}.mp4"

    with _timelapse_lock:
        job = _timelapse_jobs.get(job_id)

    if job is None:
        if output_path.exists():
            return jsonify(
                {
                    "status": "done",
                    "step": 1,
                    "total": 1,
                    "message": "Video bereits vorhanden.",
                    "download_url": f"/api/albums/timelapse/download/{job_id}",
                }
            )
        return jsonify({"error": "Job nicht gefunden"}), 404

    response = dict(job)
    if job["status"] == "done":
        response["download_url"] = f"/api/albums/timelapse/download/{job_id}"
    return jsonify(response)


@web_blueprint.get("/api/albums/timelapse/download/<job_id>")
def api_timelapse_download(job_id: str):
    """Lädt das fertige Timelapse-Video herunter."""
    cache_dir: Path = current_app.config["CACHE_DIR"]
    output_path = cache_dir / "exports" / f"{job_id}.mp4"

    if not output_path.exists():
        return jsonify({"error": "Video nicht gefunden – zuerst generieren"}), 404

    return send_file(
        output_path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"{job_id}.mp4",
    )


# ===========================================================================
# Admin-Seite und Job-Management
# ===========================================================================


@web_blueprint.get("/admin")
def admin_page():
    """Admin-Dashboard für Index-Konfiguration und Job-Management."""
    return render_template("admin.html")


@web_blueprint.get("/api/admin/config")
def api_admin_get_config():
    """Lädt gespeicherte Admin-Konfiguration aus SQLite."""
    db_path: Path = current_app.config["DB_PATH"]
    ensure_schema(db_path)
    return jsonify(get_admin_config(db_path))


@web_blueprint.post("/api/admin/config")
def api_admin_save_config():
    """Speichert Admin-Konfiguration in SQLite."""
    db_path: Path = current_app.config["DB_PATH"]
    ensure_schema(db_path)
    payload = request.get_json() or {}
    return jsonify(save_admin_config(db_path, payload))


@web_blueprint.post("/api/admin/config/start-index")
def api_admin_start_index():
    """Startet Full-Index Job."""
    from .admin_jobs import get_job_manager
    from .admin_service import AdminService

    try:
        data = request.get_json() or {}
        photo_roots = data.get("photo_roots", [])
        
        if not photo_roots:
            return jsonify({"error": "Keine Foto-Pfade angegeben"}), 400

        # Validiere Pfade
        for root in photo_roots:
            p = Path(root)
            if not p.exists():
                return jsonify({"error": f"Pfad existiert nicht: {root}"}), 400

        app_config: AppConfig = current_app.config.get("APP_CONFIG")
        db_path: Path = current_app.config["DB_PATH"]
        ensure_schema(db_path)
        save_admin_config(
            db_path,
            {
                "photo_roots": photo_roots,
                "person_backend": data.get("person_backend"),
                "force_reindex": data.get("force_reindex", False),
                "index_workers": data.get("index_workers", 1),
                "near_duplicates": data.get("near_duplicates", False),
                "phash_threshold": data.get("phash_threshold", 6),
            },
        )

        job_manager = get_job_manager()
        admin_service = AdminService(app_config, job_manager)

        job_id = admin_service.start_full_index(
            photo_roots=photo_roots,
            person_backend=data.get("person_backend"),
            force_reindex=data.get("force_reindex", False),
            index_workers=data.get("index_workers", 1),
            near_duplicates=data.get("near_duplicates", False),
            phash_threshold=data.get("phash_threshold", 6),
        )

        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@web_blueprint.post("/api/admin/config/start-exif")
def api_admin_start_exif():
    """Startet EXIF-Update Job."""
    from .admin_jobs import get_job_manager
    from .admin_service import AdminService

    try:
        app_config: AppConfig = current_app.config.get("APP_CONFIG")
        job_manager = get_job_manager()
        admin_service = AdminService(app_config, job_manager)

        job_id = admin_service.start_exif_update()
        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@web_blueprint.post("/api/admin/config/start-rematch")
def api_admin_start_rematch():
    """Startet Rematch-Persons Job."""
    from .admin_jobs import get_job_manager
    from .admin_service import AdminService

    try:
        data = request.get_json() or {}
        app_config: AppConfig = current_app.config.get("APP_CONFIG")
        db_path: Path = current_app.config["DB_PATH"]
        ensure_schema(db_path)
        save_admin_config(
            db_path,
            {
                "person_backend": data.get("person_backend"),
                "rematch_workers": data.get("workers", 1),
            },
        )
        job_manager = get_job_manager()
        admin_service = AdminService(app_config, job_manager)

        job_id = admin_service.start_rematch_persons(
            person_backend=data.get("person_backend"),
            workers=data.get("workers", 1),
        )
        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@web_blueprint.get("/api/admin/job/<job_id>")
def api_admin_job_status(job_id: str):
    """Gibt den Status eines Jobs zurück."""
    from .admin_jobs import get_job_manager

    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)

    if job is None:
        return jsonify({"error": "Job nicht gefunden"}), 404

    return jsonify(job.to_dict())


@web_blueprint.post("/api/admin/job/<job_id>/abort")
def api_admin_job_abort(job_id: str):
    """Fordert Abbruch eines Jobs an."""
    from .admin_jobs import get_job_manager

    job_manager = get_job_manager()
    if job_manager.request_abort(job_id):
        return jsonify({"status": "abort_requested"})
    else:
        return jsonify({"error": "Job kann nicht abgebrochen werden"}), 400


@web_blueprint.get("/api/admin/jobs")
def api_admin_jobs_list():
    """Gibt Liste aller Jobs zurück."""
    from .admin_jobs import get_job_manager

    job_manager = get_job_manager()
    # Cleanup alte Jobs
    job_manager.cleanup_old_jobs()
    jobs = job_manager.get_all_jobs()
    return jsonify([job.to_dict() for job in jobs])

