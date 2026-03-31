import base64
import json
import math
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from ..albums.store import add_photo_to_album, create_album, get_album, list_albums
from ..albums.store import (
    add_photos_to_album_batch,
    delete_album,
    remove_photo_from_album,
    rename_album,
    set_album_cover,
)
from ..index.store import search_photos_by_location
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
    return render_template("map.html")


@web_blueprint.get("/api/photos-with-location")
def api_photos_with_location():
    """API-Endpoint für Fotos mit GPS-Daten."""
    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]
    thumb_size: int = int(current_app.config.get("THUMB_SIZE", 360))

    if not db_path.exists():
        return jsonify({"photos": []})

    # Hole alle Fotos mit GPS-Daten
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT path, exif_json, modified_ts
            FROM photos
            WHERE exif_json IS NOT NULL
            LIMIT 1000
            """
        ).fetchall()

    photos = []
    for row in rows:
        try:
            exif_data = json.loads(row[1])
            latitude = exif_data.get("latitude")
            longitude = exif_data.get("longitude")

            if latitude is None or longitude is None:
                continue

            photo_path = Path(row[0])
            token = _encode_path(row[0])

            photos.append({
                "path": row[0],
                "token": token,
                "latitude": latitude,
                "longitude": longitude,
                "thumb_url": f"/thumb/{token}",
                "modified_ts": row[2],
                "camera": exif_data.get("camera_model", "Unbekannt"),
            })
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return jsonify({"photos": photos})
