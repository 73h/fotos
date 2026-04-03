from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# Globale Variablen für Settings (werden beim Start initialisiert)
_YOLO_MODEL_NAME = None
_YOLO_CONFIDENCE = None
_YOLO_DEVICE = None


@dataclass(frozen=True)
class ObjectDetection:
    label: str
    kind: str
    group: str
    confidence: float
    bbox: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "kind": self.kind,
            "group": self.group,
            "confidence": self.confidence,
            "bbox": list(self.bbox) if self.bbox is not None else None,
        }


@dataclass(frozen=True)
class ObjectDetectionSummary:
    path: str
    model_name: str
    confidence_threshold: float
    device: str
    labels: list[str]
    counts_by_label: dict[str, int]
    counts_by_kind: dict[str, int]
    counts_by_group: dict[str, int]
    detections: list[ObjectDetection]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "model_name": self.model_name,
            "confidence_threshold": self.confidence_threshold,
            "device": self.device,
            "labels": list(self.labels),
            "counts_by_label": dict(self.counts_by_label),
            "counts_by_kind": dict(self.counts_by_kind),
            "counts_by_group": dict(self.counts_by_group),
            "detections": [detection.to_dict() for detection in self.detections],
        }


def _load_yolo_settings_from_db(db_path: Path | None = None) -> tuple[str, float, str]:
    """Lädt YOLO-Einstellungen aus der Datenbank."""
    model = "yolov8n.pt"
    confidence = 0.25
    device = "auto"

    # Versuche aus DB zu laden
    if db_path and db_path.exists():
        try:
            from ..index.store import get_admin_config
            config = get_admin_config(db_path)
            model = str(config.get("yolo_model", model))
            confidence = float(str(config.get("yolo_confidence", confidence)))
            device_value = config.get("yolo_device", "0")
            device = str(device_value) if device_value else "auto"
        except Exception:
            pass

    resolved_device = device if device != "auto" else _resolve_yolo_device_internal()
    return model, confidence, resolved_device


def _resolve_yolo_device_internal() -> str:
    """Bestimmt automatisch das beste Device (CUDA/CPU)."""
    try:
        import torch
        return "0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _clear_model_cache() -> None:
    cache_clear = getattr(_load_model, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()


def configure_yolo_runtime(
    model_name: str | None = None,
    confidence: float | None = None,
    device: str | None = None,
) -> None:
    """Überschreibt YOLO-Settings für den aktuellen Prozesslauf."""
    global _YOLO_MODEL_NAME, _YOLO_CONFIDENCE, _YOLO_DEVICE

    normalized_model = None
    if isinstance(model_name, str) and model_name.strip():
        normalized_model = model_name.strip()

    if normalized_model is not None and normalized_model != _YOLO_MODEL_NAME:
        _YOLO_MODEL_NAME = normalized_model
        _clear_model_cache()

    if confidence is not None:
        try:
            _YOLO_CONFIDENCE = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            pass

    if isinstance(device, str):
        normalized_device = device.strip() or "auto"
        _YOLO_DEVICE = normalized_device if normalized_device != "auto" else _resolve_yolo_device_internal()


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

_YOLO_GROUPS_BY_CLASS: dict[str, str] = {
    "person": "person",
    "bird": "bird",
    "cat": "pet",
    "dog": "pet",
    "horse": "farm-animal",
    "sheep": "farm-animal",
    "cow": "farm-animal",
    "elephant": "wildlife",
    "bear": "wildlife",
    "zebra": "wildlife",
    "giraffe": "wildlife",
    "bicycle": "vehicle",
    "car": "vehicle",
    "motorcycle": "vehicle",
    "airplane": "vehicle",
    "bus": "vehicle",
    "train": "vehicle",
    "truck": "vehicle",
    "boat": "vehicle",
    "traffic light": "outdoor",
    "fire hydrant": "outdoor",
    "stop sign": "outdoor",
    "parking meter": "outdoor",
    "bench": "outdoor",
    "backpack": "accessory",
    "umbrella": "accessory",
    "handbag": "accessory",
    "tie": "accessory",
    "suitcase": "accessory",
    "frisbee": "sports",
    "skis": "sports",
    "snowboard": "sports",
    "sports ball": "sports",
    "kite": "sports",
    "baseball bat": "sports",
    "baseball glove": "sports",
    "skateboard": "sports",
    "surfboard": "sports",
    "tennis racket": "sports",
    "bottle": "kitchenware",
    "wine glass": "kitchenware",
    "cup": "kitchenware",
    "fork": "kitchenware",
    "knife": "kitchenware",
    "spoon": "kitchenware",
    "bowl": "kitchenware",
    "banana": "food",
    "apple": "food",
    "sandwich": "food",
    "orange": "food",
    "broccoli": "food",
    "carrot": "food",
    "hot dog": "food",
    "pizza": "food",
    "donut": "food",
    "cake": "food",
    "chair": "furniture",
    "couch": "furniture",
    "potted plant": "furniture",
    "bed": "furniture",
    "dining table": "furniture",
    "toilet": "furniture",
    "tv": "electronics",
    "laptop": "electronics",
    "mouse": "electronics",
    "remote": "electronics",
    "keyboard": "electronics",
    "cell phone": "electronics",
    "microwave": "appliance",
    "oven": "appliance",
    "toaster": "appliance",
    "sink": "appliance",
    "refrigerator": "appliance",
    "book": "indoor-item",
    "clock": "indoor-item",
    "vase": "indoor-item",
    "scissors": "indoor-item",
    "teddy bear": "indoor-item",
    "hair drier": "indoor-item",
    "toothbrush": "indoor-item",
}


_PLACE_KEYWORDS = {"beach", "mountain", "city", "wald", "see", "forest", "lake", "street"}


def initialize_yolo_settings(db_path: Path | None = None) -> None:
    """Initialisiert YOLO-Einstellungen zu Startup (kann aus DB geladen werden)."""
    model_name, confidence, device = _load_yolo_settings_from_db(db_path)
    configure_yolo_runtime(model_name=model_name, confidence=confidence, device=device)


def _resolve_yolo_device() -> str:
    """Gibt das aktuelle YOLO-Device zurück."""
    global _YOLO_DEVICE
    if _YOLO_DEVICE is None:
        _YOLO_DEVICE = _resolve_yolo_device_internal()
    return _YOLO_DEVICE


def _tensor_to_list(value) -> list:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            return []
    try:
        return list(value)
    except TypeError:
        return []


def _resolve_class_name(names, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, ""))
    if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        return str(names[class_id])
    return ""


def _normalize_label_filter(label_filter: Iterable[str] | None) -> set[str] | None:
    if label_filter is None:
        return None
    normalized = {str(label).strip().lower() for label in label_filter if str(label).strip()}
    return normalized or None


def _kind_for_class_name(class_name: str) -> str:
    if class_name == "person":
        return "person"
    if class_name in _YOLO_ANIMAL_CLASSES:
        return "animal"
    return "object"


def _group_for_class_name(class_name: str) -> str:
    return _YOLO_GROUPS_BY_CLASS.get(class_name, _kind_for_class_name(class_name))


def _bbox_from_coords(coords) -> tuple[int, int, int, int] | None:
    if not isinstance(coords, (list, tuple)) or len(coords) < 4:
        return None
    try:
        x1, y1, x2, y2 = [int(max(0, round(float(value)))) for value in coords[:4]]
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


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

        for raw_class_id, coords in zip(_tensor_to_list(class_ids), _tensor_to_list(xyxy_values)):
            class_name = _resolve_class_name(names, int(raw_class_id)).strip().lower()
            if class_name != "person":
                continue

            bbox = _bbox_from_coords(coords)
            if bbox is None:
                continue
            person_boxes.append(bbox)

    return person_boxes


@lru_cache(maxsize=1)
def _load_model():
    global _YOLO_MODEL_NAME
    if YOLO is None:
        return None
    if _YOLO_MODEL_NAME is None:
        _YOLO_MODEL_NAME = "yolov8n.pt"
    try:
        return YOLO(_YOLO_MODEL_NAME)
    except Exception:
        return None


def get_supported_yolo_classes() -> list[str]:
    model = _load_model()
    if model is None:
        return []

    names = getattr(model, "names", {})
    if isinstance(names, dict):
        return sorted({str(name).strip().lower() for name in names.values() if str(name).strip()})
    if isinstance(names, (list, tuple)):
        return sorted({str(name).strip().lower() for name in names if str(name).strip()})
    return []


def _labels_from_path_keywords(path: Path) -> set[str]:
    token_source = path.as_posix().lower().replace("_", " ").replace("-", " ")
    return {
        label for keyword, label in _FALLBACK_KEYWORDS_TO_LABELS.items() if keyword in token_source
    }


def _has_place_keyword(path: Path) -> bool:
    token_source = path.as_posix().lower().replace("_", " ").replace("-", " ")
    return any(keyword in token_source for keyword in _PLACE_KEYWORDS)


def detect_objects(
    path: Path,
    *,
    include_person: bool = True,
    label_filter: Iterable[str] | None = None,
) -> list[ObjectDetection]:
    model = _load_model()
    if model is None:
        return []

    global _YOLO_CONFIDENCE
    if _YOLO_CONFIDENCE is None:
        _YOLO_CONFIDENCE = 0.25

    try:
        results = model.predict(
            source=str(path),
            conf=_YOLO_CONFIDENCE,
            verbose=False,
            device=_resolve_yolo_device(),
        )
    except Exception:
        return []

    names = getattr(model, "names", {})
    normalized_filter = _normalize_label_filter(label_filter)
    detections: list[ObjectDetection] = []

    for result in results:
        boxes = getattr(result, "boxes", None)
        class_ids = getattr(boxes, "cls", None) if boxes is not None else None
        confidences = getattr(boxes, "conf", None) if boxes is not None else None
        xyxy_values = getattr(boxes, "xyxy", None) if boxes is not None else None
        if class_ids is None:
            continue

        class_id_values = _tensor_to_list(class_ids)
        confidence_values = _tensor_to_list(confidences)
        xyxy_list = _tensor_to_list(xyxy_values)

        for index, raw_class_id in enumerate(class_id_values):
            try:
                class_id = int(raw_class_id)
            except (TypeError, ValueError):
                continue

            class_name = _resolve_class_name(names, class_id).strip().lower()
            if not class_name:
                continue
            if class_name == "person" and not include_person:
                continue
            if normalized_filter is not None and class_name not in normalized_filter:
                continue

            confidence = 0.0
            if index < len(confidence_values):
                try:
                    confidence = max(0.0, min(1.0, float(confidence_values[index])))
                except (TypeError, ValueError):
                    confidence = 0.0

            bbox = _bbox_from_coords(xyxy_list[index]) if index < len(xyxy_list) else None
            detections.append(
                ObjectDetection(
                    label=class_name,
                    kind=_kind_for_class_name(class_name),
                    group=_group_for_class_name(class_name),
                    confidence=confidence,
                    bbox=bbox,
                )
            )

    return detections


def summarize_object_detections(
    path: Path,
    *,
    include_person: bool = True,
    label_filter: Iterable[str] | None = None,
) -> ObjectDetectionSummary:
    detections = detect_objects(path, include_person=include_person, label_filter=label_filter)
    labels_counter = Counter(detection.label for detection in detections)
    kinds_counter = Counter(detection.kind for detection in detections)
    groups_counter = Counter(detection.group for detection in detections)

    return ObjectDetectionSummary(
        path=str(path),
        model_name=_YOLO_MODEL_NAME or "yolov8n.pt",
        confidence_threshold=float(_YOLO_CONFIDENCE if _YOLO_CONFIDENCE is not None else 0.25),
        device=_resolve_yolo_device(),
        labels=sorted(labels_counter),
        counts_by_label={label: labels_counter[label] for label in sorted(labels_counter)},
        counts_by_kind={label: kinds_counter[label] for label in sorted(kinds_counter)},
        counts_by_group={label: groups_counter[label] for label in sorted(groups_counter)},
        detections=detections,
    )


def _labels_from_yolo(path: Path) -> set[str]:
    return {detection.kind for detection in detect_objects(path, include_person=True)}


def infer_fine_yolo_labels(
    path: Path,
    include_person: bool = False,
    label_filter: Iterable[str] | None = None,
) -> set[str]:
    """
    Extrahiert feine YOLO-Klassenlabels mit Präfix, z. B. 'yolo:cat', 'yolo:chair'.
    Diese sind zusätzlich zu groben Labels wie 'animal' / 'object' speicherbar.

    Rückgabe: Set von Labels mit 'yolo:' Präfix, z. B. {'yolo:cat', 'yolo:chair'}
    """
    detections = detect_objects(path, include_person=include_person, label_filter=label_filter)
    return {f"yolo:{detection.label}" for detection in detections}


def infer_labels_from_path(path: Path) -> list[str]:
    labels = _labels_from_yolo(path)

    # Wenn YOLO nicht verfuegbar ist oder nichts findet, bleibt die alte Heuristik aktiv.
    if not labels:
        labels.update(_labels_from_path_keywords(path))

    # Orte kommen im MVP zunaechst ueber Dateinamen/Pfad; spaeter via EXIF/Scene-Modell.
    if _has_place_keyword(path):
        labels.add("place")

    return sorted(labels)

