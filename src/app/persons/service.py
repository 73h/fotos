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

_PERSON_THRESHOLD = float(os.getenv("FOTOS_PERSON_THRESHOLD", "0.90"))
_PERSON_TOP_K = int(os.getenv("FOTOS_PERSON_TOP_K", "3"))
_USE_FULL_IMAGE_FALLBACK = os.getenv("FOTOS_PERSON_FULL_IMAGE_FALLBACK", "1") == "1"


@dataclass(frozen=True)
class PersonMatch:
    person_id: int
    person_name: str
    score: float


@dataclass(frozen=True)
class EnrollResult:
    person_name: str
    backend: str
    image_count: int
    sample_count: int


def extract_person_signatures(
    photo_path: Path,
    preferred_backend: str | None = None,
) -> tuple[str, list[list[float]]]:
    if not photo_path.exists():
        return ("unknown", [])

    backend = resolve_backend(preferred_backend)

    signatures: list[list[float]] = []
    try:
        with Image.open(photo_path) as image:
            boxes = yolo_labels.detect_person_boxes(photo_path)
            for x1, y1, x2, y2 in boxes:
                crop = image.crop((x1, y1, x2, y2))
                vector = backend.vector_from_image(crop)
                if vector is not None:
                    signatures.append(vector)

            if not signatures and _USE_FULL_IMAGE_FALLBACK:
                vector = backend.vector_from_image(image)
                if vector is not None:
                    signatures.append(vector)
    except Exception:
        return (backend.name, [])

    return (backend.name, signatures)


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
) -> list[PersonMatch]:
    scored: list[PersonMatch] = []
    for (person_id, person_name), vectors in references_by_person.items():
        if not vectors:
            continue

        best_score = max(cosine_similarity(signature, reference) for reference in vectors)
        if best_score >= _PERSON_THRESHOLD:
            scored.append(PersonMatch(person_id=person_id, person_name=person_name, score=best_score))

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:_PERSON_TOP_K]


def enroll_person(
    db_path: Path,
    person_name: str,
    root: Path,
    supported_extensions: tuple[str, ...],
    preferred_backend: str | None = None,
) -> EnrollResult:
    backend = resolve_backend(preferred_backend)
    images = scan_images(root=root, supported_extensions=supported_extensions)
    source_vectors: list[tuple[str, list[float]]] = []
    backend_name = backend.name

    for image_record in images:
        used_backend_name, signatures = extract_person_signatures(
            image_record.path,
            preferred_backend=preferred_backend,
        )
        backend_name = used_backend_name
        for signature in signatures:
            source_vectors.append((str(image_record.path), signature))

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
        image_count=len(images),
        sample_count=len(source_vectors),
    )


def match_persons_for_photo(
    db_path: Path,
    photo_path: Path,
    preferred_backend: str | None = None,
) -> list[PersonMatch]:
    backend_name, signatures = extract_person_signatures(
        photo_path,
        preferred_backend=preferred_backend,
    )
    if not signatures:
        return []

    refs = list_person_references(db_path, backend_filter=backend_name)
    if not refs:
        return []

    refs_by_person = _group_references_by_person(refs)
    best_by_person: dict[int, PersonMatch] = {}

    for signature in signatures:
        for match in _score_signature_against_references(signature, refs_by_person):
            current = best_by_person.get(match.person_id)
            if current is None or match.score > current.score:
                best_by_person[match.person_id] = match

    matches = sorted(best_by_person.values(), key=lambda item: item.score, reverse=True)
    return matches[:_PERSON_TOP_K]


def persist_matches_for_photo(db_path: Path, photo_path: Path, matches: list[PersonMatch]) -> None:
    replace_photo_person_matches(
        db_path=db_path,
        photo_path=str(photo_path),
        matches=[(match.person_id, match.score) for match in matches],
    )


def search_person_photos(db_path: Path, person_name: str, limit: int = 20) -> list[PersonPhotoHit]:
    if limit <= 0:
        return []
    return search_photos_by_person_name(db_path=db_path, person_name=person_name, limit=limit)

