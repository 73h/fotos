import base64
import math
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

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


def _build_page_payload(query: str, page: int, per_page: int) -> dict[str, object]:
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
            "message": f"Index nicht gefunden: {db_path}",
        }

    if not query.strip():
        return {
            "items": [],
            "query": "",
            "page": 1,
            "per_page": per_page,
            "total": 0,
            "pages": 0,
            "has_prev": False,
            "has_next": False,
            "message": "Bitte Suchbegriff eingeben.",
        }

    safe_page = max(page, 1)
    safe_per_page = max(1, min(per_page, 200))
    offset = (safe_page - 1) * safe_per_page
    rows, total = run_search_page(db_path=db_path, query=query, limit=safe_per_page, offset=offset)
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
        "message": "Keine Treffer." if total == 0 else "",
    }


@web_blueprint.get("/")
def home():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    payload = _build_page_payload(query=query, page=page, per_page=per_page)
    return render_template("search.html", **payload)


@web_blueprint.get("/search")
def search_partial():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    payload = _build_page_payload(query=query, page=page, per_page=per_page)
    return render_template("_results.html", **payload)


@web_blueprint.get("/api/search")
def search_api():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=24, type=int)
    payload = _build_page_payload(query=query, page=page, per_page=per_page)
    return jsonify(payload)


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

