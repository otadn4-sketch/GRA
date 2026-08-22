"""Shared near-duplicate scoring for analysis, ingest, and the crawler send guard."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Sequence

from .persian_text import keywords, normalize_persian

ANALYSIS_NEAR_DUPLICATE_THRESHOLD = 0.80


def message_body(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("text") or row.get("caption") or "").strip()


def text_sha256(value: str | None) -> str | None:
    normalized = normalize_persian(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def keyword_jaccard(left: str | None, right: str | None) -> float:
    left_words, right_words = keywords(left), keywords(right)
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def is_near_duplicate(
    left: str | None,
    right: str | None,
    *,
    threshold: float = ANALYSIS_NEAR_DUPLICATE_THRESHOLD,
) -> bool:
    if text_sha256(left) and text_sha256(left) == text_sha256(right):
        return True
    return keyword_jaccard(left, right) >= threshold


def _sort_key(item: dict[str, Any]) -> tuple[int, str, int]:
    text = message_body(item)
    published = str(item.get("published_at") or item.get("received_at") or item.get("created_at") or "")
    return (-len(text), published, int(item.get("id") or 0))


def choose_representatives(
    items: Sequence[dict[str, Any]],
    *,
    threshold: float = ANALYSIS_NEAR_DUPLICATE_THRESHOLD,
    existing: Iterable[dict[str, Any]] = (),
) -> tuple[list[int], dict[int, int]]:
    """Pick one representative per near-duplicate cluster.

    Representatives prefer the longest text, then the earliest timestamp.
    ``duplicate_of`` maps skipped ids onto the kept representative id.  Items
    in ``existing`` are already claimed (recent corpus / previously sent news)
    and never re-analyzed; a new item that matches one becomes a duplicate of
    that existing row.
    """

    claimed: list[dict[str, Any]] = []
    for row in existing:
        body = message_body(row)
        if not body:
            continue
        claimed.append(row)

    duplicate_of: dict[int, int] = {}
    representatives: list[int] = []
    for item in sorted(items, key=_sort_key):
        item_id = int(item["id"])
        body = message_body(item)
        matched: dict[str, Any] | None = None
        for previous in claimed:
            previous_body = message_body(previous)
            if not body or not previous_body:
                continue
            if is_near_duplicate(body, previous_body, threshold=threshold):
                matched = previous
                break
        if matched is not None:
            duplicate_of[item_id] = int(matched.get("duplicate_of") or matched["id"])
            continue
        representatives.append(item_id)
        claimed.append(item)
    return representatives, duplicate_of
