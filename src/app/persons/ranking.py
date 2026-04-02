from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .embeddings import InsightFaceBackend, resolve_backend
from .service import match_persons_for_photo


@dataclass(frozen=True)
class AgingSelectionResult:
    photo_paths: list[str]
    considered_count: int
    used_gpu: bool


@dataclass
class _Candidate:
    path: str
    sort_ts: float
    db_score: float
    smile_score: float
    person_count: int
    score: float


def _base_score(db_score: float, smile_score: float, person_count: int) -> float:
    solo_bonus = 0.10 if person_count == 1 else 0.0
    return db_score * 0.75 + smile_score * 0.15 + solo_bonus


def _load_candidates(db_path: Path, person_name: str) -> list[_Candidate]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                m.photo_path,
                COALESCE(ph.taken_ts, ph.modified_ts, 0) AS sort_ts,
                COALESCE(m.score, 0),
                COALESCE(m.smile_score, 0),
                COALESCE(ph.person_count, 0),
                ph.duplicate_kind,
                ph.duplicate_of_path
            FROM photo_person_matches m
            JOIN persons p ON p.id = m.person_id
            LEFT JOIN photos ph ON ph.path = m.photo_path
            WHERE lower(p.name) = lower(?)
            ORDER BY COALESCE(m.score, 0) DESC, sort_ts ASC
            """,
            (person_name.strip(),),
        ).fetchall()

    candidates: list[_Candidate] = []
    for row in rows:
        duplicate_kind = str(row[5]).lower() if row[5] is not None else ""
        duplicate_of = str(row[6]) if row[6] is not None else ""
        if duplicate_kind in {"exact", "near"} and duplicate_of:
            continue

        path = str(row[0])
        if not path:
            continue
        score = _base_score(float(row[2]), float(row[3]), int(row[4]))
        candidates.append(
            _Candidate(
                path=path,
                sort_ts=float(row[1]),
                db_score=float(row[2]),
                smile_score=float(row[3]),
                person_count=int(row[4]),
                score=score,
            )
        )
    return candidates


def _year_bucket(ts: float) -> int:
    if ts <= 0:
        return 0
    try:
        return _dt.datetime.fromtimestamp(ts).year
    except Exception:
        return 0


def _diversify_by_year(candidates: list[_Candidate], max_photos: int) -> list[str]:
    if max_photos <= 0:
        return []

    buckets: dict[int, list[_Candidate]] = {}
    for candidate in candidates:
        year = _year_bucket(candidate.sort_ts)
        buckets.setdefault(year, []).append(candidate)

    for year in buckets:
        buckets[year].sort(key=lambda item: item.score, reverse=True)

    ordered_years = sorted(buckets.keys())
    selected: list[str] = []
    seen: set[str] = set()

    while len(selected) < max_photos:
        added = 0
        for year in ordered_years:
            items = buckets.get(year, [])
            while items and items[0].path in seen:
                items.pop(0)
            if not items:
                continue
            pick = items.pop(0)
            seen.add(pick.path)
            selected.append(pick.path)
            added += 1
            if len(selected) >= max_photos:
                break
        if added == 0:
            break

    return selected


def _pick_by_quality(candidates: list[_Candidate], max_photos: int) -> list[str]:
    if max_photos <= 0:
        return []
    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    return [item.path for item in ranked[:max_photos]]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _select_with_bias(candidates: list[_Candidate], max_photos: int, quality_bias: float) -> list[str]:
    quality_first = _pick_by_quality(candidates, max_photos=max_photos)
    diverse_first = _diversify_by_year(candidates, max_photos=max_photos)

    # 0.0 = moeglichst divers, 1.0 = moeglichst qualitaetsgetrieben
    bias = float(_clamp(quality_bias, 0.0, 1.0))
    quality_take = int(round(max_photos * (0.35 + 0.65 * bias)))

    selected: list[str] = []
    seen: set[str] = set()

    for path in quality_first:
        if len(selected) >= quality_take:
            break
        if path in seen:
            continue
        selected.append(path)
        seen.add(path)

    for path in diverse_first:
        if len(selected) >= max_photos:
            break
        if path in seen:
            continue
        selected.append(path)
        seen.add(path)

    if len(selected) < max_photos:
        for path in quality_first:
            if len(selected) >= max_photos:
                break
            if path in seen:
                continue
            selected.append(path)
            seen.add(path)

    return selected


def select_aging_timelapse_photo_paths(
    db_path: Path,
    person_name: str,
    max_photos: int = 80,
    prefer_gpu: bool = True,
    strict_gpu: bool = False,
    quality_bias: float = 0.5,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> AgingSelectionResult:
    safe_max = max(2, min(int(max_photos), 300))
    candidates = _load_candidates(db_path, person_name)
    if not candidates:
        return AgingSelectionResult(photo_paths=[], considered_count=0, used_gpu=False)

    def _progress(step: int, total: int, msg: str) -> None:
        if progress_cb:
            progress_cb(step, total, msg)

    candidates.sort(key=lambda item: item.score, reverse=True)

    used_gpu = False
    if prefer_gpu:
        # strict_gpu=True erzwingt InsightFace-Verfügbarkeit; sonst stilles Fallback.
        used_gpu = isinstance(resolve_backend("insightface", strict=strict_gpu), InsightFaceBackend)

        if used_gpu:
            pool = candidates[: min(len(candidates), safe_max * 3)]
            _progress(1, max(2, len(pool) + 1), "GPU-Refinement ueber InsightFace ...")
            for index, candidate in enumerate(pool, start=1):
                path = Path(candidate.path)
                if not path.exists():
                    continue
                matches, _ = match_persons_for_photo(
                    db_path=db_path,
                    photo_path=path,
                    preferred_backend="insightface",
                )
                best = 0.0
                for match in matches:
                    if match.person_name.lower() == person_name.lower():
                        best = max(best, float(match.score))
                if best > 0:
                    candidate.score = candidate.score * 0.70 + best * 0.30
                _progress(index + 1, max(2, len(pool) + 1), f"GPU-Score: {path.name}")

            candidates.sort(key=lambda item: item.score, reverse=True)

    selected = _select_with_bias(candidates, safe_max, quality_bias=quality_bias)
    return AgingSelectionResult(photo_paths=selected, considered_count=len(candidates), used_gpu=used_gpu)

