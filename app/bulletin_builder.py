from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import jdatetime
import qrcode

from .bulletin_models import (
    CATEGORY_ORDER,
    PERSIAN_SECTION_LETTERS,
    BulletinCategory,
    BulletinControversy,
    BulletinData,
    BulletinMeta,
    BulletinPerson,
    BulletinStatement,
    ControversySide,
    MonitoredPerson,
    MonitoredSource,
    QualityControl,
    StatementSource,
    category_key,
    category_section_title,
    category_title,
    is_event_category,
)
from .bulletin_validation import is_generic_url, valid_public_url
from .config import PROJECT_ROOT, Settings
from .db import Database
from .persian_text import normalize_persian, split_sentences

_POSITIVE = ("حمایت", "تأیید", "موافق", "ضروری", "استقبال", "دفاع", "مثبت", "توافق")
_NEGATIVE = ("مخالفت", "رد کرد", "انتقاد", "هشدار", "غلط", "نامناسب", "مضر", "محکوم", "اعتراض")
_ROUTINE = ("دیدار کرد", "حضور یافت", "تبریک گفت", "تسلیت گفت", "افتتاح شد", "بازدید کرد")
_POLICY = ("قانون", "سیاست", "بودجه", "اصلاح", "پیشنهاد", "برنامه", "آیین‌نامه", "تصمیم", "راهبرد")
_SPECIFIC = ("باید", "نباید", "پیشنهاد", "راهکار", "ضروری", "الزام", "تصویب", "اجرا")


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value)) if value not in (None, "") else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _jalali(value: Any, timezone_name: str, *, with_time: bool = True) -> str | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    local = dt.astimezone(ZoneInfo(timezone_name))
    jd = jdatetime.datetime.fromgregorian(datetime=local)
    return jd.strftime("%Y/%m/%d %H:%M:%S" if with_time else "%Y/%m/%d")


def _source_type(message: dict[str, Any], person_id: int | None = None) -> str:
    title = normalize_persian(str(message.get("source_chat_title") or "")).lower()
    username = str(message.get("source_chat_username") or "").lower()
    if message.get("person_match_method") == "person_channel" or any(x in title for x in ("صفحه رسمی", "کانال رسمی شخص")):
        return "personal_page"
    if any(x in title for x in ("رسمی", "دفتر", "وزارت", "سازمان", "روابط عمومی")):
        return "official"
    if any(x in title for x in ("خبرگزاری", "روزنامه", "پایگاه خبری", "خبر", "ایرنا", "ایسنا", "فارس", "تسنیم")):
        return "news_agency"
    return "other"


def _url_quality(url: str | None, source_type: str, priority: tuple[str, ...]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if not valid_public_url(url):
        return 0.0, ["لینک نامعتبر یا غیردردسترس برای QR"]
    score = 0.35
    if source_type in priority:
        rank = priority.index(source_type)
        score += max(0.0, 0.45 - 0.1 * rank)
        reasons.append(f"اولویت منبع: {source_type}")
    if not is_generic_url(url):
        score += 0.2
        reasons.append("لینک اختصاصی‌تر از صفحه اصلی")
    return min(score, 1.0), reasons


def _message_time(message: dict[str, Any]) -> Any:
    return message.get("published_at") or message.get("message_date") or message.get("received_at") or message.get("created_at")


def _message_text(message: dict[str, Any]) -> str:
    return normalize_persian(
        str(message.get("normalized_text") or message.get("text") or message.get("caption") or ""),
        remove_noise=True,
    )


def _polarity(text: str) -> int:
    clean = normalize_persian(text).lower()
    positive = sum(1 for token in _POSITIVE if token in clean)
    negative = sum(1 for token in _NEGATIVE if token in clean)
    if positive > negative:
        return 1
    if negative > positive:
        return -1
    return 0


def _importance_reason(item: dict[str, Any], messages: list[dict[str, Any]]) -> tuple[float, str]:
    text = " ".join(_message_text(x) for x in messages)
    base = float(item.get("importance_score") or 0)
    policy = 0.12 if any(x in text for x in _POLICY) else 0
    specificity = 0.10 if any(x in text for x in _SPECIFIC) else 0
    numerical = 0.08 if re.search(r"[۰-۹0-9]", text) else 0
    conflict = 0.08 if _polarity(text) != 0 or any(x in text for x in ("واکنش", "اختلاف", "مناقشه")) else 0
    sources = len({x.get("source_chat_id") for x in messages if x.get("source_chat_id") is not None})
    diversity = min(0.12, 0.04 * max(0, sources - 1))
    views = max((int(x.get("views") or 0) for x in messages), default=0)
    engagement = min(0.12, math.log10(views + 1) / 50) if views else 0
    routine = 0.18 if text and any(x in text for x in _ROUTINE) and not any(x in text for x in _SPECIFIC) else 0
    score = max(0.0, min(1.0, base * 0.55 + policy + specificity + numerical + conflict + diversity + engagement - routine))
    reasons = []
    if policy: reasons.append("ارتباط سیاستی")
    if specificity: reasons.append("موضع یا پیشنهاد صریح")
    if numerical: reasons.append("دارای عدد یا داده")
    if conflict: reasons.append("دارای واکنش یا مناقشه")
    if diversity: reasons.append(f"پوشش در {sources} منبع")
    if engagement: reasons.append("بازدید قابل توجه")
    if routine: reasons.append("کاهش امتیاز به دلیل محتوای روزمره/تشریفاتی")
    return round(score, 4), "، ".join(reasons) or "امتیاز پایه پالایش"


def _detailed_summary(item: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    edited = normalize_persian(str(item.get("edited_summary") or item.get("summary") or "")).strip()
    selected = _loads(item.get("selected_sentences_json"), [])
    claims: list[str] = []
    for entry in selected:
        claim = normalize_persian(str(entry.get("claim") or entry.get("source_sentence") or "")).strip()
        if claim and claim not in claims:
            claims.append(claim)
    if not claims:
        for message in messages:
            for sentence in split_sentences(_message_text(message)):
                if len(sentence) >= 20 and sentence not in claims:
                    claims.append(sentence)
                if len(claims) >= 5:
                    break
            if len(claims) >= 5:
                break
    combined = " ".join(claims[:5]).strip()
    if combined and edited and edited not in combined:
        return f"{edited} {combined}"[:2200].strip()
    return (combined or edited)[:2200].strip()


_UNKNOWN_LOCATION = {"", "محل بیان نامشخص", "نامشخص", "unknown", "none"}
_META_SUMMARY_MARKERS = (
    "در پیام ارائه شده هیچ", "در پیام ارائه‌شده هیچ", "در منبع هیچ اشاره",
    "هیچ اظهار نظر مستقیمی", "هیچ اظهارنظر مستقیمی", "متن صرفاً به", "متن صرفا به",
)

def _public_location(item: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    current = normalize_persian(str(item.get("statement_location_label") or "")).strip()
    if current.lower() not in _UNKNOWN_LOCATION:
        return current.rstrip(":")
    text = " ".join(_message_text(message)[:700] for message in messages)
    patterns = (
        (r"در\s+(?:صفحه|حساب)\s+شخصی(?:\s+خود)?\s+(?:در\s+)?شبکه\s+اجتماعی\s+ایکس", "در صفحه شخصی شبکه ایکس"),
        (r"در\s+شبکه\s+اجتماعی\s+ایکس", "در شبکه اجتماعی ایکس"),
        (r"در\s+(?:کانال|صفحه)\s+شخصی", "در کانال شخصی"),
        (r"در\s+گفت[‌\s-]*وگو\s+با\s+خبرنگار\s+خانه\s+ملت", "در گفت‌وگو با خبرنگار خانه ملت"),
        (r"در\s+گفت[‌\s-]*وگو(?:یی)?\s+با", "در گفت‌وگویی رسانه‌ای"),
        (r"در\s+مصاحبه(?:ای)?", "در مصاحبه‌ای"),
        (r"در\s+نشست", "در یک نشست"),
        (r"در\s+جلسه", "در یک جلسه"),
        (r"در\s+یادداشت(?:ی)?", "در یادداشتی"),
        (r"در\s+پیام(?:ی)?", "در پیامی"),
    )
    for pattern, label in patterns:
        if re.search(pattern, text):
            return label
    if any(_source_type(message) == "personal_page" for message in messages):
        return "در صفحه شخصی"
    return "در اظهارنظری"


def _clean_editorial_summary(text: str, person_name: str, topic: str) -> str:
    clean = normalize_persian(str(text or ""), remove_noise=True).strip()
    clean = re.sub(r"\s+", " ", clean)
    prefixes = (
        rf"^{re.escape(person_name)}\s+درباره\s+{re.escape(topic)}[،,:\s]+",
        rf"^{re.escape(person_name)}[،,:\s]+",
    )
    for pattern in prefixes:
        clean = re.sub(pattern, "", clean, count=1)
    return clean.strip(" ،؛:")


def _headline_from_summary(summary: str, topic: str) -> str:
    clean = normalize_persian(summary).strip()
    first = re.split(r"[.!؟؛]", clean, maxsplit=1)[0].strip()
    words = first.split()
    if len(words) > 12:
        first = " ".join(words[:12])
    if len(first) < 8:
        first = f"موضع درباره {topic}"
    return first[:150].strip()


def _lead_from_summary(summary: str) -> str:
    clean = normalize_persian(summary).strip()
    sentences = split_sentences(clean)
    lead = " ".join(sentences[:2]) if sentences else clean
    words = lead.split()
    return " ".join(words[:65]).strip()


def _portrait_path(person_id: int | None, name: str) -> str | None:
    portrait_dir = PROJECT_ROOT / "data" / "portraits"
    candidates: list[Path] = []
    if person_id is not None:
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidates.append(portrait_dir / f"person_{person_id}.{ext}")
    slug = re.sub(r"[^آ-یA-Za-z0-9]+", "_", normalize_persian(name)).strip("_")
    if slug:
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidates.append(portrait_dir / f"{slug}.{ext}")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
    return None


def _invalid_public_attribution(summary: str) -> str | None:
    if any(marker in summary for marker in _META_SUMMARY_MARKERS):
        return "meta_attribution_without_direct_statement"
    return None


class BulletinDataBuilder:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def build(self, run_id: int, output_dir: Path, *, apply_editorial: bool = True) -> tuple[BulletinData, dict[str, Any]]:
        snapshot = await self.db.bulletin_v10_snapshot(run_id)
        run = snapshot["run"]
        run_filters = _loads(run.get("filters_json"), {})
        is_manual_editorial = bool(run_filters.get("manual_draft_ids"))
        issue_number = int(run.get("issue_number") or (self.settings.bulletin_issue_number_start + run_id - 1))
        report_mode = str(run.get("report_mode") or self.settings.bulletin_default_report_mode)
        if report_mode not in {"concise", "full"}:
            report_mode = "concise"

        registry_people = {
            int(person["person_id"]): person
            for person in snapshot.get("people") or []
            if person.get("person_id") is not None
        }
        items = snapshot["items"]
        category_people: dict[str, dict[str, BulletinPerson]] = defaultdict(dict)
        all_statements: list[BulletinStatement] = []
        unregistered_names: set[str] = set()
        person_counter = 0

        # Create stable numbering per person in category order after collection.
        temporary: list[tuple[str, dict[str, Any], BulletinStatement]] = []
        for item in items:
            messages = [m for m in item.get("messages") or [] if m.get("relation_type") != "duplicate"]
            all_messages = item.get("messages") or []
            primary_messages = [m for m in messages if m.get("relation_type") == "representative"] or messages
            importance, importance_reason = _importance_reason(item, primary_messages)
            include_main = bool(item.get("include_in_main", 1)) and importance >= self.settings.bulletin_main_importance_threshold
            include_appendix = bool(item.get("include_in_appendix", 1)) and importance >= self.settings.bulletin_appendix_importance_threshold

            source_records: list[StatementSource] = []
            best_url: str | None = None
            best_qr_path: str | None = None
            editorial_source_url = str(item.get("editorial_source_url") or "").strip() or None
            editorial_qr_path = self._resolve_existing_qr(
                str(item.get("editorial_qr_code_path") or "").strip() or None
            )
            editorial_qr_ready = bool(
                editorial_qr_path
                and valid_public_url(editorial_source_url)
                and not is_generic_url(editorial_source_url)
            )
            best_message_id: int | None = None
            best_url_score = -1.0
            source_names: list[str] = []
            source_links: list[str] = []
            for message in primary_messages:
                source_name = str(message.get("source_chat_title") or message.get("source_chat_username") or message.get("source_chat_id") or "منبع نامشخص")
                source_type = _source_type(message, item.get("person_id"))
                url = str(message.get("primary_source_url") or message.get("forwarded_origin_url") or "").strip() or None
                qr_path = str(message.get("primary_source_qr_path") or "").strip() or None
                source_records.append(StatementSource(
                    message_id=int(message["id"]), source_name=source_name, source_type=source_type,
                    source_url=url, published_at=_jalali(_message_time(message), self.settings.bulletin_default_timezone),
                    text=_message_text(message), relation_type=str(message.get("relation_type") or "evidence"),
                    views=int(message.get("views") or 0) if message.get("views") is not None else None,
                    qr_code_path=qr_path,
                ))
                if source_name not in source_names:
                    source_names.append(source_name)
                if url and url not in source_links:
                    source_links.append(url)
                score, _ = _url_quality(url, source_type, self.settings.bulletin_qr_source_priority)
                if score > best_url_score:
                    best_url_score, best_url, best_qr_path, best_message_id = score, url, qr_path, int(message["id"])

            statement_id = f"S{int(item['item_id']):06d}"
            # QR is not inferred from a raw-source URL and is never generated
            # while exporting.  It is a deliberate editorial-desk decision:
            # the final link must have been saved there and its QR must already
            # exist.  This also keeps an old/orphaned QR from leaking into a
            # newly rebuilt bulletin.
            best_qr_path = str(editorial_qr_path) if editorial_qr_ready else None

            person_record = registry_people.get(int(item.get("person_id"))) if item.get("person_id") is not None else None
            canonical_name = str((person_record or {}).get("full_name") or item.get("person_name") or "نامشخص")
            location = _public_location(item, primary_messages)
            summary_short = _clean_editorial_summary(
                str(item.get("edited_summary") or item.get("summary") or ""),
                canonical_name,
                str(item.get("topic_name") or "سایر"),
            )
            summary_detailed = _clean_editorial_summary(
                str(item.get("summary_detailed") or "").strip() or _detailed_summary(item, primary_messages),
                canonical_name,
                str(item.get("topic_name") or "سایر"),
            )
            exclusion_reason = _invalid_public_attribution(summary_short + " " + summary_detailed)
            if exclusion_reason:
                include_main = False
            statement = BulletinStatement(
                statement_id=statement_id,
                person_id=item.get("person_id"),
                topic=str(item.get("topic_name") or "سایر"),
                # دستهٔ اشخاص برای تیترهای بالای صفحه است؛ روی کارت خبر،
                # موضوع کلی و رنگ موضوع نمایش داده می‌شود.
                category=str(item.get("category") or "سایر"),
                detail=str(item.get("detail") or "").strip(),
                main_subject=str(
                    item.get("main_subject")
                    or item.get("headline")
                    or item.get("topic_name")
                    or ""
                ).strip(),
                statement_mode=location,
                summary_short=summary_short,
                summary_detailed=summary_detailed or summary_short,
                importance_score=importance,
                importance_reason=importance_reason,
                include_in_main=include_main,
                include_in_appendix=include_appendix,
                source_ids=[int(x.message_id) for x in source_records],
                source_names=source_names,
                source_links=source_links,
                source_url=(
                    editorial_source_url
                    if valid_public_url(editorial_source_url) and not is_generic_url(editorial_source_url)
                    else (best_url if valid_public_url(best_url) and not is_generic_url(best_url) else None)
                ),
                has_source_url=bool(
                    (valid_public_url(editorial_source_url) and not is_generic_url(editorial_source_url))
                    or (valid_public_url(best_url) and not is_generic_url(best_url))
                ),
                editorial_source_url=editorial_source_url,
                qr_code_path=best_qr_path,
                show_qr_in_bulletin=bool(
                    self.settings.bulletin_show_qr_codes and editorial_qr_ready
                ),
                confidence=float(item.get("confidence") or 0),
                consensus_method=str(item.get("consensus_method") or item.get("summary_method") or "extractive"),
                source_records=source_records,
                headline=_headline_from_summary(summary_short, str(item.get("topic_name") or "سایر")),
                summary_lead=_lead_from_summary(summary_short),
                summary_body=summary_detailed or summary_short,
                editorial_context_label=location,
                editorial_order=int(item.get("editorial_order") or 0),
                merged_statement_ids=[statement_id],
                public_exclusion_reason=exclusion_reason,
                footnote=str(item.get("footnote") or "").strip(),
            )
            is_event = is_event_category(item.get("content_type")) or is_event_category(
                item.get("category") or item.get("editorial_category")
            )
            # شناسنامه category is the sole grouping authority for people.
            registry_category = str((person_record or {}).get("category") or "").strip()
            if is_event:
                ckey = "events"
            else:
                ckey = category_key(
                    registry_category
                    or item.get("category")
                    or item.get("editorial_category")
                )
            editorial_item = dict(item)
            editorial_item["canonical_person_name"] = canonical_name
            editorial_item["canonical_position"] = (person_record or {}).get("position") or item.get("position")
            editorial_item["person_priority"] = (person_record or {}).get("priority") or item.get("person_priority") or 100
            editorial_item["section_category_key"] = ckey
            temporary.append((ckey, editorial_item, statement))
            all_statements.append(statement)
            if str(item.get("registry_bucket")) == "outside":
                unregistered_names.add(str(item.get("person_name")))

        # Preserve the exact editorial selection sequence for layout.  The
        # nested category/person representation below is retained for the
        # index and registry views, but must not force a card reordering.
        ordered_temporary = sorted(
            enumerate(temporary),
            key=lambda pair: (
                int(pair[1][2].editorial_order or 0)
                if int(pair[1][2].editorial_order or 0) > 0
                else 10**9,
                pair[0],
            ),
        )
        publication_order = [
            statement.statement_id
            for _, (_, _, statement) in ordered_temporary
        ]

        # Populate categories in the publication section order: registry groups
        # first, then any exact custom شناسنامه labels, with events last.
        present_keys = {entry[0] for entry in temporary}
        ordered_keys: list[str] = [
            key for key, _title in CATEGORY_ORDER if key in present_keys and key != "events"
        ]
        custom_keys = sorted(
            key for key in present_keys if key.startswith("custom:")
        )
        ordered_keys.extend(key for key in custom_keys if key not in ordered_keys)
        if "other" in present_keys and "other" not in ordered_keys:
            ordered_keys.append("other")
        if "events" in present_keys:
            ordered_keys.append("events")

        categories: list[BulletinCategory] = []
        for order, ckey in enumerate(ordered_keys, start=1):
            matching = [x for x in temporary if x[0] == ckey]
            if not matching:
                continue
            title = category_section_title(ckey, dict(CATEGORY_ORDER).get(ckey))
            person_map: dict[str, BulletinPerson] = {}
            matching.sort(key=lambda x: (int(x[1].get("person_priority") or 100), str(x[1].get("canonical_person_name") or x[1].get("person_name")), -x[2].importance_score))
            for _key, item, statement in matching:
                name = str(item.get("canonical_person_name") or item.get("person_name") or "نامشخص")
                person_key = f"id:{int(item['person_id'])}" if item.get("person_id") is not None else "name:" + normalize_persian(name).strip().lower()
                if person_key not in person_map:
                    person_counter += 1
                    person_map[person_key] = BulletinPerson(
                        person_id=item.get("person_id"), name_canonical=name,
                        position=item.get("canonical_position") or item.get("position"), is_registry_person=str(item.get("registry_bucket")) == "inside",
                        priority=int(item.get("person_priority") or 100), continuous_number=person_counter,
                        portrait_path=_portrait_path(item.get("person_id"), name),
                    )
                person_map[person_key].statements.append(statement)
            people = list(person_map.values())
            for person in people:
                person.statements.sort(key=lambda x: (x.editorial_order, -x.importance_score, x.topic))
            letter_index = min(order - 1, len(PERSIAN_SECTION_LETTERS) - 1)
            categories.append(BulletinCategory(
                category_id=ckey, title=title, order=order,
                section_letter=PERSIAN_SECTION_LETTERS[letter_index], people=people,
            ))

        selected_high_attention_ids = [
            int(value)
            for value in (run_filters.get("high_attention_item_ids") or [])
            if str(value).strip()
        ]
        selected_high_attention = await self.db.get_finalized_high_attention_items(
            selected_high_attention_ids
        )
        # «پربازتاب» is an editorially selected output.  The former automatic
        # controversy heuristic is deliberately not used here: it could add a
        # weak topic that the editor did not select or finalize.
        controversies = [
            BulletinControversy(
                title=str(item.get("title") or "").strip(),
                summary=str(item.get("summary") or "").strip(),
                sides=[],
                person_ids=[],
                statement_ids=[],
                importance_score=1.0,
                confidence=1.0,
            )
            for item in selected_high_attention
            if str(item.get("title") or "").strip()
            and str(item.get("summary") or "").strip()
        ]
        await self.db.replace_bulletin_controversies(run_id, [x.model_dump() for x in controversies])

        records = snapshot["selected_records"]
        report = _loads(run.get("processing_report_json"), {})
        generated_at = datetime.now(timezone.utc).isoformat()
        meta = BulletinMeta(
            issue_number=issue_number, report_mode=report_mode,
            report_date_jalali=_jalali(run.get("date_to") or generated_at, self.settings.bulletin_default_timezone, with_time=False) or "نامشخص",
            window_start=_jalali(run.get("date_from"), self.settings.bulletin_default_timezone),
            window_end=_jalali(run.get("date_to"), self.settings.bulletin_default_timezone),
            registry_people_count=sum(1 for p in snapshot["people"] if str(p.get("registry_status")) == "inside"),
            discovered_people_count=len(unregistered_names), raw_record_count=len(records),
            accepted_record_count=sum(1 for x in records if x.get("relevance_status") == "usable"),
            approved_item_count=len(items), source_count=len(snapshot["sources"]),
            pipeline_version="11.0" if is_manual_editorial else "10.3",
            editorial_version="human_finalization_v11" if is_manual_editorial else None,
            editorial_package_status="validated" if is_manual_editorial else "missing",
            introduction=str(run_filters.get("introduction") or "").strip(),
            generated_at=generated_at,
        )

        monitored_people = [MonitoredPerson(
            person_id=p.get("person_id"), name_canonical=str(p.get("full_name") or ""),
            category=p.get("category"), position=p.get("position"),
            is_registry_person=str(p.get("registry_status")) == "inside",
            has_statement=int(p.get("final_statement_count") or 0) > 0,
            record_count=int(p.get("run_record_count") or 0), final_statement_count=int(p.get("final_statement_count") or 0),
        ) for p in snapshot["people"]]
        # Add approved unregistered names not yet present in people.
        existing_names = {p.name_canonical for p in monitored_people}
        for name in sorted(unregistered_names - existing_names):
            statement_count = sum(1 for _c, i, _s in temporary if str(i.get("person_name")) == name)
            monitored_people.append(MonitoredPerson(
                person_id=None, name_canonical=name, is_registry_person=False,
                has_statement=True, final_statement_count=statement_count,
            ))

        sources = [MonitoredSource(
            source_id=x.get("source_id"), title=str(x.get("title") or x.get("username") or x.get("source_id") or "منبع نامشخص"),
            username=x.get("username"), platform="bale", record_count=int(x.get("record_count") or 0),
        ) for x in snapshot["sources"]]

        qc = self._quality_control(snapshot, all_statements)
        data = BulletinData(
            meta=meta, controversies=controversies, categories=categories,
            publication_order=publication_order,
            monitored_people=monitored_people, sources=sources, quality_control=qc,
        )
        if apply_editorial:
            # Local import avoids a builder/editorial module import cycle.
            from .editorial_rebuild import apply_editorial_package, load_editorial_package
            package = load_editorial_package(self.settings, run_id)
            if package is not None:
                data = apply_editorial_package(data, package)
            elif self.settings.bulletin_require_editorial_package and not is_manual_editorial:
                data.meta.editorial_package_status = "required_missing"
        return data, snapshot

    def _resolve_existing_qr(self, value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve() if path.exists() and path.is_file() else None

    async def _generate_qr(self, run_id: int, statement_id: str, url: str, message_id: int | None, output_dir: Path) -> Path | None:
        try:
            qr_dir = PROJECT_ROOT / "data" / "qrcodes"
            qr_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            path = qr_dir / f"run_{run_id}_{statement_id}_{digest[:12]}.png"
            if not path.exists():
                image = qrcode.make(url)
                image.save(path)
            if message_id:
                await self.db.update_message_qr(message_id, qr_path=str(path), qr_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            return path
        except Exception:
            return None

    def _detect_controversies(self, statements: list[BulletinStatement]) -> list[BulletinControversy]:
        by_topic: dict[str, list[BulletinStatement]] = defaultdict(list)
        for statement in statements:
            by_topic[statement.topic].append(statement)
        result: list[BulletinControversy] = []
        for topic, items in by_topic.items():
            person_ids = {x.person_id or x.statement_id for x in items}
            sources = {name for x in items for name in x.source_names}
            polarities = {_polarity(x.summary_short + " " + x.summary_detailed) for x in items}
            has_opposition = 1 in polarities and -1 in polarities
            if not has_opposition or len(person_ids) < 2:
                continue
            source_diversity = min(len(sources) / 4, 1.0)
            people_score = min(len(person_ids) / 4, 1.0)
            repetition = min(len(items) / 5, 1.0)
            engagement = min(max((r.views or 0 for x in items for r in x.source_records), default=0) / 100000, 1.0)
            editorial = max(x.importance_score for x in items)
            score = 0.18 * source_diversity + 0.20 * people_score + 0.25 + 0.12 * repetition + 0.10 * engagement + 0.15 * editorial
            if score < 0.52:
                continue
            positive = [x for x in items if _polarity(x.summary_short + " " + x.summary_detailed) > 0]
            negative = [x for x in items if _polarity(x.summary_short + " " + x.summary_detailed) < 0]
            sides = [
                ControversySide(label="مواضع موافق یا حمایتی", position_summary=" ".join(x.summary_short for x in positive[:3]), person_ids=[x.person_id or x.statement_id for x in positive]),
                ControversySide(label="مواضع مخالف یا انتقادی", position_summary=" ".join(x.summary_short for x in negative[:3]), person_ids=[x.person_id or x.statement_id for x in negative]),
            ]
            result.append(BulletinControversy(
                title=f"اختلاف‌نظر درباره {topic}",
                summary=f"موضوع {topic} با حضور {len(person_ids)} شخصیت و پوشش {len(sources)} منبع، دارای مواضع موافق و مخالف بوده است.",
                sides=sides, person_ids=sorted(person_ids, key=str),
                statement_ids=[x.statement_id for x in items], importance_score=round(min(score, 1.0), 4),
                confidence=round(min(0.95, 0.55 + 0.08 * len(person_ids) + 0.04 * len(sources)), 4),
            ))
        result.sort(key=lambda x: x.importance_score, reverse=True)
        return result[:8]

    def _quality_control(self, snapshot: dict[str, Any], statements: list[BulletinStatement]) -> QualityControl:
        records = snapshot["selected_records"]
        ambiguous = [
            {"message_id": x.get("id"), "detected_person": x.get("detected_person_name"), "confidence": x.get("person_confidence"), "method": x.get("person_match_method")}
            for x in records if x.get("detected_person_name") and float(x.get("person_confidence") or 0) < 0.70
        ]
        excluded = [
            {"message_id": x.get("id"), "status": x.get("relevance_status"), "reason": x.get("processing_error") or x.get("relevance_status")}
            for x in records if x.get("relevance_status") != "usable"
        ]
        duplicate_groups: dict[int, list[int]] = defaultdict(list)
        for x in records:
            if x.get("duplicate_of"):
                duplicate_groups[int(x["duplicate_of"])].append(int(x["id"]))
        low_conf = [
            {"statement_id": s.statement_id, "confidence": s.confidence, "topic": s.topic}
            for s in statements if s.confidence < self.settings.bulletin_quality_low_confidence
        ]
        invalid_qr = [
            {
                "statement_id": s.statement_id,
                "editorial_source_url": s.editorial_source_url,
                "qr_code_path": s.qr_code_path,
            }
            for s in statements
            if s.editorial_source_url and not s.show_qr_in_bulletin
        ]
        conflicts: list[dict[str, Any]] = []
        by_person_topic: dict[tuple[int | None, str], list[BulletinStatement]] = defaultdict(list)
        for s in statements:
            by_person_topic[(s.person_id, s.topic)].append(s)
        for key, values in by_person_topic.items():
            polarities = {_polarity(x.summary_short + " " + x.summary_detailed) for x in values}
            if 1 in polarities and -1 in polarities:
                conflicts.append({"person_id": key[0], "topic": key[1], "statement_ids": [x.statement_id for x in values]})
        return QualityControl(
            ambiguous_person_matches=ambiguous,
            conflicting_statements=conflicts,
            low_confidence_items=low_conf,
            invalid_qr_items=invalid_qr,
            excluded_records=excluded,
            duplicate_groups=[{"representative_message_id": rep, "duplicate_message_ids": ids} for rep, ids in duplicate_groups.items()],
        )
