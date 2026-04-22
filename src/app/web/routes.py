import base64
from functools import lru_cache
import json
import math
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any
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
from ..albums.export import export_album_zip, is_original_export_format, parse_ratio

from ..index.store import ADMIN_REMATCH_ORDER_MODES, ensure_schema, get_admin_config, parse_search_filters, save_admin_config, update_person_labels
from ..persons import list_persons
from ..persons.ranking import select_aging_timelapse_photo_paths
from ..persons.embeddings import cosine_similarity
from ..persons.service import enroll_person_from_paths, extract_person_signatures, match_persons_for_photo, persist_matches_for_photo
from ..search.query import run_search_page

from .thumbnails import ensure_thumbnail

web_blueprint = Blueprint("web", __name__)

# ...existing code...


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


def _build_timelapse_ai_context() -> dict[str, object]:
    db_path: Path = current_app.config["DB_PATH"]
    config = get_admin_config(db_path)

    onnx_model_text = str(config.get("timelapse_face_onnx_model", "") or "").strip()
    superres_model_text = str(config.get("timelapse_superres_model", "") or "").strip()

    onnx_model_ok = bool(onnx_model_text and Path(onnx_model_text).is_file())
    superres_model_ok = bool(superres_model_text and Path(superres_model_text).is_file())

    onnx_runtime_ok = False
    try:
        import onnxruntime  # noqa: F401
        onnx_runtime_ok = True
    except Exception:
        onnx_runtime_ok = False

    onnx_available = onnx_model_ok and onnx_runtime_ok
    superres_available = superres_model_ok

    configured_backend = str(config.get("timelapse_ai_backend", "auto") or "auto").strip().lower() or "auto"
    if configured_backend not in {"auto", "local", "onnx", "superres"}:
        configured_backend = "auto"

    default_backend = configured_backend
    if configured_backend == "onnx" and not onnx_available:
        default_backend = "superres" if superres_available else "auto"
    elif configured_backend == "superres" and not superres_available:
        default_backend = "onnx" if onnx_available else "auto"

    if onnx_available:
        hint = "ONNX-Backend ist verfuegbar."
    elif onnx_model_ok and not onnx_runtime_ok:
        hint = "ONNX-Modell gefunden, aber onnxruntime fehlt."
    elif not onnx_model_ok:
        hint = "ONNX-Modell nicht in Admin-Konfiguration gesetzt."
    else:
        hint = "Lokales Backend aktiv."

    return {
        "onnx_available": onnx_available,
        "superres_available": superres_available,
        "default_backend": default_backend,
        "hint": hint,
    }


def _build_photo_filter_clause(
    query: str,
    person_count: int | None = None,
    album_id: int | None = None,
) -> tuple[str, list[object]]:
    terms, filters = parse_search_filters(query)
    where_parts = ["search_blob LIKE ?" for _ in terms]
    params: list[object] = [f"%{term}%" for term in terms]

    persons_filter = filters["persons"]
    smile_min = filters["smile_min"]
    person_unknown = filters["person_unknown"]

    # Pro Person ein eigenes EXISTS (UND-Verknuepfung)
    for person_name in persons_filter:
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
        params.append(person_name)

    # smile_min als separater globaler Filter
    if smile_min is not None:
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

    if person_unknown:
        where_parts.append(
            """
            photos.person_count > 0
            AND photos.person_count > (
                SELECT COUNT(*)
                FROM photo_person_matches m
                WHERE m.photo_path = photos.path
            )
            """
        )

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
        "timelapse_ai": _build_timelapse_ai_context(),
    }


def _person_name_by_id(db_path: Path, person_id: int) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT name FROM persons WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        return None
    return str(row[0])


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

    # Lade InsightFace-Einstellungen aus der Datenbank
    from ..persons.embeddings import initialize_insightface_settings
    initialize_insightface_settings(db_path)

    album = get_album(db_path=db_path, album_id=album_id)
    if album is None:
        return jsonify({"error": "Album nicht gefunden."}), 404

    person_name = parse_reference_album_name(album.name)
    if person_name is None:
        return jsonify({"error": "Nur Alben mit Präfix 'Ref:' können als Personenreferenz angelernt werden."}), 400
    person_name_str: str = str(person_name)

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
        # Lade InsightFace-Einstellungen AUCH hier (im Worker-Thread!)
        from ..persons.embeddings import initialize_insightface_settings
        initialize_insightface_settings(db_path)

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
            f"Lerne Person '{person_name_str}' mit InsightFace an ({len(photo_paths)} Bilder)...",
        )

        result = enroll_person_from_paths(
            db_path=db_path,
            person_name=person_name_str,
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
            "person_name": person_name_str,
            "job_id": job.job_id,
            "status": "started",
            "status_url": f"/api/admin/job/{job.job_id}",
        }
    ), 202


@web_blueprint.post("/api/persons/<int:person_id>/build-aging-album")
def api_build_aging_album_for_person(person_id: int):
    from ..persons.embeddings import initialize_insightface_settings
    from .admin_jobs import get_job_manager

    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]
    if not db_path.exists():
        return jsonify({"error": "Index nicht gefunden"}), 404

    person_name = _person_name_by_id(db_path, person_id)
    if not person_name:
        return jsonify({"error": "Person nicht gefunden"}), 404
    person_name_str: str = str(person_name)

    body = request.get_json(silent=True) or {}
    max_photos = int(body.get("max_photos", 60))
    strict_gpu = bool(body.get("strict_gpu", False))
    quality_bias_raw = body.get("quality_bias", 0.5)
    auto_start_timelapse = bool(body.get("auto_start_timelapse", False))

    try:
        quality_bias = float(quality_bias_raw)
    except (TypeError, ValueError):
        quality_bias = 0.5
    quality_bias = max(0.0, min(1.0, quality_bias))

    target_album_id_raw = body.get("target_album_id")
    target_album_id: int | None = None
    if target_album_id_raw not in (None, ""):
        try:
            target_album_id = int(str(target_album_id_raw))
        except (TypeError, ValueError):
            return jsonify({"error": "target_album_id ist ungueltig"}), 400

    safe_max_photos = max(2, min(max_photos, 300))
    if target_album_id is not None:
        album = get_album(db_path=db_path, album_id=target_album_id)
        if album is None:
            return jsonify({"error": "Zielalbum nicht gefunden"}), 404
    else:
        album_name = f"Aging: {person_name_str} ({uuid.uuid4().hex[:6]})"
        album = create_album(db_path=db_path, name=album_name)

    timelapse_job_id = f"timelapse_album_{album.id}_{uuid.uuid4().hex[:8]}" if auto_start_timelapse else ""

    job_manager = get_job_manager()
    job_id = f"aging_album_{person_id}_{uuid.uuid4().hex[:8]}"
    job = job_manager.create_job(job_id, "build_aging_album", total=4)

    def _run_build_aging_album(progress_job):
        initialize_insightface_settings(db_path)
        job_manager.update_progress(
            progress_job.job_id,
            1,
            progress_job.total,
            f"Starte Auswahl fuer '{person_name_str}' ...",
        )

        selection = select_aging_timelapse_photo_paths(
            db_path=db_path,
            person_name=person_name_str,
            max_photos=safe_max_photos,
            prefer_gpu=True,
            strict_gpu=strict_gpu,
            quality_bias=quality_bias,
        )

        if not selection.photo_paths:
            raise ValueError("Keine geeigneten Bilder fuer Aging-Timelapse gefunden.")

        job_manager.update_progress(
            progress_job.job_id,
            2,
            progress_job.total,
            f"{len(selection.photo_paths)} Bilder ausgewaehlt, befuelle Album ...",
        )

        added_count = 0
        for path in selection.photo_paths:
            added_count += add_photo_to_album(db_path=db_path, album_id=album.id, photo_path=path)

        job_manager.update_progress(
            progress_job.job_id,
            3,
            progress_job.total,
            "Album gespeichert.",
        )

        timelapse_note = ""
        if auto_start_timelapse:
            tmeta = _start_album_timelapse_job(
                db_path=db_path,
                cache_dir=cache_dir,
                album_id=album.id,
                person_name=person_name_str,
                body={
                    "quality": "balanced",
                    "interpolator": "auto",
                    "enhance_faces": True,
                    "ai_mode": "auto",
                },
                job_id=timelapse_job_id,
            )
            timelapse_note = f" Timelapse-Job gestartet: {tmeta['job_id']}."

        backend_hint = "GPU" if selection.used_gpu else "CPU/Fallback"
        progress_job.message = (
            f"Aging-Album erstellt: '{album.name}' mit {added_count} Bildern "
            f"(Kandidaten: {selection.considered_count}, Backend: {backend_hint})."
            f"{timelapse_note}"
        )

    job_manager.run_job_async(job.job_id, _run_build_aging_album)

    return jsonify(
        {
            "ok": True,
            "status": "started",
            "job_id": job.job_id,
            "status_url": f"/api/admin/job/{job.job_id}",
            "album_id": album.id,
            "album_name": album.name,
            "album_url": f"/?album_id={album.id}",
            "timelapse_status_url": f"/api/admin/job/{timelapse_job_id}" if timelapse_job_id else None,
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
        "elements": {
            "objects": [],
            "animals": [],
            "persons": [],
        },
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
                    "SELECT labels_json, exif_json, taken_ts FROM photos WHERE path = ?",
                    (str(path),),
                ).fetchone()

                if row:
                    # Add taken_ts to file_info if available
                    if row["taken_ts"] is not None:
                        result["file_info"]["taken_ts"] = float(row["taken_ts"])
                    
                    # Parse labels
                    try:
                        labels = json.loads(row["labels_json"] or "[]")
                        result["labels"] = labels if isinstance(labels, list) else []
                    except (json.JSONDecodeError, TypeError):
                        result["labels"] = []

                    if "object" in result["labels"]:
                        result["elements"]["objects"].append("Objekt erkannt")
                    if "animal" in result["labels"]:
                        result["elements"]["animals"].append("Tier erkannt")

                    # Parse EXIF data
                    try:
                        exif_data = json.loads(row["exif_json"] or "{}")
                        if isinstance(exif_data, dict):
                            result["exif"] = exif_data
                    except (json.JSONDecodeError, TypeError):
                        result["exif"] = {}

                person_rows = conn.execute(
                    """
                    SELECT p.id, p.name, m.score, m.smile_score
                    FROM photo_person_matches m
                    JOIN persons p ON p.id = m.person_id
                    WHERE m.photo_path = ?
                    ORDER BY m.score DESC, lower(p.name) ASC
                    """,
                    (str(path),),
                ).fetchall()
                for person_row in person_rows:
                    result["elements"]["persons"].append(
                        {
                            "person_id": int(person_row["id"]),
                            "name": str(person_row["name"]),
                            "score": float(person_row["score"]),
                            "smile_score": float(person_row["smile_score"]) if person_row["smile_score"] is not None else None,
                        }
                    )
        except Exception:
            pass

    return jsonify(result)


@web_blueprint.post("/api/photo-details/<token>/persons/<int:person_id>/remove")
def api_remove_photo_person_match(token: str, person_id: int):
    try:
        path = _decode_path(token)
    except ValueError:
        return jsonify({"error": "invalid token"}), 400

    db_path: Path = current_app.config["DB_PATH"]
    if not db_path.exists():
        return jsonify({"error": "index not found"}), 404

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            """
            SELECT p.name
            FROM photo_person_matches m
            JOIN persons p ON p.id = m.person_id
            WHERE m.photo_path = ? AND m.person_id = ?
            """,
            (str(path), person_id),
        ).fetchone()
        if existing is None:
            return jsonify({"error": "person mark not found"}), 404

        conn.execute(
            "DELETE FROM photo_person_matches WHERE photo_path = ? AND person_id = ?",
            (str(path), person_id),
        )
        remaining_row = conn.execute(
            "SELECT COUNT(*) FROM photo_person_matches WHERE photo_path = ?",
            (str(path),),
        ).fetchone()
        remaining_count = int(remaining_row[0]) if remaining_row is not None else 0
        conn.execute(
            "UPDATE photos SET person_count = ? WHERE path = ?",
            (remaining_count, str(path)),
        )

    return jsonify(
        {
            "ok": True,
            "person_id": person_id,
            "person_name": str(existing["name"]),
            "remaining_person_count": remaining_count,
        }
    )


@web_blueprint.post("/api/photo-details/<token>/persons/rematch")
def api_rematch_photo_persons(token: str):
    try:
        path = _decode_path(token)
    except ValueError:
        return jsonify({"error": "invalid token"}), 400

    if not path.exists() or not path.is_file():
        return jsonify({"error": "photo not found"}), 404

    db_path: Path = current_app.config["DB_PATH"]
    if not db_path.exists():
        return jsonify({"error": "index not found"}), 404

    try:
        from ..detectors.labels import initialize_yolo_settings
        from ..persons.embeddings import initialize_insightface_settings
        from ..persons.service import initialize_person_settings

        initialize_yolo_settings(db_path)
        initialize_person_settings(db_path)
        initialize_insightface_settings(db_path)

        admin_config = get_admin_config(db_path)
        preferred_backend = str(admin_config.get("person_backend") or "insightface").strip() or None

        matches, person_count = match_persons_for_photo(
            db_path=db_path,
            photo_path=path,
            preferred_backend=preferred_backend,
        )
        persist_matches_for_photo(db_path=db_path, photo_path=path, matches=matches)
        update_person_labels(
            db_path=db_path,
            photo_path=str(path),
            person_matches=matches,
            person_count=person_count,
        )
    except Exception as error:
        return jsonify({"error": f"person rematch failed: {error}"}), 500

    return jsonify(
        {
            "ok": True,
            "person_count": person_count,
            "match_count": len(matches),
            "persons": [
                {
                    "person_id": match.person_id,
                    "person_name": match.person_name,
                    "score": float(match.score),
                    "smile_score": float(match.smile_score) if match.smile_score is not None else None,
                }
                for match in matches
            ],
        }
    )


@web_blueprint.get("/api/photo-details/<token>/persons/<int:person_id>/best-ref")
def api_photo_person_best_ref(token: str, person_id: int):
    """Gibt das Referenzfoto zurück, das den höchsten Match-Score für diese Person erzeugt hat."""
    try:
        path = _decode_path(token)
    except ValueError:
        return jsonify({"error": "invalid token"}), 400

    if not path.exists() or not path.is_file():
        return jsonify({"error": "photo not found"}), 404

    db_path: Path = current_app.config["DB_PATH"]
    if not db_path.exists():
        return jsonify({"error": "index not found"}), 404

    try:
        import json as _json
        from ..persons.embeddings import initialize_insightface_settings
        from ..persons.service import initialize_person_settings

        initialize_person_settings(db_path)
        initialize_insightface_settings(db_path)

        admin_config = get_admin_config(db_path)
        preferred_backend = str(admin_config.get("person_backend") or "insightface").strip() or None

        backend_name, signatures, _ = extract_person_signatures(path, preferred_backend=preferred_backend)
        if not signatures:
            return jsonify({"error": "no faces detected in photo"}), 404

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            person_row = conn.execute("SELECT name FROM persons WHERE id = ?", (person_id,)).fetchone()
            if person_row is None:
                return jsonify({"error": "person not found"}), 404

            refs = conn.execute(
                "SELECT source_path, vector_json FROM person_refs WHERE person_id = ? AND lower(backend) = lower(?)",
                (person_id, backend_name),
            ).fetchall()

        if not refs:
            return jsonify({"error": "no references for this person and backend"}), 404

        best_score = -1.0
        best_source_path: str | None = None
        for ref in refs:
            vector = [float(v) for v in _json.loads(ref["vector_json"])]
            for sig, _ in signatures:
                score = cosine_similarity(sig, vector)
                if score > best_score:
                    best_score = score
                    best_source_path = ref["source_path"]

        if best_source_path is None:
            return jsonify({"error": "could not determine best reference"}), 404

        source_token = _encode_path(best_source_path)
        return jsonify(
            {
                "source_path": best_source_path,
                "source_filename": Path(best_source_path).name,
                "source_token": source_token,
                "score": float(best_score),
            }
        )
    except Exception as error:
        return jsonify({"error": f"best-ref lookup failed: {error}"}), 500


@web_blueprint.post("/api/albums/<int:album_id>/export-zip")
def api_album_export_zip(album_id: int):
    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]

    if not db_path.exists():
        return jsonify({"error": "Index nicht gefunden"}), 404

    body = request.get_json(silent=True) or {}
    ratio = str(body.get("ratio", "1:1")).strip()
    person_name = str(body.get("person", "")).strip()
    add_metadata_overlay = bool(body.get("add_metadata_overlay", False))
    metadata_overlay_exact_5pct = bool(body.get("metadata_overlay_exact_5pct", True))
    metadata_include_date = bool(body.get("metadata_include_date", True))
    metadata_include_place = bool(body.get("metadata_include_place", True))

    try:
        if not is_original_export_format(ratio):
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
            add_metadata_overlay=add_metadata_overlay,
            metadata_overlay_exact_5pct=metadata_overlay_exact_5pct,
            metadata_include_date=metadata_include_date,
            metadata_include_place=metadata_include_place,
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


def _start_album_timelapse_job(
    db_path: Path,
    cache_dir: Path,
    album_id: int,
    person_name: str,
    body: dict[str, Any],
    job_id: str | None = None,
) -> dict[str, str]:
    from ..albums.timelapse import TimelapseConfig, generate_aging_timelapse
    from ..persons.embeddings import initialize_insightface_settings
    from .admin_jobs import get_job_manager

    person_name = str(person_name).strip()
    if not person_name:
        raise ValueError("Feld 'person' fehlt")

    admin_config = get_admin_config(db_path)

    fps = int(body.get("fps", 24))
    hold = int(body.get("hold", 24))
    morph = int(body.get("morph", 48))
    size = int(body.get("size", 512))
    quality = str(body.get("quality", "compat")).strip().lower() or "compat"
    interpolator = str(body.get("interpolator", "morph")).strip().lower() or "morph"
    temporal_smooth = float(body.get("temporal_smooth", 0.0))
    detail_boost = float(body.get("detail_boost", 0.0))
    enhance_faces = bool(body.get("enhance_faces", False))
    ai_mode = str(body.get("ai_mode", "off")).strip().lower() or "off"
    ai_backend = str(body.get("ai_backend", admin_config.get("timelapse_ai_backend", "auto"))).strip().lower() or "auto"
    ai_strength = float(body.get("ai_strength", 0.5))

    safe_job_id = str(job_id or f"timelapse_album_{album_id}_{uuid.uuid4().hex[:8]}")
    exports_dir = cache_dir / "exports"
    output_path = exports_dir / f"album_{album_id}_{_make_job_id(album_id, person_name)}.mp4"

    initialize_insightface_settings(db_path)

    cfg = TimelapseConfig(
        fps=fps,
        hold_frames=hold,
        morph_frames=morph,
        output_size=size,
        quality_profile=quality,
        interpolator=interpolator,
        temporal_smooth=temporal_smooth,
        detail_boost=detail_boost,
        enhance_faces=enhance_faces,
        ai_mode=ai_mode,
        ai_backend=ai_backend,
        ai_strength=ai_strength,
    )

    job_manager = get_job_manager()
    job = job_manager.create_job(str(safe_job_id), "timelapse", total=100)

    def _run_timelapse(progress_job) -> None:
        from ..persons.embeddings import initialize_insightface_settings

        initialize_insightface_settings(db_path)

        try:
            job_manager.update_progress(
                progress_job.job_id,
                1,
                progress_job.total,
                f"Starte Timelapse-Generierung fuer Person '{person_name}'...",
            )

            def _cb(step: int, total: int, msg: str) -> None:
                pct = int(max(1, min(99, step * 99 // max(1, total))))
                job_manager.update_progress(progress_job.job_id, pct, 100, msg)

            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass

            count = generate_aging_timelapse(
                db_path=db_path,
                album_id=album_id,
                person_name=person_name,
                output_path=output_path,
                config=cfg,
                progress_cb=_cb,
            )

            msg = f"✓ Fertig – Timelapse mit {count} Fotos erstellt"
            job_manager.set_job_completed(progress_job.job_id, msg)
            progress_job.message = msg
        except Exception as exc:
            job_manager.set_job_failed(progress_job.job_id, str(exc))

    job_manager.run_job_async(str(job.job_id), _run_timelapse)
    return {
        "job_id": safe_job_id,
        "status": "started",
        "status_url": f"/api/admin/job/{safe_job_id}",
    }

@web_blueprint.post("/api/albums/<int:album_id>/timelapse")
def api_album_timelapse(album_id: int):
    """
    Startet die Timelapse-Generierung im Hintergrund über JobManager.

    JSON-Body (alle optional außer person):
      { "person": "Marie", "fps": 24, "hold": 24, "morph": 48, "size": 512 }

    Antwort:
      202 { "job_id": "...", "status_url": "..." }   – Generierung läuft
    """
    db_path: Path = current_app.config["DB_PATH"]
    cache_dir: Path = current_app.config["CACHE_DIR"]

    if not db_path.exists():
        return jsonify({"error": "Index nicht gefunden"}), 404

    body = request.get_json(silent=True) or {}
    person_name: str = str(body.get("person", "")).strip()
    if not person_name:
        return jsonify({"error": "Feld 'person' fehlt"}), 400
    try:
        payload = _start_album_timelapse_job(
            db_path=db_path,
            cache_dir=cache_dir,
            album_id=album_id,
            person_name=person_name,
            body=body,
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify(payload), 202


@web_blueprint.get("/api/albums/timelapse/status/<job_id>")
def api_timelapse_status(job_id: str):
    """
    Gibt den Status eines Timelapse-Jobs zurück.
    Leitet zum JobManager-Endpunkt um für konsistentes Job-Tracking.
    """
    from .admin_jobs import get_job_manager

    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)

    if job is None:
        return jsonify({"error": "Job nicht gefunden"}), 404

    response = job.to_dict()

    # Wenn fertig, füge Download-Link hinzu
    if job.status.value == "completed":
        cache_dir: Path = current_app.config["CACHE_DIR"]
        exports_dir = cache_dir / "exports"
        # Suche nach dem Video-File basierend auf job_id
        for video_file in exports_dir.glob("album_*.mp4"):
            if job_id in str(video_file) or video_file.name.startswith(job_id):
                response["download_url"] = f"/api/albums/timelapse/download/{_encode_path(str(video_file))}"
                break

    return jsonify(response)


@web_blueprint.get("/api/albums/timelapse/download/<job_id>")
def api_timelapse_download(job_id: str):
    """Lädt das fertige Timelapse-Video herunter."""
    cache_dir: Path = current_app.config["CACHE_DIR"]
    exports_dir = cache_dir / "exports"
    
    # Versuche, das Video zu finden
    # Erst probieren wir den direkten Pfad (falls job_id ein encoded path ist)
    try:
        output_path = _decode_path(job_id).resolve()
    except (ValueError, AttributeError):
        # Fallback: Suche nach einem Video, das job_id im Namen hat
        output_path = None
        for video_file in exports_dir.glob("album_*.mp4"):
            if job_id in str(video_file) or video_file.name.startswith(job_id):
                output_path = video_file
                break
        
        if output_path is None:
            return jsonify({"error": "Video nicht gefunden"}), 404

    # Validiere, dass die Datei in exports_dir liegt
    try:
        if exports_dir not in output_path.parents and output_path.parent != exports_dir:
            return jsonify({"error": "Ungültige Datei-Location"}), 403
    except Exception:
        return jsonify({"error": "Ungültige Datei-Location"}), 403

    if not output_path.exists():
        return jsonify({"error": "Video nicht gefunden – zuerst generieren"}), 404

    return send_file(
        output_path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=output_path.name,
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
                "include_fine_labels": bool(data.get("include_fine_labels", False)),
                "merge_fine_labels": bool(data.get("merge_fine_labels", False)),
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
            include_fine_labels=bool(data.get("include_fine_labels", False)),
            merge_fine_labels=bool(data.get("merge_fine_labels", False)),
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
        try:
            requested_workers = int(data.get("workers", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "workers muss eine Zahl sein"}), 400

        requested_order = str(data.get("order_mode", "mixed")).strip().lower() or "mixed"
        if requested_order not in ADMIN_REMATCH_ORDER_MODES:
            return jsonify({"error": "order_mode ist ungueltig"}), 400

        cpu_count = os.cpu_count() or 4
        max_workers = max(1, min(32, cpu_count * 2))
        safe_workers = max(1, min(requested_workers, max_workers))

        app_config: AppConfig = current_app.config.get("APP_CONFIG")
        db_path: Path = current_app.config["DB_PATH"]
        ensure_schema(db_path)
        save_admin_config(
            db_path,
            {
                "person_backend": data.get("person_backend"),
                "rematch_workers": safe_workers,
                "rematch_order": requested_order,
            },
        )
        job_manager = get_job_manager()
        admin_service = AdminService(app_config, job_manager)

        job_id = admin_service.start_rematch_persons(
            person_backend=data.get("person_backend"),
            workers=safe_workers,
            order_mode=requested_order,
        )
        return jsonify({"job_id": job_id, "status": "started", "workers": safe_workers, "max_workers": max_workers, "order_mode": requested_order})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@web_blueprint.post("/api/admin/config/start-detect-objects")
def api_admin_start_detect_objects():
    """Startet Objekt-Erkennungs-Job."""
    from .admin_jobs import get_job_manager
    from .admin_service import AdminService

    try:
        data = request.get_json() or {}
        photo_roots = data.get("photo_roots", [])
        if not photo_roots or not isinstance(photo_roots, list):
            return jsonify({"error": "photo_roots ist erforderlich und muss eine Liste sein"}), 400

        app_config: AppConfig = current_app.config.get("APP_CONFIG")
        db_path: Path = current_app.config["DB_PATH"]
        ensure_schema(db_path)

        model_name = data.get("model_name")
        confidence = data.get("confidence")
        device = data.get("device")
        labels_filter = data.get("labels_filter")
        include_person = bool(data.get("include_person", False))

        job_manager = get_job_manager()
        admin_service = AdminService(app_config, job_manager)

        job_id = admin_service.start_detect_objects(
            photo_roots=photo_roots,
            model_name=model_name,
            confidence=confidence,
            device=device,
            labels_filter=labels_filter,
            include_person=include_person,
        )
        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@web_blueprint.post("/api/admin/config/start-backfill-fine-labels")
def api_admin_start_backfill_fine_labels():
    """Startet Backfill-Job für fehlende Fine-Labels."""
    from .admin_jobs import get_job_manager
    from .admin_service import AdminService

    try:
        data = request.get_json() or {}
        db_path: Path = current_app.config["DB_PATH"]
        ensure_schema(db_path)

        config = get_admin_config(db_path)
        photo_roots = data.get("photo_roots") or config.get("photo_roots") or []
        if not photo_roots or not isinstance(photo_roots, list):
            return jsonify({"error": "Keine Foto-Pfade konfiguriert"}), 400

        app_config: AppConfig = current_app.config.get("APP_CONFIG")
        job_manager = get_job_manager()
        admin_service = AdminService(app_config, job_manager)

        job_id = admin_service.start_backfill_fine_labels(photo_roots=[str(root) for root in photo_roots])
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

