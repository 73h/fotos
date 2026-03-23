from __future__ import annotations

import os

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
        try:
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]
        except Exception as error:  # pragma: no cover - optional dependency
            raise RuntimeError("InsightFace ist nicht installiert.") from error

        model_name = os.getenv("FOTOS_INSIGHTFACE_MODEL", "buffalo_l")
        context_id = int(os.getenv("FOTOS_INSIGHTFACE_CTX", "0"))
        det_size = tuple(int(v.strip()) for v in os.getenv("FOTOS_INSIGHTFACE_DET_SIZE", "640,640").split(","))

        app = FaceAnalysis(name=model_name)
        app.prepare(ctx_id=context_id, det_size=det_size)
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


def resolve_backend(preferred_backend: str | None = None) -> EmbeddingBackend:
    backend_name = (preferred_backend or os.getenv("FOTOS_PERSON_BACKEND", "auto")).strip().lower()

    if backend_name == "histogram":
        return HistogramBackend()

    if backend_name == "insightface":
        try:
            return InsightFaceBackend()
        except Exception:
            return HistogramBackend()

    if backend_name != "auto":
        raise ValueError(
            "Ungueltiger Personen-Backend-Name. Erlaubt sind: auto, insightface, histogram"
        )

    try:
        return InsightFaceBackend()
    except Exception:
        return HistogramBackend()

