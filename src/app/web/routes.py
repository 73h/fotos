import base64
from functools import lru_cache
import json
import math
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from ..albums.store import add_photo_to_album, create_album, get_album, list_albums
from ..albums.store import (
    add_photos_to_album_batch,
    delete_album,
    remove_photo_from_album,
    rename_album,
    set_album_cover,
)
from ..index.store import parse_search_filters
from ..persons import list_persons
from ..search.query import run_search_page

from .thumbnails import ensure_thumbnail

web_blueprint = Blueprint("web", __name__)


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
    max_persons: int | None = None,
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

    if max_persons is not None:
        where_parts.append("person_count <= ?")
        params.append(max_persons)
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
    max_persons: int | None = None,
    album_id: int | None = None,
) -> dict[str, object]:
    db_path: Path = current_app.config["DB_PATH"]
    albums = list_albums(db_path=db_path) if db_path.exists() else []
    active_album = get_album(db_path=db_path, album_id=album_id) if album_id is not None and db_path.exists() else None
    return {
        "albums": [
            {
                "id": album.id,
                "name": album.name,
                "photo_count": album.photo_count,
                "active": album.id == album_id,
            }
            for album in albums
        ],
        "active_album_id": album_id,
        "active_album_name": active_album.name if active_album is not None else None,
        "query": query,
        "per_page": per_page,
        "max_persons": max_persons,
    }


def _build_page_payload(
    query: str,
    page: int,
    per_page: int,
    max_persons: int | None = None,
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

    if not query.strip() and album_id is None:
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
        max_persons=max_persons,
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
        "max_persons": max_persons,
        "active_album_id": album_id,
        "active_album_name": active_album.name if active_album is not None else None,
        "message": "Keine Treffer." if total == 0 else "",
    }


@web_blueprint.get("/")
def home():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    max_persons = request.args.get("max_persons", default=None, type=int)
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_page_payload(
        query=query,
        page=page,
        per_page=per_page,
        max_persons=max_persons,
        album_id=album_id,
    )
    payload.update(_build_album_payload(query=query, per_page=per_page, max_persons=max_persons, album_id=album_id))
    
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
    max_persons = request.args.get("max_persons", default=None, type=int)
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_page_payload(
        query=query,
        page=page,
        per_page=per_page,
        max_persons=max_persons,
        album_id=album_id,
    )
    payload.update(_build_album_payload(query=query, per_page=per_page, max_persons=max_persons, album_id=album_id))

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
    max_persons = request.args.get("max_persons", default=None, type=int)
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_page_payload(
        query=query,
        page=page,
        per_page=per_page,
        max_persons=max_persons,
        album_id=album_id,
    )
    return jsonify(payload)


@web_blueprint.get("/albums/sidebar")
def albums_sidebar():
    query = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", default=24, type=int)
    max_persons = request.args.get("max_persons", default=None, type=int)
    album_id = request.args.get("album_id", default=None, type=int)
    payload = _build_album_payload(query=query, per_page=per_page, max_persons=max_persons, album_id=album_id)
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
    max_persons = request.form.get("max_persons", default=None, type=int)
    album_id = request.form.get("album_id", default=None, type=int)
    payload = _build_album_payload(query=query, per_page=per_page, max_persons=max_persons, album_id=album_id)
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
    max_persons = request.args.get("max_persons", default=None, type=int)
    return render_template(
        "map.html",
        query=query,
        active_album_id=album_id,
        max_persons=max_persons,
    )


@web_blueprint.get("/api/photos-with-location")
def api_photos_with_location():
    """API-Endpoint für Fotos mit GPS-Daten."""
    db_path: Path = current_app.config["DB_PATH"]
    query = request.args.get("q", "").strip()
    max_persons = request.args.get("max_persons", default=None, type=int)
    album_id = request.args.get("album_id", default=None, type=int)
    limit = request.args.get("limit", default=20000, type=int)
    safe_limit = max(1, min(limit, 50000))

    if not db_path.exists():
        return jsonify({"photos": []})

    import sqlite3

    filter_sql, filter_params = _build_photo_filter_clause(
        query=query,
        max_persons=max_persons,
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

