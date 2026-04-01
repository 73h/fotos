"""
Aging-Timelapse-Generator fuer Personen-Alben.

Erzeugt ein MP4-Video, das eine Person sortiert nach Aufnahmedatum zeigt
und mit einem Morphing-Effekt zwischen den Aufnahmen altert.

Voraussetzung:  opencv-python  (pip install opencv-python)
Optional:       scipy          (pip install scipy) – fuer Delaunay-Morphing;
                               ohne scipy wird Cross-Fade verwendet.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

@dataclass
class TimelapseConfig:
    fps: int = 24
    """Frames pro Sekunde des Ausgabe-Videos."""
    hold_frames: int = 24
    """Frames, die ein einzelnes Originalfoto gezeigt wird (1 s bei 24 fps)."""
    morph_frames: int = 48
    """Anzahl der Übergangsframes zwischen zwei Fotos (2 s bei 24 fps)."""
    output_size: int = 512
    """Seitenlänge des (quadratischen) Ausgabe-Videos in Pixeln."""
    codec: str = "mp4v"
    """OpenCV VideoWriter-Codec-Tag."""
    min_face_score: float = 0.28
    """Mindest-Cosine-Ähnlichkeit zum Personen-Embedding; niedrigere Werte verwerfen."""


# ---------------------------------------------------------------------------
# ArcFace 5-Punkt-Template (normiert auf 112×112, skalierbar)
# ---------------------------------------------------------------------------

_TEMPLATE_112 = np.array([
    [38.2946, 51.6963],   # linkes Auge
    [73.5318, 51.5014],   # rechtes Auge
    [56.0252, 71.7366],   # Nasenspitze
    [41.5493, 92.3655],   # linker Mundwinkel
    [70.7299, 92.2041],   # rechter Mundwinkel
], dtype=np.float32)


def _template(size: int) -> np.ndarray:
    return _TEMPLATE_112 * (size / 112.0)


# ---------------------------------------------------------------------------
# Datenbank-Hilfsfunktionen
# ---------------------------------------------------------------------------

def _get_album_person_photos(
    db_path: Path,
    album_id: int,
    person_name: str,
) -> list[tuple[str, float]]:
    """Gibt (path, sort_ts) für alle Album-Fotos mit Personen-Treffer, aufsteigend nach Datum."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ph.path, COALESCE(ph.taken_ts, ph.modified_ts) AS sort_ts
            FROM photos ph
            JOIN album_photos ap ON ap.photo_path = ph.path
            JOIN photo_person_matches m ON m.photo_path = ph.path
            JOIN persons p ON p.id = m.person_id
            WHERE ap.album_id = ?
              AND lower(p.name) = lower(?)
            ORDER BY sort_ts ASC
            """,
            (album_id, person_name.strip()),
        ).fetchall()
    return [(str(r[0]), float(r[1])) for r in rows]


def _load_person_refs(db_path: Path, person_name: str) -> list[np.ndarray]:
    """Lädt alle gespeicherten Embedding-Vektoren einer Person."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.vector_json
            FROM person_refs r
            JOIN persons p ON p.id = r.person_id
            WHERE lower(p.name) = lower(?)
            """,
            (person_name.strip(),),
        ).fetchall()
    return [np.array(json.loads(r[0]), dtype=np.float32) for r in rows]


def _mean_embedding(refs: list[np.ndarray]) -> np.ndarray | None:
    if not refs:
        return None
    m = np.mean(refs, axis=0).astype(np.float32)
    n = np.linalg.norm(m)
    return m / n if n > 0 else m


# ---------------------------------------------------------------------------
# Gesichts-Extraktion und Alignment
# ---------------------------------------------------------------------------

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _align_from_kps(bgr: np.ndarray, kps: np.ndarray, size: int) -> np.ndarray | None:
    """Affin-Alignment auf das kanonische Template per Similarity-Transform."""
    try:
        import cv2
        M, _ = cv2.estimateAffinePartial2D(kps, _template(size), method=cv2.LMEDS)
        if M is None:
            return None
        return cv2.warpAffine(
            bgr, M, (size, size),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
    except Exception:
        return None


def _crop_resize(bgr: np.ndarray, box: tuple, size: int) -> np.ndarray | None:
    """Schneidet eine Bounding Box aus, fügt etwas Padding hinzu und resizet."""
    try:
        import cv2
        x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        h, w = bgr.shape[:2]
        pad = int((y2 - y1) * 0.15)
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        crop = bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        return cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR)
    except Exception:
        return None


def _extract_face(
    photo_path: str,
    mean_emb: np.ndarray | None,
    cfg: TimelapseConfig,
) -> np.ndarray | None:
    """
    Extrahiert das Gesicht der Zielperson aus einem Foto.

    Reihenfolge der Versuche:
    1. InsightFace – liefert Embedding-Matching + 5-Punkt-Alignment
    2. YOLO Bounding Box – einfacher Crop ohne Alignment
    """
    try:
        import cv2
        bgr = cv2.imread(photo_path, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
    except ImportError:
        return None

    # --- Versuch 1: InsightFace ---
    try:
        from ..persons.embeddings import resolve_backend, InsightFaceBackend
        from typing import cast, Any
        backend = resolve_backend(None)
        if isinstance(backend, InsightFaceBackend):
            app = cast(Any, backend._app)
            faces = app.get(bgr)
            if faces:
                if mean_emb is not None:
                    best = max(
                        faces,
                        key=lambda f: _cosine(np.asarray(f.embedding, dtype=np.float32), mean_emb),
                    )
                    score = _cosine(np.asarray(best.embedding, dtype=np.float32), mean_emb)
                    if score < cfg.min_face_score:
                        return None
                else:
                    best = faces[0]

                kps = getattr(best, "kps", None)
                if kps is not None:
                    aligned = _align_from_kps(bgr, np.asarray(kps, dtype=np.float32), cfg.output_size)
                    if aligned is not None:
                        return aligned

                bbox = getattr(best, "bbox", None)
                if bbox is not None:
                    return _crop_resize(bgr, bbox, cfg.output_size)
    except Exception:
        pass

    # --- Versuch 2: YOLO Bounding Box ---
    try:
        from ..detectors.labels import detect_person_boxes
        boxes = detect_person_boxes(Path(photo_path))
        if boxes:
            biggest = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            return _crop_resize(bgr, biggest, cfg.output_size)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Morphing
# ---------------------------------------------------------------------------

def _add_boundary(pts: np.ndarray, size: int) -> np.ndarray:
    """Ergänzt Keypoints um Bild-Randpunkte für vollständige Delaunay-Abdeckung."""
    s = size - 1
    boundary = np.array([
        [0, 0], [s // 2, 0], [s, 0],
        [0, s // 2], [s, s // 2],
        [0, s], [s // 2, s], [s, s],
    ], dtype=np.float32)
    return np.vstack([pts, boundary])


def _warp_triangle(
    src: np.ndarray,   # float32 HxWx3 Quellbild
    dst: np.ndarray,   # float32 HxWx3 Zielbild (in-place)
    t_src: np.ndarray, # 3×2 Dreieck im Quellbild
    t_dst: np.ndarray, # 3×2 Dreieck im Zielbild
) -> None:
    import cv2
    r1 = cv2.boundingRect(t_src.reshape(1, 3, 2).astype(np.float32))
    r2 = cv2.boundingRect(t_dst.reshape(1, 3, 2).astype(np.float32))
    x1s, y1s, ws, hs = r1
    x1d, y1d, wd, hd = r2
    H, W = dst.shape[:2]
    if ws <= 0 or hs <= 0 or wd <= 0 or hd <= 0:
        return
    if x1s < 0 or y1s < 0 or x1s + ws > W or y1s + hs > H:
        return
    if x1d < 0 or y1d < 0 or x1d + wd > W or y1d + hd > H:
        return

    t1_off = (t_src - [x1s, y1s]).astype(np.float32)
    t2_off = (t_dst - [x1d, y1d]).astype(np.float32)
    M = cv2.getAffineTransform(t1_off, t2_off)
    patch = src[y1s:y1s + hs, x1s:x1s + ws]
    warped = cv2.warpAffine(patch, M, (wd, hd), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REFLECT_101)
    mask = np.zeros((hd, wd), dtype=np.float32)
    cv2.fillConvexPoly(mask, t2_off.astype(np.int32), 1.0)
    m3 = mask[:, :, np.newaxis]
    roi = dst[y1d:y1d + hd, x1d:x1d + wd]
    roi[:] = roi * (1 - m3) + warped * m3


def _morph_frame(
    img1: np.ndarray,
    img2: np.ndarray,
    kps1: np.ndarray,      # 5×2 keypoints in output_size Koordinaten
    kps2: np.ndarray,      # 5×2 keypoints in output_size Koordinaten
    alpha: float,
    size: int,
) -> np.ndarray:
    """
    Erzeugt einen Morph-Zwischenframe.
    Nutzt Delaunay-Triangulations-Morphing wenn scipy verfügbar,
    sonst Cross-Fade.
    """
    # Wenn Keypoints identisch (beide aligned auf Template), degeneriert
    # Delaunay-Morphing sauber zu Cross-Fade – das ist korrekt und effizient.
    if not np.allclose(kps1, kps2, atol=1.0):
        try:
            from scipy.spatial import Delaunay  # type: ignore[import-not-found]
            p1 = _add_boundary(kps1, size)
            p2 = _add_boundary(kps2, size)
            pb = ((1 - alpha) * p1 + alpha * p2).astype(np.float32)
            tri = Delaunay(pb)
            f1 = img1.astype(np.float32)
            f2 = img2.astype(np.float32)
            w1 = np.zeros_like(f1)
            w2 = np.zeros_like(f2)
            for s in tri.simplices:
                _warp_triangle(f1, w1, p1[s], pb[s])
                _warp_triangle(f2, w2, p2[s], pb[s])
            return ((1 - alpha) * w1 + alpha * w2).clip(0, 255).astype(np.uint8)
        except ImportError:
            pass
        except Exception:
            pass

    # Cross-Fade (auch für aligned Faces der Standard-Pfad)
    return ((1 - alpha) * img1.astype(np.float32) + alpha * img2.astype(np.float32)
            ).clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# ---------------------------------------------------------------------------

def generate_aging_timelapse(
    db_path: Path,
    album_id: int,
    person_name: str,
    output_path: Path,
    config: TimelapseConfig | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> int:
    """
    Generiert einen Aging-Timelapse-Film.

    Args:
        db_path:      Pfad zur SQLite-Datenbank
        album_id:     ID des Quell-Albums
        person_name:  Name der darzustellenden Person
        output_path:  Ausgabepfad (z.B. ``aging.mp4``)
        config:       Optionale Konfiguration (Defaults gelten)
        progress_cb:  Optionaler Fortschritts-Callback (step, total, message)

    Returns:
        Anzahl der tatsächlich verwendeten Fotos

    Raises:
        ImportError:  opencv-python nicht installiert
        ValueError:   Zu wenige Gesichter gefunden
    """
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python ist erforderlich: pip install opencv-python"
        ) from exc

    cfg = config or TimelapseConfig()

    def _progress(step: int, total: int, msg: str) -> None:
        if progress_cb:
            progress_cb(step, total, msg)

    # 1. Album-Fotos mit Personen-Treffer laden
    photos = _get_album_person_photos(db_path, album_id, person_name)
    if not photos:
        raise ValueError(
            f"Keine Fotos für Person '{person_name}' in Album {album_id} gefunden. "
            "Stelle sicher, dass die Person eingelernt und das Album mit Fotos befüllt ist."
        )

    # 2. Mittleres Personen-Embedding für Face-Matching
    refs = _load_person_refs(db_path, person_name)
    mean_emb = _mean_embedding(refs)

    _progress(0, len(photos), f"{len(photos)} Fotos gefunden – extrahiere Gesichter …")

    # 3. Gesichts-Crops extrahieren
    face_frames: list[np.ndarray] = []
    skipped = 0
    for i, (path, _ts) in enumerate(photos):
        _progress(i, len(photos), f"Gesicht: {Path(path).name}")
        face = _extract_face(path, mean_emb, cfg)
        if face is None:
            skipped += 1
            continue
        face_frames.append(face)

    if len(face_frames) < 2:
        raise ValueError(
            f"Nur {len(face_frames)} Gesicht(er) gefunden ({skipped} übersprungen). "
            "Für ein Timelapse werden mindestens 2 Aufnahmen benötigt.\n"
            "Tipp: Stelle sicher, dass InsightFace installiert ist "
            "(pip install insightface onnxruntime) für bessere Gesichtserkennung."
        )

    # Für aligned Faces sind die Keypoints im Template-Raum identisch
    aligned_kps = _template(cfg.output_size)

    # 4. Video schreiben
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*cfg.codec)
    writer = cv2.VideoWriter(
        str(output_path), fourcc, cfg.fps, (cfg.output_size, cfg.output_size)
    )
    if not writer.isOpened():
        # Fallback-Codec
        writer.release()
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"XVID"),
            cfg.fps,
            (cfg.output_size, cfg.output_size),
        )

    n = len(face_frames)
    total_frames = n * cfg.hold_frames + (n - 1) * cfg.morph_frames

    for i, face in enumerate(face_frames):
        # Originalfoto halten
        for _ in range(cfg.hold_frames):
            writer.write(face)

        if i < n - 1:
            next_face = face_frames[i + 1]
            # Morphing-Übergang
            for j in range(cfg.morph_frames):
                alpha = (j + 1) / (cfg.morph_frames + 1)
                morph = _morph_frame(face, next_face, aligned_kps, aligned_kps, alpha, cfg.output_size)
                writer.write(morph)

        frames_done = (i + 1) * cfg.hold_frames + i * cfg.morph_frames
        _progress(i + 1, n, f"Video: {frames_done}/{total_frames} Frames …")

    writer.release()
    _progress(n, n, f"✓ Fertig – {n} Fotos, {total_frames} Frames → {output_path.name}")
    return n

