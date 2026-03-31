from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import logging
import os
from threading import Lock

import numpy as np
from PIL import Image


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
    _app: object

    def __init__(self) -> None:
        _configure_inference_logging()
        try:
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]
        except Exception as error:  # pragma: no cover - optional dependency
            raise RuntimeError("InsightFace ist nicht installiert.") from error

        model_name = os.getenv("FOTOS_INSIGHTFACE_MODEL", "buffalo_l")
        context_id = int(os.getenv("FOTOS_INSIGHTFACE_CTX", "0"))
        det_size = tuple(int(v.strip()) for v in os.getenv("FOTOS_INSIGHTFACE_DET_SIZE", "640,640").split(","))

        def _init_app():
            app_obj = FaceAnalysis(name=model_name)
            app_obj.prepare(ctx_id=context_id, det_size=det_size)
            return app_obj

        app = _run_quietly(_init_app)
        setattr(self, "_app", app)
        super().__init__(name="insightface", vector_dim=512)

    def vector_from_image(self, image: Image.Image) -> list[float] | None:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        bgr = rgb[:, :, ::-1]
        faces = self._app.get(bgr)
        if not faces:
            return None

        embedding = np.asarray(faces[0].embedding, dtype=np.float32)
        embedding = _normalize_vector(embedding)
        if embedding.shape[0] != self.vector_dim:
            return None
        return embedding.tolist()


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


_BACKEND_CACHE: dict[tuple[str, str, str, str], EmbeddingBackend] = {}
_BACKEND_CACHE_LOCK = Lock()


def _resolve_backend_cached(
    backend_name: str,
    model_name: str,
    context_id: str,
    det_size: str,
) -> EmbeddingBackend:
    cache_key = (backend_name, model_name, context_id, det_size)
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
                backend = HistogramBackend()
        elif backend_name == "auto":
            try:
                backend = InsightFaceBackend()
            except Exception:
                backend = HistogramBackend()
        else:
            raise ValueError(
                "Ungueltiger Personen-Backend-Name. Erlaubt sind: auto, insightface, histogram"
            )

        _BACKEND_CACHE[cache_key] = backend
        return backend


def resolve_backend(preferred_backend: str | None = None) -> EmbeddingBackend:
    backend_name = (preferred_backend or os.getenv("FOTOS_PERSON_BACKEND", "auto")).strip().lower()
    return _resolve_backend_cached(
        backend_name=backend_name,
        model_name=os.getenv("FOTOS_INSIGHTFACE_MODEL", "buffalo_l"),
        context_id=os.getenv("FOTOS_INSIGHTFACE_CTX", "0"),
        det_size=os.getenv("FOTOS_INSIGHTFACE_DET_SIZE", "640,640"),
    )


