from __future__ import annotations

import re
import unicodedata


_CHAR_TRANSLATION = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ئ": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "ؤ": "و",
        "\u200f": " ",
        "\u200e": " ",
        "\ufeff": " ",
    }
)

# Keep numeral presentation in one place.  The storage layer intentionally
# retains machine-readable numbers, while publication renderers call this
# helper at their final display boundary.
_PERSIAN_DIGIT_TRANSLATION = str.maketrans(
    "0123456789٠١٢٣٤٥٦٧٨٩",
    "۰۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹",
)


def to_persian_digits(value: object | None) -> str:
    """Return *value* with Latin and Arabic-Indic numerals shown in Persian.

    It is deliberately presentation-only: IDs and numeric values in the
    database/API remain unchanged, while every visible publication string can
    consistently use Persian numerals.
    """

    return str(value or "").translate(_PERSIAN_DIGIT_TRANSLATION)


def normalize_persian(
    value: str | None,
    *,
    remove_noise: bool = False,
) -> str:
    """Normalize Arabic/Persian glyph variants without changing meaning."""
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_CHAR_TRANSLATION)
    if remove_noise:
        text = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]", " ", text)
        text = re.sub(r"(?:https?://\S+|www\.\S+)", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"[#@][^\s،؛,.!?؟]+", " ", text)
    text = re.sub(r"[\t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def canonical_key(value: str | None) -> str:
    text = normalize_persian(value).replace("\u200c", " ").lower()
    text = re.sub(r"[^\wآ-ی]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def split_sentences(value: str | None) -> list[str]:
    text = normalize_persian(value)
    if not text:
        return []
    parts = re.split(r"(?<=[.!؟?؛])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def keywords(value: str | None, *, min_length: int = 2) -> set[str]:
    stop = {
        "از",
        "به",
        "در",
        "با",
        "برای",
        "که",
        "این",
        "آن",
        "را",
        "و",
        "یا",
        "است",
        "شد",
        "می",
        "یک",
        "بر",
        "تا",
        "هم",
    }
    return {
        word
        for word in canonical_key(value).split()
        if len(word) >= min_length and word not in stop
    }
