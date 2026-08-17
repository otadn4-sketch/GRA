from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    ("government", "مسئولان دولت"),
    ("parliament", "نمایندگان مجلس شورای اسلامی"),
    ("sovereign", "سایر مسئولان حاکمیتی"),
    ("armed_forces", "فرماندهان نیروهای مسلح"),
    ("politicians", "سیاسیون"),
    ("religious", "علمای دینی"),
    ("academics", "اساتید دانشگاه و نخبگان"),
    ("experts", "کارشناسان"),
    ("journalists", "روزنامه‌نگاران و انسان‌رسانه‌ها"),
    ("other", "سایر افراد"),
    ("events", "رویدادهای مهم ایران و جهان"),
)

CATEGORY_ALIASES: dict[str, set[str]] = {
    "government": {"مسئولان دولت", "دولت", "قوه مجریه", "وزرا", "مسئول دولتی"},
    "parliament": {"نمایندگان مجلس", "نمایندگان مجلس شورای اسلامی", "مجلس", "نماینده مجلس"},
    "sovereign": {"سایر مسئولان حاکمیتی", "مسئولان حاکمیتی", "حاکمیتی", "قوه قضاییه", "شوراها"},
    "armed_forces": {"فرماندهان نیروهای مسلح", "نظامی", "فرماندهان", "نیروهای مسلح"},
    "politicians": {"سیاسیون", "فعال سیاسی", "احزاب", "چهره سیاسی"},
    "religious": {"علمای دینی", "روحانیون", "علما", "مراجع", "ائمه جمعه"},
    "academics": {"اساتید دانشگاه و نخبگان", "اساتید دانشگاه", "نخبگان", "دانشگاهیان", "استاد دانشگاه"},
    "experts": {"کارشناسان", "کارشناس", "پژوهشگران", "اندیشکده"},
    "journalists": {"روزنامه‌نگاران و انسان‌رسانه‌ها", "روزنامه نگاران", "روزنامه‌نگاران", "رسانه", "انسان‌رسانه", "خبرنگاران"},
    "other": {"سایر افراد", "سایر", "نامشخص", ""},
    "events": {
        "رویدادهای مهم ایران و جهان",
        "رویداد مهم ایران و جهان",
        "رویداد",
        "رویدادها",
        "اخبار رویداد",
        "event",
        "events",
    },
}

REGISTRY_CATEGORY_OPTIONS: tuple[str, ...] = tuple(
    title for key, title in CATEGORY_ORDER if key != "events"
)

PERSIAN_SECTION_LETTERS = ("الف", "ب", "پ", "ت", "ث", "ج", "چ", "ح", "خ", "د")


def category_key(category: str | None) -> str:
    clean = " ".join(str(category or "").replace("ي", "ی").replace("ك", "ک").split())
    for key, aliases in CATEGORY_ALIASES.items():
        if clean in aliases:
            return key
    lowered = clean.lower()
    heuristics = (
        ("events", ("رویداد مهم", "رویدادهای مهم")),
        ("government", ("دولت", "وزیر", "معاون رئیس", "استاندار")),
        ("parliament", ("مجلس", "نماینده")),
        ("armed_forces", ("فرمانده", "سپاه", "ارتش", "نظامی")),
        ("religious", ("آیت", "حجت", "امام جمعه", "روحانی")),
        ("academics", ("دانشگاه", "استاد", "نخبه")),
        ("journalists", ("روزنامه", "خبرنگار", "رسانه")),
        ("experts", ("کارشناس", "پژوهشگر", "تحلیلگر")),
        ("politicians", ("سیاسی", "حزب", "دبیرکل")),
        ("sovereign", ("قوه", "شورا", "حاکمیتی")),
    )
    for key, words in heuristics:
        if any(word.lower() in lowered for word in words):
            return key
    # Preserve exact registry category labels as stable custom section keys so
    # people linked to a free-text شناسنامه category do not collapse into «سایر».
    if clean and clean not in {"سایر", "سایر افراد", "نامشخص"}:
        return f"custom:{clean}"
    return "other"


def category_section_title(key: str, fallback: str | None = None) -> str:
    if key.startswith("custom:"):
        return key.split(":", 1)[1] or (fallback or "سایر افراد")
    return dict(CATEGORY_ORDER).get(key, fallback or "سایر افراد")


def category_title(key: str) -> str:
    return category_section_title(key)


class BulletinMeta(BaseModel):
    issue_number: int
    report_mode: Literal["concise", "full"]
    report_date_jalali: str
    window_start: str | None = None
    window_end: str | None = None
    registry_people_count: int = 0
    discovered_people_count: int = 0
    raw_record_count: int = 0
    accepted_record_count: int = 0
    approved_item_count: int = 0
    source_count: int = 0
    pipeline_version: str = "10.3"
    editorial_version: str | None = None
    editorial_package_status: str = "missing"
    introduction: str = ""
    generated_at: str


class StatementSource(BaseModel):
    message_id: int
    source_name: str | None = None
    source_type: str = "other"
    source_url: str | None = None
    published_at: str | None = None
    text: str | None = None
    relation_type: str = "evidence"
    views: int | None = None
    qr_code_path: str | None = None


class BulletinStatement(BaseModel):
    statement_id: str
    person_id: int | None = None
    topic: str
    category: str = "سایر"
    detail: str = ""
    # سوژهٔ اصلی کوتاهِ خبر؛ از تحلیل مدل اول می‌آید و در میز تدوین قابل
    # اصلاح است. «topic» همچنان موضوع کلی برای رنگ و برچسب عمودی است.
    main_subject: str = ""
    statement_mode: str
    summary_short: str
    summary_detailed: str
    importance_score: float = Field(ge=0, le=1)
    importance_reason: str = ""
    include_in_main: bool = True
    include_in_appendix: bool = True
    source_ids: list[int] = Field(default_factory=list)
    source_names: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    source_url: str | None = None
    has_source_url: bool = False
    # This is deliberately distinct from URLs found in the raw evidence.  A
    # QR is a publication decision and is valid only when the editor has set
    # this final source link in the editorial desk.
    editorial_source_url: str | None = None
    qr_code_path: str | None = None
    show_qr_in_bulletin: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    consensus_method: str | None = None
    source_records: list[StatementSource] = Field(default_factory=list)
    headline: str = ""
    summary_lead: str = ""
    summary_body: str = ""
    editorial_context_label: str | None = None
    editorial_order: int = 0
    merged_statement_ids: list[str] = Field(default_factory=list)
    public_exclusion_reason: str | None = None


class BulletinPerson(BaseModel):
    person_id: int | None = None
    name_canonical: str
    position: str | None = None
    is_registry_person: bool
    priority: int = 100
    continuous_number: int = 0
    statements: list[BulletinStatement] = Field(default_factory=list)
    portrait_path: str | None = None


class BulletinCategory(BaseModel):
    category_id: str
    title: str
    order: int
    section_letter: str
    people: list[BulletinPerson] = Field(default_factory=list)


class ControversySide(BaseModel):
    label: str
    position_summary: str
    person_ids: list[int | str] = Field(default_factory=list)


class BulletinControversy(BaseModel):
    title: str
    summary: str
    sides: list[ControversySide] = Field(default_factory=list)
    person_ids: list[int | str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    importance_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class MonitoredPerson(BaseModel):
    person_id: int | None = None
    name_canonical: str
    category: str | None = None
    position: str | None = None
    is_registry_person: bool
    has_statement: bool
    record_count: int = 0
    final_statement_count: int = 0


class MonitoredSource(BaseModel):
    source_id: int | str | None = None
    title: str
    username: str | None = None
    platform: str = "bale"
    record_count: int = 0


class QualityIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"] = "warning"
    object_type: str | None = None
    object_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class QualityControl(BaseModel):
    ambiguous_person_matches: list[dict[str, Any]] = Field(default_factory=list)
    conflicting_statements: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_numbers: list[dict[str, Any]] = Field(default_factory=list)
    low_confidence_items: list[dict[str, Any]] = Field(default_factory=list)
    invalid_qr_items: list[dict[str, Any]] = Field(default_factory=list)
    excluded_records: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_groups: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[QualityIssue] = Field(default_factory=list)


class BulletinData(BaseModel):
    meta: BulletinMeta
    controversies: list[BulletinControversy] = Field(default_factory=list)
    categories: list[BulletinCategory] = Field(default_factory=list)
    # Exact sequence selected by the bulletin editor.  Categories are retained
    # for the index and person registry headings; renderers must use this list
    # when laying out cards so they do not reorder news by speaker.
    publication_order: list[str] = Field(default_factory=list)
    monitored_people: list[MonitoredPerson] = Field(default_factory=list)
    sources: list[MonitoredSource] = Field(default_factory=list)
    quality_control: QualityControl = Field(default_factory=QualityControl)
