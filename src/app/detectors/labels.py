from functools import lru_cache
import os
from pathlib import Path

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


def _resolve_yolo_device() -> str:
    """Bestimmt das Ziel-Device fuer YOLO (cuda/cpu), konfigurierbar per FOTOS_YOLO_DEVICE."""
    device = os.getenv("FOTOS_YOLO_DEVICE", "auto").strip().lower()
    if device != "auto":
        return device
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

_YOLO_MODEL_NAME = os.getenv("FOTOS_YOLO_MODEL", "yolov8n.pt")
_YOLO_CONFIDENCE = float(os.getenv("FOTOS_YOLO_CONF", "0.25"))


def detect_person_boxes(path: Path) -> list[tuple[int, int, int, int]]:
    model = _load_model()
    if model is None:
        return []

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
    if YOLO is None:
        return None
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

