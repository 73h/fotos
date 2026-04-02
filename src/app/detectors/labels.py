from functools import lru_cache
import os
from pathlib import Path

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# Globale Variablen für Settings (werden beim Start initialisiert)
_YOLO_MODEL_NAME = None
_YOLO_CONFIDENCE = None
_YOLO_DEVICE = None


def _load_yolo_settings_from_db(db_path: Path | None = None) -> tuple[str, float, str]:
    """Lädt YOLO-Einstellungen aus der Datenbank oder ENV-Variablen."""
    global _YOLO_MODEL_NAME, _YOLO_CONFIDENCE, _YOLO_DEVICE

    model = "yolov8n.pt"
    confidence = 0.25
    device = "auto"

    # Versuche aus DB zu laden
    if db_path and db_path.exists():
        try:
            from ..index.store import get_admin_config
            config = get_admin_config(db_path)
            model = str(config.get("yolo_model", model))
            confidence = float(config.get("yolo_confidence", confidence))
            device_value = config.get("yolo_device", "0")
            device = str(device_value) if device_value else "auto"
        except Exception:
            pass

    # ENV-Variablen überschreiben (für Fallback/Kompabilität)
    model = os.getenv("FOTOS_YOLO_MODEL", model)
    try:
        confidence = float(os.getenv("FOTOS_YOLO_CONF", str(confidence)))
    except ValueError:
        pass
    device_env = os.getenv("FOTOS_YOLO_DEVICE", "").strip().lower()
    if device_env and device_env != "auto":
        device = device_env

    _YOLO_MODEL_NAME = model
    _YOLO_CONFIDENCE = confidence
    _YOLO_DEVICE = device if device != "auto" else _resolve_yolo_device_internal()

    return _YOLO_MODEL_NAME, _YOLO_CONFIDENCE, _YOLO_DEVICE


def _resolve_yolo_device_internal() -> str:
    """Bestimmt automatisch das beste Device (CUDA/CPU)."""
    try:
        import torch
        return "0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


_FALLBACK_KEYWORDS_TO_LABELS: dict[str, str] = {
    "person": "person",
    "people": "person",
    "mann": "person",
    "frau": "person",
    "kind": "person",
    "dog": "animal",
    "cat": "animal",
    "horse": "animal",
    "bird": "animal",
    "hund": "animal",
    "katze": "animal",
    "car": "object",
    "bike": "object",
    "fahrrad": "object",
    "table": "object",
    "phone": "object",
    "beach": "place",
    "mountain": "place",
    "city": "place",
    "wald": "place",
    "see": "place",
}

_YOLO_ANIMAL_CLASSES = {
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
}


_PLACE_KEYWORDS = {"beach", "mountain", "city", "wald", "see", "forest", "lake", "street"}


def initialize_yolo_settings(db_path: Path | None = None) -> None:
    """Initialisiert YOLO-Einstellungen zu Startup (kann aus DB geladen werden)."""
    global _YOLO_MODEL_NAME, _YOLO_CONFIDENCE, _YOLO_DEVICE
    _YOLO_MODEL_NAME, _YOLO_CONFIDENCE, _YOLO_DEVICE = _load_yolo_settings_from_db(db_path)


def _resolve_yolo_device() -> str:
    """Gibt das aktuelle YOLO-Device zurück."""
    global _YOLO_DEVICE
    if _YOLO_DEVICE is None:
        _YOLO_DEVICE = _resolve_yolo_device_internal()
    return _YOLO_DEVICE


def detect_person_boxes(path: Path) -> list[tuple[int, int, int, int]]:
    model = _load_model()
    if model is None:
        return []

    global _YOLO_CONFIDENCE
    if _YOLO_CONFIDENCE is None:
        _YOLO_CONFIDENCE = 0.25

    try:
        results = model.predict(source=str(path), conf=_YOLO_CONFIDENCE, verbose=False, device=_resolve_yolo_device())
    except Exception:
        return []

    names = model.names
    person_boxes: list[tuple[int, int, int, int]] = []

    for result in results:
        boxes = getattr(result, "boxes", None)
        class_ids = getattr(boxes, "cls", None) if boxes is not None else None
        xyxy_values = getattr(boxes, "xyxy", None) if boxes is not None else None
        if class_ids is None or xyxy_values is None:
            continue

        for raw_class_id, coords in zip(class_ids.tolist(), xyxy_values.tolist()):
            class_name = names.get(int(raw_class_id), "")
            if class_name != "person":
                continue

            x1, y1, x2, y2 = [int(max(0, round(value))) for value in coords]
            if x2 <= x1 or y2 <= y1:
                continue
            person_boxes.append((x1, y1, x2, y2))

    return person_boxes


@lru_cache(maxsize=1)
def _load_model():
    global _YOLO_MODEL_NAME
    if YOLO is None:
        return None
    if _YOLO_MODEL_NAME is None:
        _YOLO_MODEL_NAME = "yolov8n.pt"
    return YOLO(_YOLO_MODEL_NAME)


def _labels_from_path_keywords(path: Path) -> set[str]:
    token_source = path.as_posix().lower().replace("_", " ").replace("-", " ")
    return {
        label for keyword, label in _FALLBACK_KEYWORDS_TO_LABELS.items() if keyword in token_source
    }


def _has_place_keyword(path: Path) -> bool:
    token_source = path.as_posix().lower().replace("_", " ").replace("-", " ")
    return any(keyword in token_source for keyword in _PLACE_KEYWORDS)


def _labels_from_yolo(path: Path) -> set[str]:
    model = _load_model()
    if model is None:
        return set()

    global _YOLO_CONFIDENCE
    if _YOLO_CONFIDENCE is None:
        _YOLO_CONFIDENCE = 0.25

    labels: set[str] = set()
    try:
        results = model.predict(source=str(path), conf=_YOLO_CONFIDENCE, verbose=False, device=_resolve_yolo_device())
    except Exception:
        return set()

    names = model.names
    for result in results:
        boxes = getattr(result, "boxes", None)
        class_ids = getattr(boxes, "cls", None) if boxes is not None else None
        if class_ids is None:
            continue

        for raw_class_id in class_ids.tolist():
            class_name = names.get(int(raw_class_id), "")
            if class_name == "person":
                labels.add("person")
            elif class_name in _YOLO_ANIMAL_CLASSES:
                labels.add("animal")
            elif class_name:
                labels.add("object")

    return labels


def infer_labels_from_path(path: Path) -> list[str]:
    labels = _labels_from_yolo(path)

    # Wenn YOLO nicht verfuegbar ist oder nichts findet, bleibt die alte Heuristik aktiv.
    if not labels:
        labels.update(_labels_from_path_keywords(path))

    # Orte kommen im MVP zunaechst ueber Dateinamen/Pfad; spaeter via EXIF/Scene-Modell.
    if _has_place_keyword(path):
        labels.add("place")

    return sorted(labels)

