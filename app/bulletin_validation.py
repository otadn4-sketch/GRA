from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .bulletin_models import BulletinData, QualityIssue

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NUMBER_RE = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?(?:\s*(?:درصد|میلیون|میلیارد|هزار|تومان|ریال|دلار|یورو|مترمکعب|تن|کیلومتر|مگاوات|سال|ماه|روز))?")


def normalize_digits(text: str) -> str:
    return str(text or "").translate(_DIGITS).replace("٬", ",").replace("٫", ".")


def extract_numbers(text: str) -> set[str]:
    clean = normalize_digits(text)
    return {re.sub(r"\s+", " ", m.group(0).strip()) for m in _NUMBER_RE.finditer(clean)}


def valid_public_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(str(url).strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        return False
    return True


def is_generic_url(url: str | None) -> bool:
    if not valid_public_url(url):
        return True
    parsed = urlparse(str(url))
    return parsed.path in {"", "/"} and not parsed.query and not parsed.fragment


def _all_statements(data: BulletinData):
    for category in data.categories:
        for person in category.people:
            for statement in person.statements:
                yield category, person, statement


def validate_bulletin_data(data: BulletinData) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    seen_statement_ids: set[str] = set()
    seen_numbers: set[int] = set()
    seen_person_ids: set[int] = set()
    seen_person_names: set[str] = set()

    for category in data.categories:
        if not category.people:
            issues.append(QualityIssue(
                code="empty_category", severity="warning", object_type="category", object_id=category.category_id,
                message=f"دسته «{category.title}» خالی است و نباید در Word نمایش داده شود.",
            ))
        for person in category.people:
            if not person.name_canonical.strip():
                issues.append(QualityIssue(
                    code="missing_person_name", severity="critical", object_type="person", object_id=str(person.person_id),
                    message="نام استاندارد شخص خالی است.",
                ))
            canonical_key = re.sub(r"\s+", " ", person.name_canonical.strip().replace("ي", "ی").replace("ك", "ک")).lower()
            if person.person_id is not None and int(person.person_id) in seen_person_ids:
                issues.append(QualityIssue(
                    code="duplicate_canonical_person_id", severity="critical", object_type="person", object_id=str(person.person_id),
                    message="یک شخص ثبت‌شده با چند نام یا در چند دسته عمومی تکرار شده است.",
                ))
            if person.person_id is not None:
                seen_person_ids.add(int(person.person_id))
            if canonical_key in seen_person_names:
                issues.append(QualityIssue(
                    code="duplicate_canonical_person_name", severity="critical", object_type="person", object_id=str(person.person_id),
                    message=f"نام معیار شخص در بولتن تکرار شده است: {person.name_canonical}",
                ))
            seen_person_names.add(canonical_key)

            if person.continuous_number in seen_numbers:
                issues.append(QualityIssue(
                    code="duplicate_person_number", severity="critical", object_type="person", object_id=str(person.person_id),
                    message=f"شماره پیوسته شخص تکراری است: {person.continuous_number}",
                ))
            seen_numbers.add(person.continuous_number)

    expected_numbers = set(range(1, len(seen_numbers) + 1))
    if seen_numbers and seen_numbers != expected_numbers:
        issues.append(QualityIssue(
            code="broken_person_numbering", severity="critical", object_type="bulletin",
            message="شماره‌گذاری اشخاص پیوسته نیست.", details={"actual": sorted(seen_numbers)},
        ))

    for _category, person, statement in _all_statements(data):
        if statement.statement_id in seen_statement_ids:
            issues.append(QualityIssue(
                code="duplicate_statement_id", severity="critical", object_type="statement", object_id=statement.statement_id,
                message="شناسه اظهار در بیش از یک آیتم استفاده شده است.",
            ))
        seen_statement_ids.add(statement.statement_id)
        if not statement.source_ids or not statement.source_records:
            issues.append(QualityIssue(
                code="missing_evidence", severity="critical", object_type="statement", object_id=statement.statement_id,
                message=f"اظهار «{statement.topic}» برای {person.name_canonical} فاقد رکورد شاهد است.",
            ))
        source_text = " ".join(x.text or "" for x in statement.source_records)
        source_numbers = extract_numbers(source_text)
        summary_numbers = extract_numbers(statement.summary_short + " " + statement.summary_detailed)
        unsupported = sorted(x for x in summary_numbers if x not in source_numbers)
        if unsupported:
            issues.append(QualityIssue(
                code="unsupported_number", severity="critical", object_type="statement", object_id=statement.statement_id,
                message="عدد یا واحدی در خلاصه وجود دارد که در شواهد پیدا نشد.",
                details={"numbers": unsupported, "source_numbers": sorted(source_numbers)},
            ))
        public_text = " ".join((
            statement.headline, statement.summary_lead, statement.summary_body,
            statement.summary_short, statement.summary_detailed,
        ))
        forbidden = [
            phrase for phrase in (
                "محل بیان نامشخص", "در پیام ارائه‌شده هیچ", "در پیام ارائه شده هیچ",
                "هیچ اظهارنظر مستقیمی", "هیچ اظهار نظر مستقیمی", "متن صرفاً به", "متن صرفا به",
            )
            if phrase in public_text
        ]
        if statement.include_in_main and forbidden:
            issues.append(QualityIssue(
                code="public_meta_text", severity="critical", object_type="statement", object_id=statement.statement_id,
                message="عبارت فنی یا ممیزی وارد متن عمومی شده است.", details={"phrases": forbidden},
            ))
        if statement.include_in_main and not (statement.headline or statement.summary_short):
            issues.append(QualityIssue(
                code="missing_public_headline", severity="warning", object_type="statement", object_id=statement.statement_id,
                message="اظهار عمومی عنوان یا خلاصه قابل نمایش ندارد.",
            ))
        if statement.include_in_main and statement.public_exclusion_reason:
            issues.append(QualityIssue(
                code="excluded_statement_marked_public", severity="critical", object_type="statement", object_id=statement.statement_id,
                message="اظهاری که برای خروجی عمومی نامعتبر تشخیص داده شده همچنان قابل انتشار است.",
                details={"reason": statement.public_exclusion_reason},
            ))

        if statement.has_source_url:
            if not valid_public_url(statement.source_url):
                issues.append(QualityIssue(
                    code="invalid_source_url", severity="warning", object_type="statement", object_id=statement.statement_id,
                    message="لینک منبع نامعتبر است و QR نباید نمایش داده شود.", details={"url": statement.source_url},
                ))
            elif is_generic_url(statement.source_url):
                issues.append(QualityIssue(
                    code="generic_source_url", severity="warning", object_type="statement", object_id=statement.statement_id,
                    message="لینک به صفحه عمومی منبع اشاره دارد، نه لزوماً همان اظهار.", details={"url": statement.source_url},
                ))
        if statement.show_qr_in_bulletin:
            if not valid_public_url(statement.editorial_source_url) or is_generic_url(statement.editorial_source_url):
                issues.append(QualityIssue(
                    code="missing_editorial_qr_link", severity="warning", object_type="statement", object_id=statement.statement_id,
                    message="QR فقط با لینک نهایی ثبت‌شده در میز تدوین قابل انتشار است.",
                    details={"editorial_source_url": statement.editorial_source_url},
                ))
            elif not statement.qr_code_path:
                issues.append(QualityIssue(
                    code="missing_qr_path", severity="warning", object_type="statement", object_id=statement.statement_id,
                    message="نمایش QR فعال است اما مسیر تصویر ثبت نشده است.",
                ))
            elif not Path(statement.qr_code_path).exists():
                issues.append(QualityIssue(
                    code="missing_qr_file", severity="warning", object_type="statement", object_id=statement.statement_id,
                    message="فایل QR در مسیر ثبت‌شده وجود ندارد.", details={"path": statement.qr_code_path},
                ))
        if statement.include_in_main and statement.importance_score < 0:
            issues.append(QualityIssue(
                code="invalid_importance", severity="critical", object_type="statement", object_id=statement.statement_id,
                message="امتیاز اهمیت نامعتبر است.",
            ))

    for controversy in data.controversies:
        unknown = [sid for sid in controversy.statement_ids if sid not in seen_statement_ids]
        if unknown:
            issues.append(QualityIssue(
                code="controversy_missing_statement", severity="critical", object_type="controversy", object_id=controversy.title,
                message="محور پربازتاب به اظهار ناموجود ارجاع داده است.", details={"statement_ids": unknown},
            ))

    return issues


def has_critical(issues: Iterable[QualityIssue]) -> bool:
    return any(issue.severity == "critical" for issue in issues)
