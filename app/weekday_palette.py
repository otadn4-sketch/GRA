"""Weekday visual identity for Garaye bulletin outputs.

Palette source: راهنمای_نهایی_پالت_رنگی_روزهای_هفته_2.pdf
Each weekday has five levels: dark, main, accent, light, ground.
"""

from __future__ import annotations

from typing import Any

import jdatetime

from .persian_text import normalize_persian


WEEKDAY_PALETTES: dict[int, dict[str, str]] = {
    # jdatetime.weekday(): Saturday=0 … Friday=6
    0: {  # شنبه — شفقی
        "name": "شنبه",
        "label": "شفقی",
        "dark": "#783B31",
        "main": "#B85C4A",
        "accent": "#D58A79",
        "light": "#EBC4BA",
        "ground": "#FAF3F1",
    },
    1: {  # یکشنبه — یشمی
        "name": "یکشنبه",
        "label": "یشمی",
        "dark": "#174B39",
        "main": "#287A5B",
        "accent": "#65A087",
        "light": "#CBE1D6",
        "ground": "#F2F7F4",
    },
    2: {  # دوشنبه — دودی
        "name": "دوشنبه",
        "label": "دودی",
        "dark": "#3E4852",
        "main": "#687582",
        "accent": "#98A3AD",
        "light": "#D8DEE3",
        "ground": "#F5F7F8",
    },
    3: {  # سه‌شنبه — سرمه‌ای
        "name": "سه‌شنبه",
        "label": "سرمه‌ای",
        "dark": "#1B2E4D",
        "main": "#304B78",
        "accent": "#687FA5",
        "light": "#CCD5E4",
        "ground": "#F2F4F8",
    },
    4: {  # چهارشنبه — چوبی
        "name": "چهارشنبه",
        "label": "چوبی",
        "dark": "#593B24",
        "main": "#95683F",
        "accent": "#BE9570",
        "light": "#E2D0BF",
        "ground": "#F8F4F0",
    },
    5: {  # پنج‌شنبه — پرتقالی
        "name": "پنج‌شنبه",
        "label": "پرتقالی",
        "dark": "#7A420E",
        "main": "#C87822",
        "accent": "#DEA154",
        "light": "#F0D1A5",
        "ground": "#FCF6ED",
    },
    6: {  # جمعه — جگری
        "name": "جمعه",
        "label": "جگری",
        "dark": "#431F2A",
        "main": "#713747",
        "accent": "#9B6574",
        "light": "#DFC7CE",
        "ground": "#F7F1F3",
    },
}

NEUTRAL_INK = "#20252B"
NEUTRAL_MUTED = "#5F6872"
NEUTRAL_LINE = "#D8DDE2"
NEUTRAL_TABLE = "#F4F5F6"


def _parse_jalali_date(value: str | None) -> jdatetime.date | None:
    text = normalize_persian(str(value or "")).strip()
    if not text:
        return None
    # Accept 1405/05/15, 1405-05-15, optionally with time.
    head = text.split()[0].replace("-", "/").replace(".", "/")
    parts = [part for part in head.split("/") if part]
    if len(parts) < 3:
        return None
    try:
        year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
        return jdatetime.date(year, month, day)
    except (TypeError, ValueError):
        return None


def weekday_palette_for_report(report_date_jalali: str | None) -> dict[str, Any]:
    """Return the weekday palette for a Jalali report date, defaulting to Saturday."""

    parsed = _parse_jalali_date(report_date_jalali)
    weekday = parsed.weekday() if parsed is not None else 0
    palette = dict(WEEKDAY_PALETTES.get(weekday, WEEKDAY_PALETTES[0]))
    palette["weekday"] = weekday
    palette["ink"] = NEUTRAL_INK
    palette["muted"] = NEUTRAL_MUTED
    palette["line"] = NEUTRAL_LINE
    palette["table"] = NEUTRAL_TABLE
    return palette
