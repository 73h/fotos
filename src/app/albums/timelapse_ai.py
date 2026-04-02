from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable, Protocol

import numpy as np


class TimelapseEnhancer(Protocol):
    def enhance_sequence(
        self,
        frames: list[np.ndarray],
        strength: float,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[np.ndarray]:
        """Verbessert eine Liste von Face-Frames und liefert neue Frames zurueck."""


def _safe_progress(
    progress_cb: Callable[[int, int, str], None] | None,
    step: int,
    total: int,
    message: str,
) -> None:
    if progress_cb:
        progress_cb(step, total, message)


@dataclass
class NoopEnhancer:
    reason: str = "ai disabled"

    def enhance_sequence(
        self,
        frames: list[np.ndarray],
        strength: float,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[np.ndarray]:
        _safe_progress(progress_cb, 0, max(1, len(frames)), f"AI-Enhancer uebersprungen ({self.reason})")
        return list(frames)


@dataclass
class CompositeEnhancer:
    enhancers: list[TimelapseEnhancer]
    label: str = "composite"

    def enhance_sequence(
        self,
        frames: list[np.ndarray],
        strength: float,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[np.ndarray]:
        current = list(frames)
        total = max(1, len(self.enhancers))
        for idx, enhancer in enumerate(self.enhancers, start=1):
            try:
                current = enhancer.enhance_sequence(current, strength, progress_cb=None)
                _safe_progress(progress_cb, idx, total, f"AI-Backend {idx}/{total} ok ({self.label})")
            except Exception as exc:
                _safe_progress(progress_cb, idx, total, f"AI-Backend {idx}/{total} fehlgeschlagen: {exc}")
        return current


class LocalAIMaxEnhancer:
    """
    Lokaler Platzhalter fuer spaetere Modellintegration.

    Nutzt aktuell OpenCV-Operatoren (Detail + Stabilisierung), ist aber bewusst
    als austauschbarer Hook gebaut, damit spaeter z.B. FaceID/diffusion-Backends
    ohne API-Bruch eingebunden werden koennen.
    """

    def enhance_sequence(
        self,
        frames: list[np.ndarray],
        strength: float,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[np.ndarray]:
        if not frames:
            return []

        import cv2

        s = float(max(0.0, min(1.0, strength)))
        anchor = frames[0].astype(np.float32)
        anchor_mean = anchor.mean(axis=(0, 1), keepdims=True)

        out: list[np.ndarray] = []
        total = len(frames)
        for i, frame in enumerate(frames, start=1):
            # Farbstabilisierung gegen den ersten Frame reduziert Flicker.
            img = frame.astype(np.float32)
            mean = img.mean(axis=(0, 1), keepdims=True)
            gain = np.clip(anchor_mean / np.maximum(mean, 1.0), 0.8, 1.25)
            stabilized = np.clip(img * gain, 0, 255).astype(np.uint8)

            sigma_s = 8 + int(10 * s)
            sigma_r = 0.08 + 0.12 * s
            detailed = cv2.detailEnhance(stabilized, sigma_s=sigma_s, sigma_r=sigma_r)
            denoised = cv2.bilateralFilter(detailed, d=0, sigmaColor=20 + 40 * s, sigmaSpace=7 + 8 * s)

            blur = cv2.GaussianBlur(denoised, (0, 0), sigmaX=1.0 + 0.8 * s, sigmaY=1.0 + 0.8 * s)
            sharp = cv2.addWeighted(denoised, 1.0 + 0.35 * s, blur, -0.35 * s, 0)
            out.append(sharp)

            _safe_progress(progress_cb, i, total, f"AI-Enhancement: {i}/{total}")

        return out


@dataclass
class DnnSuperResEnhancer:
    """Optionales OpenCV-Super-Resolution-Backend (wenn Modellpfad vorhanden ist)."""

    model_path: Path
    model_name: str = "espcn"
    scale: int = 2

    def _create_sr(self):
        import cv2

        if not self.model_path.exists() or not self.model_path.is_file():
            raise FileNotFoundError(f"SuperRes-Modell nicht gefunden: {self.model_path}")

        dnn_superres = cv2.dnn_superres  # type: ignore[attr-defined]
        sr = dnn_superres.DnnSuperResImpl_create()
        sr.readModel(str(self.model_path))
        sr.setModel(self.model_name, int(self.scale))
        return sr

    def enhance_sequence(
        self,
        frames: list[np.ndarray],
        strength: float,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[np.ndarray]:
        if not frames:
            return []

        import cv2

        sr = self._create_sr()
        mix = float(max(0.0, min(1.0, strength)))
        out: list[np.ndarray] = []
        total = len(frames)

        for i, frame in enumerate(frames, start=1):
            h, w = frame.shape[:2]
            upscaled = sr.upsample(frame)
            restored = cv2.resize(upscaled, (w, h), interpolation=cv2.INTER_CUBIC)
            blended = cv2.addWeighted(frame, 1.0 - 0.65 * mix, restored, 0.65 * mix, 0)
            out.append(blended)
            _safe_progress(progress_cb, i, total, f"SuperRes: {i}/{total}")

        return out


def _resolve_superres_enhancer() -> TimelapseEnhancer | None:
    model_text = os.getenv("FOTOS_TIMELAPSE_SUPERRES_MODEL", "").strip()
    if not model_text:
        return None

    model_path = Path(model_text)
    if not model_path.exists() or not model_path.is_file():
        return None

    model_name = os.getenv("FOTOS_TIMELAPSE_SUPERRES_NAME", "espcn").strip().lower() or "espcn"
    scale_text = os.getenv("FOTOS_TIMELAPSE_SUPERRES_SCALE", "2").strip() or "2"
    try:
        scale = int(scale_text)
    except ValueError:
        scale = 2
    return DnnSuperResEnhancer(model_path=model_path, model_name=model_name, scale=max(2, min(4, scale)))


@dataclass
class OnnxFaceEnhancer:
    """Optionales ONNX-Backend fuer bild-zu-bild Face-Enhancement."""

    model_path: Path
    provider: str = "auto"
    input_size: int = 256

    def _create_session(self):
        import onnxruntime as ort

        providers = ["CPUExecutionProvider"]
        if self.provider in {"auto", "cuda", "gpu"}:
            avail = set(ort.get_available_providers())
            if "CUDAExecutionProvider" in avail:
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ort.InferenceSession(str(self.model_path), providers=providers)

    def _run_frame(self, session, frame: np.ndarray, strength: float) -> np.ndarray:
        import cv2

        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        size = int(max(64, min(2048, self.input_size)))

        resized = cv2.resize(frame, (size, size), interpolation=cv2.INTER_CUBIC)
        x = resized.astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None, ...]
        y = session.run([output_name], {input_name: x})[0]

        if y.ndim != 4:
            raise RuntimeError("ONNX-Ausgabeformat nicht unterstuetzt")
        y = y[0]
        if y.shape[0] == 3:
            y = np.transpose(y, (1, 2, 0))
        y = np.clip(y, 0.0, 1.0)

        enhanced = (y * 255.0).astype(np.uint8)
        enhanced = cv2.resize(enhanced, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_CUBIC)
        s = float(max(0.0, min(1.0, strength)))
        return cv2.addWeighted(frame, 1.0 - 0.7 * s, enhanced, 0.7 * s, 0)

    def enhance_sequence(
        self,
        frames: list[np.ndarray],
        strength: float,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[np.ndarray]:
        if not frames:
            return []
        if not self.model_path.exists() or not self.model_path.is_file():
            raise FileNotFoundError(f"ONNX-Modell nicht gefunden: {self.model_path}")

        session = self._create_session()
        out: list[np.ndarray] = []
        total = len(frames)
        for i, frame in enumerate(frames, start=1):
            try:
                out.append(self._run_frame(session, frame, strength))
            except Exception:
                out.append(frame)
            _safe_progress(progress_cb, i, total, f"ONNX-Enhancement: {i}/{total}")
        return out


def _can_use_onnxruntime() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _resolve_onnx_enhancer() -> TimelapseEnhancer | None:
    model_text = os.getenv("FOTOS_TIMELAPSE_FACE_ONNX_MODEL", "").strip()
    if not model_text:
        return None

    model_path = Path(model_text)
    if not model_path.exists() or not model_path.is_file():
        return None
    if not _can_use_onnxruntime():
        return None

    provider = os.getenv("FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER", "auto").strip().lower() or "auto"
    size_text = os.getenv("FOTOS_TIMELAPSE_FACE_ONNX_SIZE", "256").strip() or "256"
    try:
        input_size = int(size_text)
    except ValueError:
        input_size = 256
    return OnnxFaceEnhancer(model_path=model_path, provider=provider, input_size=input_size)


def resolve_enhancer(ai_mode: str, ai_backend: str = "auto") -> TimelapseEnhancer:
    mode = (ai_mode or "off").strip().lower()
    if mode == "off":
        return NoopEnhancer("off")

    try:
        import cv2  # noqa: F401
    except Exception:
        return NoopEnhancer("opencv fehlt")

    if mode in {"auto", "max"}:
        backend = (ai_backend or os.getenv("FOTOS_TIMELAPSE_AI_BACKEND", "auto")).strip().lower() or "auto"
        local = LocalAIMaxEnhancer()
        onnx = _resolve_onnx_enhancer()
        superres = _resolve_superres_enhancer()

        if backend == "local":
            return local
        if backend == "onnx" and onnx is not None:
            return onnx
        if backend == "superres" and superres is not None:
            return superres
        if backend == "onnx":
            return local

        chain: list[TimelapseEnhancer] = [local]
        if onnx is not None:
            chain.append(onnx)
        if superres is not None:
            chain.append(superres)
        if len(chain) > 1:
            return CompositeEnhancer(chain, label="local+optional-backends")
        return local

    return NoopEnhancer(f"unbekannter mode: {mode}")


def enhance_sequence_with_ai(
    frames: list[np.ndarray],
    ai_mode: str,
    ai_backend: str,
    ai_strength: float,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[np.ndarray]:
    enhancer = resolve_enhancer(ai_mode, ai_backend=ai_backend)
    try:
        return enhancer.enhance_sequence(frames, ai_strength, progress_cb=progress_cb)
    except Exception:
        # Harte Sicherheitsleine: kein Abbruch der Timelapse bei AI-Fehlern.
        return list(frames)

