from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, cast

import numpy as np
from PIL import Image


# Globale Variablen für InsightFace-Einstellungen
_INSIGHTFACE_MODEL = None
_INSIGHTFACE_CTX = None
_INSIGHTFACE_DET_SIZE = None


def _load_insightface_settings_from_db(db_path: Path | None = None) -> tuple[str, int, str]:
    """Lädt InsightFace-Einstellungen aus der Datenbank oder ENV-Variablen."""
    global _INSIGHTFACE_MODEL, _INSIGHTFACE_CTX, _INSIGHTFACE_DET_SIZE

    model = "buffalo_l"
    ctx = 0
    det_size = "640,640"

    # Versuche aus DB zu laden
    if db_path and db_path.exists():
        try:
            from ..index.store import get_admin_config
            config = get_admin_config(db_path)
            model = str(config.get("insightface_model", model))
            ctx = int(config.get("insightface_ctx", ctx))
            det_size = str(config.get("insightface_det_size", det_size))
        except Exception:
            pass

    # ENV-Variablen überschreiben (für Fallback/Kompabilität)
    model = os.getenv("FOTOS_INSIGHTFACE_MODEL", model)
    try:
        ctx = int(os.getenv("FOTOS_INSIGHTFACE_CTX", str(ctx)))
    except ValueError:
        pass
    det_size = os.getenv("FOTOS_INSIGHTFACE_DET_SIZE", det_size)

    _INSIGHTFACE_MODEL = model
    _INSIGHTFACE_CTX = ctx
    _INSIGHTFACE_DET_SIZE = det_size

    return _INSIGHTFACE_MODEL, _INSIGHTFACE_CTX, _INSIGHTFACE_DET_SIZE


def initialize_insightface_settings(db_path: Path | None = None) -> None:
    """Initialisiert InsightFace-Einstellungen zu Startup (kann aus DB geladen werden)."""
    global _INSIGHTFACE_MODEL, _INSIGHTFACE_CTX, _INSIGHTFACE_DET_SIZE
    _INSIGHTFACE_MODEL, _INSIGHTFACE_CTX, _INSIGHTFACE_DET_SIZE = _load_insightface_settings_from_db(db_path)


def _normalize_vector(values: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(values)
    if norm == 0:
        return values
    return values / norm


def cosine_similarity(left: list[float], right: list[float]) -> float:
    left_vec = _normalize_vector(np.array(left, dtype=np.float32))
    right_vec = _normalize_vector(np.array(right, dtype=np.float32))
    return float(np.dot(left_vec, right_vec))


class EmbeddingBackend:
    def __init__(self, name: str, vector_dim: int) -> None:
        self.name = name
        self.vector_dim = vector_dim

    def vector_from_image(self, image: Image.Image) -> list[float] | None:
        raise NotImplementedError

    def smile_score_from_image(self, image: Image.Image) -> float | None:
        return None


class HistogramBackend(EmbeddingBackend):
    def __init__(self) -> None:
        super().__init__(name="histogram", vector_dim=96)

    def vector_from_image(self, image: Image.Image) -> list[float] | None:
        normalized = image.convert("RGB").resize((64, 128))
        data = np.asarray(normalized, dtype=np.float32)

        channels: list[np.ndarray] = []
        for channel in range(3):
            hist, _ = np.histogram(data[:, :, channel], bins=32, range=(0, 255), density=True)
            channels.append(hist.astype(np.float32))

        descriptor = np.concatenate(channels, axis=0)
        descriptor = _normalize_vector(descriptor)
        if descriptor.shape[0] != self.vector_dim:
            return None
        return descriptor.tolist()


class InsightFaceBackend(EmbeddingBackend):
    _app: Any

    def __init__(self) -> None:
        _configure_inference_logging()
        try:
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]
        except Exception as error:  # pragma: no cover - optional dependency
            raise RuntimeError("InsightFace ist nicht installiert.") from error

        global _INSIGHTFACE_MODEL, _INSIGHTFACE_CTX, _INSIGHTFACE_DET_SIZE

        if _INSIGHTFACE_MODEL is None:
            _INSIGHTFACE_MODEL = "buffalo_l"
        if _INSIGHTFACE_CTX is None:
            _INSIGHTFACE_CTX = 0
        if _INSIGHTFACE_DET_SIZE is None:
            _INSIGHTFACE_DET_SIZE = "640,640"

        model_name = _INSIGHTFACE_MODEL
        context_id = _INSIGHTFACE_CTX
        det_size_str = _INSIGHTFACE_DET_SIZE

        # Parse det_size
        try:
            det_size = tuple(int(v.strip()) for v in det_size_str.split(","))
        except (ValueError, AttributeError):
            det_size = (640, 640)

        def _init_app():
            app_obj = FaceAnalysis(name=model_name)
            app_obj.prepare(ctx_id=context_id, det_size=det_size)
            return app_obj

        app = _run_quietly(_init_app)
        setattr(self, "_app", app)
        super().__init__(name="insightface", vector_dim=512)

    def _get_primary_face(self, image: Image.Image):
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        bgr = rgb[:, :, ::-1]
        app = cast(Any, self._app)
        faces = app.get(bgr)
        if not faces:
            return None
        return faces[0]

    def vector_from_image(self, image: Image.Image) -> list[float] | None:
        face = self._get_primary_face(image)
        if face is None:
            return None

        embedding = np.asarray(face.embedding, dtype=np.float32)
        embedding = _normalize_vector(embedding)
        if embedding.shape[0] != self.vector_dim:
            return None
        return embedding.tolist()

    def smile_score_from_image(self, image: Image.Image) -> float | None:
        face = self._get_primary_face(image)
        if face is None:
            return None

        keypoints = np.asarray(getattr(face, "kps", None), dtype=np.float32)
        if keypoints.shape != (5, 2):
            return None

        left_eye, right_eye = keypoints[0], keypoints[1]
        left_mouth, right_mouth = keypoints[3], keypoints[4]

        eye_distance = float(np.linalg.norm(right_eye - left_eye))
        mouth_distance = float(np.linalg.norm(right_mouth - left_mouth))
        if eye_distance <= 1e-6:
            return None

        ratio = mouth_distance / eye_distance
        normalized = (ratio - 0.45) / 0.30
        return float(max(0.0, min(1.0, normalized)))


def _configure_inference_logging() -> None:
    # Unterdrueckt laute Bibliotheksausgaben, Fortschritt bleibt ueber tqdm sichtbar.
    if os.getenv("FOTOS_QUIET_INFERENCE", "1") != "1":
        return
    logging.getLogger("insightface").setLevel(logging.ERROR)
    logging.getLogger("onnxruntime").setLevel(logging.ERROR)
    os.environ.setdefault("ORT_LOG_SEVERITY_LEVEL", "4")
    os.environ.setdefault("ORT_LOG_VERBOSITY_LEVEL", "0")


def _run_quietly(factory):
    if os.getenv("FOTOS_QUIET_INFERENCE", "1") != "1":
        return factory()
    with open(os.devnull, "w", encoding="ascii") as devnull:
        with redirect_stdout(devnull), redirect_stderr(devnull):
            return factory()


_BACKEND_CACHE: dict[tuple[str, str, str, str, str], EmbeddingBackend] = {}
_BACKEND_CACHE_LOCK = Lock()


def _resolve_backend_cached(
    backend_name: str,
    model_name: str,
    context_id: str,
    det_size: str,
    strict: bool,
) -> EmbeddingBackend:
    cache_key = (backend_name, model_name, context_id, det_size, "strict" if strict else "fallback")
    with _BACKEND_CACHE_LOCK:
        cached = _BACKEND_CACHE.get(cache_key)
        if cached is not None:
            return cached

        if backend_name == "histogram":
            backend = HistogramBackend()
        elif backend_name == "insightface":
            try:
                backend = InsightFaceBackend()
            except Exception:
                if strict:
                    raise
                backend = HistogramBackend()
        elif backend_name == "auto":
            try:
                backend = InsightFaceBackend()
            except Exception:
                if strict:
                    raise
                backend = HistogramBackend()
        else:
            raise ValueError(
                "Ungueltiger Personen-Backend-Name. Erlaubt sind: auto, insightface, histogram"
            )

        _BACKEND_CACHE[cache_key] = backend
        return backend


def resolve_backend(preferred_backend: str | None = None, strict: bool = False) -> EmbeddingBackend:
    global _INSIGHTFACE_MODEL, _INSIGHTFACE_CTX, _INSIGHTFACE_DET_SIZE

    backend_name = (preferred_backend or os.getenv("FOTOS_PERSON_BACKEND", "auto")).strip().lower()

    if _INSIGHTFACE_MODEL is None:
        _INSIGHTFACE_MODEL = "buffalo_l"
    if _INSIGHTFACE_CTX is None:
        _INSIGHTFACE_CTX = 0
    if _INSIGHTFACE_DET_SIZE is None:
        _INSIGHTFACE_DET_SIZE = "640,640"

    return _resolve_backend_cached(
        backend_name=backend_name,
        model_name=_INSIGHTFACE_MODEL,
        context_id=str(_INSIGHTFACE_CTX),
        det_size=_INSIGHTFACE_DET_SIZE,
        strict=strict,
    )
