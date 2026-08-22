"""Keep registry speaker binding attached to spans that actually appear in the text."""

from __future__ import annotations

import re
from typing import Iterable

from .persian_text import canonical_key, normalize_persian

TITLE_TOKENS = {
    "آقای",
    "خانم",
    "جناب",
    "دکتر",
    "مهندس",
    "سردار",
    "سرلشکر",
    "دریادار",
    "سرهنگ",
    "وزیر",
    "وزیران",
    "معاون",
    "رئیس",
    "رییس",
    "نماینده",
    "سخنگو",
    "استاندار",
    "فرمانده",
    "مدیر",
    "دبیر",
    "امام",
    "حجت",
    "الاسلام",
    "آیت",
    "الله",
    "جمهور",
    "مجلس",
    "قوه",
    "قضائیه",
    "مجریه",
    "مقننه",
    "کشور",
    "خارجه",
    "نیرو",
    "اقتصاد",
    "راه",
    "شهرسازی",
    "کابینه",
    "دولت",
    "سپاه",
    "ارتش",
    "نیروی",
    "انتظامی",
    "ملی",
    "اسلامی",
    "جمهوری",
    "ایران",
}

_WHITESPACE = re.compile(r"\s+")


def phrase_tokens(value: str | None) -> list[str]:
    key = canonical_key(value)
    return key.split() if key else []


def is_title_only_name(value: str | None) -> bool:
    tokens = phrase_tokens(value)
    if not tokens:
        return True
    return all(token in TITLE_TOKENS or len(token) < 2 for token in tokens)


def contains_phrase(haystack: str | None, needle: str | None) -> bool:
    """True when *needle* appears as contiguous whole words in *haystack*."""

    need = phrase_tokens(needle)
    hay = phrase_tokens(haystack)
    if not need or not hay:
        return False
    size = len(need)
    return any(hay[index : index + size] == need for index in range(len(hay) - size + 1))


def contains_excerpt(haystack: str | None, needle: str | None) -> bool:
    excerpt = _WHITESPACE.sub(" ", normalize_persian(needle)).strip()
    text = _WHITESPACE.sub(" ", normalize_persian(haystack)).strip()
    if excerpt and excerpt in text:
        return True
    return contains_phrase(haystack, needle)


def grounding_span(
    text: str | None,
    *,
    extracted_name: str | None,
    full_name: str | None = None,
    aliases: Iterable[str] | None = None,
) -> str | None:
    """Return the first name/alias that actually appears in the message."""

    candidates: list[str] = []
    for raw in (extracted_name, full_name, *(aliases or [])):
        value = normalize_persian(raw).strip()
        if not value or value == "نامشخص" or is_title_only_name(value):
            continue
        if value not in candidates:
            candidates.append(value)
    for value in candidates:
        if contains_phrase(text, value):
            return value
    return None


def evidence_supports_name(text: str | None, evidence: str | None, name: str | None) -> bool:
    excerpt = normalize_persian(evidence).strip()
    if not excerpt:
        return True
    if not contains_excerpt(text, excerpt):
        return False
    return contains_phrase(excerpt, name)


def may_bind_short_name(extracted_name: str | None, grounded_span: str | None) -> bool:
    """Single-token names bind only when that exact token is the grounded span."""

    tokens = phrase_tokens(extracted_name)
    if len(tokens) >= 2:
        return True
    if len(tokens) != 1:
        return False
    grounded = phrase_tokens(grounded_span)
    return grounded == tokens
