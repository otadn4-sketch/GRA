"""Calendar labels for bulletin covers: Jalali, Hijri (lunar) and Gregorian."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import jdatetime

from .persian_text import normalize_persian, to_persian_digits


HIJRI_MONTHS = (
    "محرم",
    "صفر",
    "ربیع‌الاول",
    "ربیع‌الثانی",
    "جمادی‌الاول",
    "جمادی‌الثانیه",
    "رجب",
    "شعبان",
    "رمضان",
    "شوال",
    "ذی‌القعده",
    "ذی‌الحجه",
)
GREGORIAN_MONTHS_FA = (
    "ژانویه",
    "فوریه",
    "مارس",
    "آوریل",
    "مه",
    "ژوئن",
    "ژوئیه",
    "اوت",
    "سپتامبر",
    "اکتبر",
    "نوامبر",
    "دسامبر",
)
JALALI_WEEKDAYS = (
    "شنبه",
    "یکشنبه",
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنج‌شنبه",
    "جمعه",
)


def _parse_jalali_date(value: str | None) -> jdatetime.date | None:
    text = normalize_persian(str(value or "")).strip()
    if not text:
        return None
    head = text.split()[0].replace("-", "/").replace(".", "/")
    parts = [part for part in head.split("/") if part]
    if len(parts) < 3:
        return None
    try:
        return jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (TypeError, ValueError):
        return None


def gregorian_to_hijri(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Kuwaiti algorithm: civil Hijri date from a Gregorian Y-M-D."""

    if month < 3:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    jd = int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524
    l = jd - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = ((10985 - l) // 5316) * ((50 * l) // 17719) + (l // 5670) * ((43 * l) // 15238)
    l = l - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    hijri_month = (24 * l) // 709
    hijri_day = l - (709 * hijri_month) // 24
    hijri_year = 30 * n + j - 30
    return hijri_year, hijri_month, hijri_day


def report_calendar_labels(
    report_date_jalali: str | None,
    *,
    timezone_name: str = "Asia/Tehran",
) -> dict[str, Any]:
    """Return printable Jalali, Hijri and Gregorian labels for a report date."""

    parsed = _parse_jalali_date(report_date_jalali)
    if parsed is None:
        local = datetime.now(ZoneInfo(timezone_name)).date()
        parsed = jdatetime.date.fromgregorian(date=local)
    gregorian = parsed.togregorian()
    if isinstance(gregorian, datetime):
        gregorian_date = gregorian.date()
    elif isinstance(gregorian, date):
        gregorian_date = gregorian
    else:
        gregorian_date = date.today()
    hy, hm, hd = gregorian_to_hijri(gregorian_date.year, gregorian_date.month, gregorian_date.day)
    jalali_iso = f"{parsed.year:04d}/{parsed.month:02d}/{parsed.day:02d}"
    hijri_iso = f"{hy:04d}/{hm:02d}/{hd:02d}"
    gregorian_iso = f"{gregorian_date.year:04d}/{gregorian_date.month:02d}/{gregorian_date.day:02d}"
    weekday = JALALI_WEEKDAYS[parsed.weekday() % 7]
    return {
        "weekday": weekday,
        "jalali": jalali_iso,
        "jalali_label": to_persian_digits(f"{weekday} {jalali_iso}"),
        "hijri": hijri_iso,
        "hijri_label": to_persian_digits(f"{hd} {HIJRI_MONTHS[max(0, min(11, hm - 1))]} {hy}"),
        "gregorian": gregorian_iso,
        "gregorian_label": to_persian_digits(
            f"{gregorian_date.day} {GREGORIAN_MONTHS_FA[gregorian_date.month - 1]} {gregorian_date.year}"
        ),
    }
