from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..detectors import labels as yolo_labels
from ..ingest import scan_images
from .embeddings import cosine_similarity, resolve_backend
from .store import (
    PersonPhotoHit,
    PersonReference,
    list_person_references,
    replace_person_references,
    replace_photo_person_matches,
    search_photos_by_person_name,
    upsert_person,
)

# Globale Variablen für Personen-Einstellungen
_PERSON_THRESHOLD = None
_PERSON_TOP_K = None
_USE_FULL_IMAGE_FALLBACK = None


def _load_person_settings_from_db(db_path: Path | None = None) -> tuple[float, int, bool]:
    """Lädt Personen-Einstellungen aus der Datenbank oder ENV-Variablen."""
    global _PERSON_THRESHOLD, _PERSON_TOP_K, _USE_FULL_IMAGE_FALLBACK

    threshold = 0.38
    top_k = 3
    use_fallback = True

    # Versuche aus DB zu laden
    if db_path and db_path.exists():
        try:
            from ..index.store import get_admin_config
            config = get_admin_config(db_path)
            threshold = float(config.get("person_threshold", threshold))
            top_k = int(config.get("person_top_k", top_k))
            use_fallback = bool(config.get("person_full_image_fallback", use_fallback))
        except Exception:
            pass

    # ENV-Variablen überschreiben (für Fallback/Kompabilität)
    try:
        threshold = float(os.getenv("FOTOS_PERSON_THRESHOLD", str(threshold)))
    except ValueError:
        pass

    try:
        top_k = int(os.getenv("FOTOS_PERSON_TOP_K", str(top_k)))
    except ValueError:
        pass

    use_fallback = os.getenv("FOTOS_PERSON_FULL_IMAGE_FALLBACK", "1") == "1"

    _PERSON_THRESHOLD = threshold
    _PERSON_TOP_K = top_k
    _USE_FULL_IMAGE_FALLBACK = use_fallback

    return _PERSON_THRESHOLD, _PERSON_TOP_K, _USE_FULL_IMAGE_FALLBACK


def initialize_person_settings(db_path: Path | None = None) -> None:
    """Initialisiert Personen-Einstellungen zu Startup (kann aus DB geladen werden)."""
    global _PERSON_THRESHOLD, _PERSON_TOP_K, _USE_FULL_IMAGE_FALLBACK
    _PERSON_THRESHOLD, _PERSON_TOP_K, _USE_FULL_IMAGE_FALLBACK = _load_person_settings_from_db(db_path)


@dataclass(frozen=True)
class PersonMatch:
    person_id: int
    person_name: str
    score: float
    smile_score: float | None = None


@dataclass(frozen=True)
class EnrollResult:
    person_name: str
    backend: str
    image_count: int
    sample_count: int


def extract_person_signatures(
    photo_path: Path,
    preferred_backend: str | None = None,
    strict_backend: bool = False,
) -> tuple[str, list[tuple[list[float], float | None]], int]:
    """Gibt (backend_name, signatures_mit_smile, person_box_count) zurück."""
    if not photo_path.exists():
        return ("unknown", [], 0)

    backend = resolve_backend(preferred_backend, strict=strict_backend)

    signatures: list[tuple[list[float], float | None]] = []
    try:
        with Image.open(photo_path) as image:
            boxes = yolo_labels.detect_person_boxes(photo_path)
            person_box_count = len(boxes)
            for x1, y1, x2, y2 in boxes:
                crop = image.crop((x1, y1, x2, y2))
                vector = backend.vector_from_image(crop)
                if vector is not None:
                    smile_score = backend.smile_score_from_image(crop)
                    signatures.append((vector, smile_score))

            global _USE_FULL_IMAGE_FALLBACK
            if _USE_FULL_IMAGE_FALLBACK is None:
                _USE_FULL_IMAGE_FALLBACK = True

            if not signatures and _USE_FULL_IMAGE_FALLBACK:
                vector = backend.vector_from_image(image)
                if vector is not None:
                    smile_score = backend.smile_score_from_image(image)
                    signatures.append((vector, smile_score))
    except Exception:
        return (backend.name, [], 0)

    return (backend.name, signatures, person_box_count)


def _group_references_by_person(
    references: list[PersonReference],
) -> dict[tuple[int, str], list[list[float]]]:
    grouped: dict[tuple[int, str], list[list[float]]] = defaultdict(list)
    for reference in references:
        grouped[(reference.person_id, reference.person_name)].append(reference.vector)
    return grouped


def _score_signature_against_references(
    signature: list[float],
    references_by_person: dict[tuple[int, str], list[list[float]]],
    smile_score: float | None = None,
) -> list[PersonMatch]:
    global _PERSON_THRESHOLD
    if _PERSON_THRESHOLD is None:
        _PERSON_THRESHOLD = 0.38

    scored: list[PersonMatch] = []
    for (person_id, person_name), vectors in references_by_person.items():
        if not vectors:
            continue

        best_score = max(cosine_similarity(signature, reference) for reference in vectors)
        if best_score >= _PERSON_THRESHOLD:
            scored.append(
                PersonMatch(
                    person_id=person_id,
                    person_name=person_name,
                    score=best_score,
                    smile_score=smile_score,
                )
            )

    global _PERSON_TOP_K
    if _PERSON_TOP_K is None:
        _PERSON_TOP_K = 3

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:_PERSON_TOP_K]


def _select_best_unique_matches(candidates_by_signature: list[list[PersonMatch]]) -> list[PersonMatch]:
    if not candidates_by_signature:
        return []

    flattened_candidates: list[tuple[float, int, int, PersonMatch]] = []

    for signature_index, candidates in enumerate(candidates_by_signature):
        best_for_signature: dict[int, PersonMatch] = {}
        for match in candidates:
            current = best_for_signature.get(match.person_id)
            if current is None or match.score > current.score:
                best_for_signature[match.person_id] = match
        for match in best_for_signature.values():
            flattened_candidates.append((match.score, signature_index, match.person_id, match))

    selected: list[PersonMatch] = []
    used_signatures: set[int] = set()
    used_person_ids: set[int] = set()

    for _score, signature_index, person_id, match in sorted(
        flattened_candidates,
        key=lambda item: (-item[0], item[1], item[2]),
    ):
        if signature_index in used_signatures or person_id in used_person_ids:
            continue
        used_signatures.add(signature_index)
        used_person_ids.add(person_id)
        if match.score > 0:
            selected.append(match)

    selected.sort(key=lambda item: item.score, reverse=True)
    return selected


def enroll_person(
    db_path: Path,
    person_name: str,
    root: Path,
    supported_extensions: tuple[str, ...],
    preferred_backend: str | None = None,
) -> EnrollResult:
    images = scan_images(root=root, supported_extensions=supported_extensions)

    return enroll_person_from_paths(
        db_path=db_path,
        person_name=person_name,
        image_paths=[image_record.path for image_record in images],
        preferred_backend=preferred_backend,
    )


def enroll_person_from_paths(
    db_path: Path,
    person_name: str,
    image_paths: list[Path],
    preferred_backend: str | None = None,
    strict_backend: bool = False,
) -> EnrollResult:
    backend = resolve_backend(preferred_backend, strict=strict_backend)
    unique_image_paths = list(dict.fromkeys(path for path in image_paths))
    source_vectors: list[tuple[str, list[float]]] = []
    backend_name = backend.name

    for image_path in unique_image_paths:
        used_backend_name, signatures, _box_count = extract_person_signatures(
            image_path,
            preferred_backend=preferred_backend,
            strict_backend=strict_backend,
        )
        backend_name = used_backend_name
        for signature, _smile_score in signatures:
            source_vectors.append((str(image_path), signature))

    if not source_vectors:
        raise ValueError(
            "Keine verwendbaren Personen-Samples gefunden. Nutze Fotos mit gut sichtbaren Personen."
        )

    person_id = upsert_person(db_path=db_path, name=person_name)
    vector_dim = len(source_vectors[0][1])
    replace_person_references(
        db_path=db_path,
        person_id=person_id,
        source_vectors=source_vectors,
        backend=backend_name,
        vector_dim=vector_dim,
    )

    return EnrollResult(
        person_name=person_name,
        backend=backend_name,
        image_count=len(unique_image_paths),
        sample_count=len(source_vectors),
    )


def match_persons_for_photo(
    db_path: Path,
    photo_path: Path,
    preferred_backend: str | None = None,
) -> tuple[list[PersonMatch], int]:
    """Gibt (matches, person_box_count) zurück."""
    backend_name, signatures, person_count = extract_person_signatures(
        photo_path,
        preferred_backend=preferred_backend,
    )
    if not signatures:
        return [], person_count

    refs = list_person_references(db_path, backend_filter=backend_name)
    if not refs:
        return [], person_count

    refs_by_person = _group_references_by_person(refs)
    candidates_by_signature = [
        _score_signature_against_references(signature, refs_by_person, smile_score=smile_score)
        for signature, smile_score in signatures
    ]

    matches = _select_best_unique_matches(candidates_by_signature)
    global _PERSON_TOP_K
    if _PERSON_TOP_K is None:
        _PERSON_TOP_K = 3
    return matches[:_PERSON_TOP_K], person_count


def persist_matches_for_photo(db_path: Path, photo_path: Path, matches: list[PersonMatch]) -> None:
    replace_photo_person_matches(
        db_path=db_path,
        photo_path=str(photo_path),
        matches=[(match.person_id, match.score, match.smile_score) for match in matches],
    )


def search_person_photos(db_path: Path, person_name: str, limit: int = 20, max_persons: int | None = None) -> list[PersonPhotoHit]:
    if limit <= 0:
        return []
    return search_photos_by_person_name(db_path=db_path, person_name=person_name, limit=limit, max_persons=max_persons)

