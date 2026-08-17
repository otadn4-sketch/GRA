from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    # Production installs python-dotenv from requirements.txt. Keeping config
    # importable without it lets the standalone layout preview run in the
    # bundled document-validation runtime, where environment values still work.
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_set(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result



def _api_keys() -> tuple[str, ...]:
    """Load one to eight API keys while preserving legacy single-key settings."""
    values: list[str] = []
    for index in range(1, 9):
        value = os.getenv(f"AI_API_KEY_{index}", "").strip()
        if value:
            values.append(value)
    legacy = (os.getenv("AI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")).strip()
    if legacy:
        values.append(legacy)
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return tuple(unique[:8])


def _profile_api_keys(prefix: str) -> tuple[str, ...]:
    values: list[str] = []
    for index in range(1, 9):
        value = os.getenv(f"{prefix}_API_KEY_{index}", "").strip()
        if value:
            values.append(value)
    single = os.getenv(f"{prefix}_API_KEY", "").strip()
    if single:
        values.append(single)
    return tuple(dict.fromkeys(values))[:8]


def _numbered_api_key_slots(prefix: str) -> tuple[str, ...]:
    """Return the eight configured slots without collapsing their numbers.

    The analysis workflow intentionally uses slots 1–4 while the
    «پربازتاب» workflow reserves slot 8.  The older helper returns a compact
    sequence and is therefore unsuitable when a specific numbered key is
    required.
    """
    return tuple(
        os.getenv(f"{prefix}_API_KEY_{index}", "").strip()
        for index in range(1, 9)
    )


def resolve_database_path(raw: str | None = None) -> Path:
    value = (raw if raw is not None else os.getenv("DATABASE_PATH", "data/prasad.sqlite3")).strip()
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    token: str
    source_channel_username: str
    target_chat_id: int | None
    target_chat_title: str
    miniapp_base_url: str
    bot_mode: str
    webhook_secret: str
    database_path: Path
    reviewer_user_ids: set[int]
    purge_rejected_content: bool
    miniapp_auth_max_age: int
    source_input_timeout: int
    replace_original_message: bool
    input_mode: str
    delete_channel_input_messages: bool
    cleanup_legacy_channel_inputs: bool
    log_level: str

    # Selenium crawler inherited from the Bale collector.
    crawler_enabled: bool
    crawler_channels_path: Path
    crawler_firefox_profile: str
    crawler_destination_chat_id: str
    crawler_repeat_seconds: int

    # Dashboard
    dashboard_enabled: bool
    admin_username: str
    admin_password: str

    # AI / bulletin
    ai_provider: str
    ai_api_key: str = field(repr=False)
    ai_api_keys: tuple[str, ...] = field(repr=False)
    ai_base_url: str
    ai_model: str
    ai_timeout_seconds: int
    ai_max_concurrency: int
    ai_concurrency_per_key: int
    ai_circuit_breaker_seconds: int
    ai_temperature: float
    bulletin_default_timezone: str
    bulletin_max_messages: int
    bulletin_output_dir: Path
    bulletin_near_duplicate_threshold: float
    bulletin_min_summary_coverage: float
    bulletin_max_summary_chars: int
    bulletin_ai_max_retries: int
    bulletin_summary_max_sentences: int
    bulletin_summary_mmr_lambda: float
    bulletin_font_name: str
    bulletin_quality_low_confidence: float

    # Version 10 final bulletin/export controls
    bulletin_default_report_mode: str
    bulletin_issue_number_start: int
    bulletin_show_period_on_cover: bool
    bulletin_show_qr_codes: bool
    bulletin_qr_size_cm: float
    bulletin_generate_missing_qr: bool
    bulletin_qr_source_priority: tuple[str, ...]
    bulletin_generate_concise: bool
    bulletin_generate_full: bool
    bulletin_generate_json: bool
    bulletin_generate_audit_xlsx: bool
    bulletin_generate_unregistered_xlsx: bool
    bulletin_generate_run_logs: bool
    bulletin_fail_on_critical_validation: bool
    bulletin_main_importance_threshold: float
    bulletin_appendix_importance_threshold: float
    bulletin_toc_update_on_open: bool

    # Operational controls
    scheduler_enabled: bool
    backup_dir: Path
    backup_enabled: bool
    backup_cron: str
    max_update_attempts: int

    # Version 10.4 human editorial and public publishing outputs. Defaults preserve
    # compatibility with older tests and code that construct Settings directly.
    bulletin_require_editorial_package: bool = False
    editorial_ai_max_retries: int = 3
    editorial_headline_max_words: int = 14
    editorial_body_max_words: int = 190
    editorial_max_controversies: int = 5
    bulletin_generate_classic_pdf: bool = False
    bulletin_generate_magazine_html: bool = False
    bulletin_generate_magazine_pdf: bool = False
    magazine_browser_path: str = ""
    magazine_show_portraits: bool = True
    magazine_show_qr_codes: bool = True
    magazine_columns: int = 2
    magazine_page_size: str = "A4"
    magazine_max_body_words: int = 180

    # AI semantic enrichment (defaults preserve compatibility with older tests/config builders)
    ai_enrichment_enabled: bool = False
    ai_enrichment_scope: str = "all"
    ai_enrichment_batch_size: int = 6
    ai_enrichment_max_retries: int = 3
    ai_enrichment_min_confidence: float = 0.60
    ai_enrichment_max_chars_per_message: int = 6000
    ai_require_validated_output: bool = False
    ai_allow_pending_export: bool = False

    # Independent model for editorial synthesis/final summaries.
    editorial_ai_provider: str = "disabled"
    editorial_ai_api_keys: tuple[str, ...] = field(default_factory=tuple, repr=False)
    editorial_ai_base_url: str = "https://api.openai.com/v1"
    editorial_ai_model: str = ""
    editorial_ai_timeout_seconds: int = 180
    editorial_ai_temperature: float = 0.1
    # Model-one key routing.  Slots 1–4 analyze people/location; slot 8 is
    # intentionally isolated for the high-attention editorial workflow.
    analysis_person_api_keys: tuple[str, ...] = field(default_factory=tuple, repr=False)
    high_attention_ai_api_key: str = field(default="", repr=False)
    # Public address used for editorial short links.  A fixed production default
    # keeps this feature usable without changing an existing .env file.
    public_base_url: str = "https://prasad.lmskalk.ir"

    # HTML is the canonical page-layout source.  Word remains a secondary,
    # editable export generated from the same BulletinData object.
    layout_engine: str = "html"
    layout_browser_path: str = ""
    layout_page_size: str = "A3"


def load_settings() -> Settings:
    bot_mode = os.getenv("BOT_MODE", "polling").strip().lower()
    if bot_mode not in {"polling", "webhook", "disabled"}:
        raise RuntimeError("BOT_MODE فقط polling، webhook یا disabled است.")

    token = os.getenv("BALE_BOT_TOKEN", "").strip()
    if bot_mode != "disabled" and not token:
        raise RuntimeError(
            "برای polling یا webhook باید BALE_BOT_TOKEN تنظیم شود؛ "
            "برای اجرای فقط داشبورد BOT_MODE=disabled را قرار دهید."
        )

    raw_target_id = os.getenv("TARGET_CHAT_ID", "").strip()
    target_chat_id = int(raw_target_id) if raw_target_id else None

    base_url = os.getenv("MINIAPP_BASE_URL", "").strip().rstrip("/")
    if base_url and not base_url.startswith("https://"):
        raise RuntimeError("MINIAPP_BASE_URL در صورت تنظیم باید با https:// آغاز شود.")

    input_mode = os.getenv("INPUT_MODE", "channel").strip().lower()
    if input_mode not in {"miniapp", "channel", "private"}:
        raise RuntimeError("INPUT_MODE فقط miniapp یا channel یا private است.")

    crawler_enabled = _bool("BALE_CRAWLER_ENABLED", False)
    crawler_channels_value = os.getenv(
        "BALE_CRAWLER_CHANNELS_FILE", "crawler/channels.csv"
    ).strip()
    crawler_channels_path = Path(os.path.expandvars(crawler_channels_value)).expanduser()
    if not crawler_channels_path.is_absolute():
        crawler_channels_path = PROJECT_ROOT / crawler_channels_path
    crawler_firefox_profile = os.path.expandvars(
        os.getenv("BALE_CRAWLER_FIREFOX_PROFILE", "").strip()
    )
    crawler_destination_chat_id = (
        os.getenv("BALE_CRAWLER_DESTINATION_CHAT_ID", "").strip()
        or os.getenv("BALE_DESTINATION_CHAT_ID", "").strip()
        or raw_target_id
    )
    crawler_repeat_seconds = max(
        60, int(os.getenv("BALE_CRAWLER_REPEAT_SECONDS", "1800"))
    )
    if crawler_enabled and not token:
        raise RuntimeError("برای فعال‌سازی کرولر، BALE_BOT_TOKEN باید تنظیم شود.")
    if crawler_enabled and not crawler_destination_chat_id:
        raise RuntimeError(
            "برای فعال‌سازی کرولر، BALE_CRAWLER_DESTINATION_CHAT_ID "
            "یا TARGET_CHAT_ID باید تنظیم شود."
        )

    ai_provider = os.getenv(
        "AI_ANALYSIS_PROVIDER", os.getenv("AI_PROVIDER", "openai")
    ).strip().lower()
    if ai_provider not in {"openai", "openai_compatible", "disabled"}:
        raise RuntimeError("AI_ANALYSIS_PROVIDER باید openai یا openai_compatible یا disabled باشد.")

    analysis_profile_slots = _numbered_api_key_slots("AI_ANALYSIS")
    legacy_analysis_slots = _numbered_api_key_slots("AI")
    numbered_analysis_slots = (
        analysis_profile_slots
        if any(analysis_profile_slots)
        else legacy_analysis_slots
    )
    fallback_analysis_keys = _profile_api_keys("AI_ANALYSIS") or _api_keys()
    if any(numbered_analysis_slots):
        # Do not compact these before slicing: API_KEY_8 must never be used
        # by the automatic person/location analysis pool.  Slots 1–5 are
        # available to the parallel person/location analysis pool.
        ai_api_keys = tuple(
            dict.fromkeys(key for key in numbered_analysis_slots[:5] if key)
        )
        high_attention_ai_api_key = numbered_analysis_slots[7]
    else:
        # Keep a single-key legacy installation operational; installations
        # with numbered keys receive the strict 1–5/8 split above.
        ai_api_keys = tuple(fallback_analysis_keys[:5])
        high_attention_ai_api_key = (
            fallback_analysis_keys[7] if len(fallback_analysis_keys) >= 8 else ""
        )
    editorial_ai_provider = os.getenv(
        "AI_EDITORIAL_PROVIDER", ai_provider
    ).strip().lower()
    if editorial_ai_provider not in {"openai", "openai_compatible", "disabled"}:
        raise RuntimeError("AI_EDITORIAL_PROVIDER باید openai یا openai_compatible یا disabled باشد.")
    editorial_ai_api_keys = (
        _profile_api_keys("AI_EDITORIAL") or ai_api_keys
    )
    ai_max_concurrency = max(1, int(os.getenv("AI_MAX_CONCURRENCY", "5")))
    ai_concurrency_per_key = max(1, int(os.getenv("AI_CONCURRENCY_PER_KEY", "1")))
    if ai_max_concurrency > 64:
        raise RuntimeError("AI_MAX_CONCURRENCY نباید بیشتر از 64 باشد.")

    ai_enrichment_scope = os.getenv("AI_ENRICHMENT_SCOPE", "all").strip().lower()
    if ai_enrichment_scope not in {"all", "unresolved_and_review", "unresolved_only"}:
        raise RuntimeError("AI_ENRICHMENT_SCOPE باید all یا unresolved_and_review یا unresolved_only باشد.")

    backup_value = os.getenv("BACKUP_DIR", "backups").strip()
    backup_dir = Path(backup_value)
    if not backup_dir.is_absolute():
        backup_dir = PROJECT_ROOT / backup_dir

    dashboard_enabled = _bool("DASHBOARD_ENABLED", True)
    admin_username = os.getenv("ADMIN_USERNAME", "admin").strip()
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if dashboard_enabled and (
        len(admin_password) < 10
        or admin_password.upper().startswith("CHANGE_THIS")
    ):
        raise RuntimeError(
            "ADMIN_PASSWORD باید یک رمز واقعی و حداقل ۱۰ نویسه باشد."
        )

    return Settings(
        token=token,
        source_channel_username=os.getenv("SOURCE_CHANNEL_USERNAME", "nookh").strip().lstrip("@"),
        target_chat_id=target_chat_id,
        target_chat_title=os.getenv("TARGET_CHAT_TITLE", "گروه پایش دوم").strip(),
        miniapp_base_url=base_url,
        bot_mode=bot_mode,
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
        database_path=resolve_database_path(),
        reviewer_user_ids=_int_set("REVIEWER_USER_IDS"),
        purge_rejected_content=_bool("PURGE_REJECTED_CONTENT", False),
        miniapp_auth_max_age=int(os.getenv("MINIAPP_AUTH_MAX_AGE", "900")),
        source_input_timeout=int(os.getenv("SOURCE_INPUT_TIMEOUT", "1800")),
        replace_original_message=_bool("REPLACE_ORIGINAL_MESSAGE", True),
        input_mode=input_mode,
        delete_channel_input_messages=_bool("DELETE_CHANNEL_INPUT_MESSAGES", True),
        cleanup_legacy_channel_inputs=_bool("CLEANUP_LEGACY_CHANNEL_INPUTS", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        crawler_enabled=crawler_enabled,
        crawler_channels_path=crawler_channels_path.resolve(),
        crawler_firefox_profile=crawler_firefox_profile,
        crawler_destination_chat_id=crawler_destination_chat_id,
        crawler_repeat_seconds=crawler_repeat_seconds,
        dashboard_enabled=dashboard_enabled,
        admin_username=admin_username,
        admin_password=admin_password,
        ai_provider=ai_provider,
        ai_api_key=ai_api_keys[0] if ai_api_keys else "",
        ai_api_keys=ai_api_keys,
        ai_base_url=os.getenv(
            "AI_ANALYSIS_BASE_URL",
            os.getenv("AI_BASE_URL", "https://api.openai.com/v1"),
        ).strip().rstrip("/"),
        ai_model=os.getenv(
            "AI_ANALYSIS_MODEL", os.getenv("AI_MODEL", "")
        ).strip(),
        ai_timeout_seconds=int(os.getenv("AI_TIMEOUT_SECONDS", "180")),
        ai_max_concurrency=ai_max_concurrency,
        ai_concurrency_per_key=ai_concurrency_per_key,
        ai_circuit_breaker_seconds=max(1, int(os.getenv("AI_CIRCUIT_BREAKER_SECONDS", "60"))),
        ai_temperature=float(os.getenv("AI_TEMPERATURE", "0.1")),
        ai_enrichment_enabled=_bool("AI_ENRICHMENT_ENABLED", True),
        ai_enrichment_scope=ai_enrichment_scope,
        ai_enrichment_batch_size=max(1, min(12, int(os.getenv("AI_ENRICHMENT_BATCH_SIZE", "6")))),
        ai_enrichment_max_retries=max(2, int(os.getenv("AI_ENRICHMENT_MAX_RETRIES", "3"))),
        ai_enrichment_min_confidence=max(0.0, min(1.0, float(os.getenv("AI_ENRICHMENT_MIN_CONFIDENCE", "0.60")))),
        ai_enrichment_max_chars_per_message=max(500, int(os.getenv("AI_ENRICHMENT_MAX_CHARS_PER_MESSAGE", "6000"))),
        ai_require_validated_output=_bool("AI_REQUIRE_VALIDATED_OUTPUT", True),
        ai_allow_pending_export=_bool("AI_ALLOW_PENDING_EXPORT", False),
        bulletin_default_timezone=os.getenv("BULLETIN_DEFAULT_TIMEZONE", "Asia/Tehran").strip(),
        bulletin_max_messages=int(os.getenv("BULLETIN_MAX_MESSAGES", "500")),
        bulletin_output_dir=(PROJECT_ROOT / os.getenv("BULLETIN_OUTPUT_DIR", "data/bulletins")).resolve() if not Path(os.getenv("BULLETIN_OUTPUT_DIR", "data/bulletins")).is_absolute() else Path(os.getenv("BULLETIN_OUTPUT_DIR", "data/bulletins")).resolve(),
        bulletin_near_duplicate_threshold=float(os.getenv("BULLETIN_NEAR_DUPLICATE_THRESHOLD", "0.86")),
        bulletin_min_summary_coverage=float(os.getenv("BULLETIN_MIN_SUMMARY_COVERAGE", "0.35")),
        bulletin_max_summary_chars=int(os.getenv("BULLETIN_MAX_SUMMARY_CHARS", "650")),
        bulletin_ai_max_retries=max(3, int(os.getenv("BULLETIN_AI_MAX_RETRIES", "3"))),
        bulletin_summary_max_sentences=int(os.getenv("BULLETIN_SUMMARY_MAX_SENTENCES", "3")),
        bulletin_summary_mmr_lambda=float(os.getenv("BULLETIN_SUMMARY_MMR_LAMBDA", "0.72")),
        bulletin_font_name=os.getenv("BULLETIN_FONT_NAME", "IRZar").strip() or "IRZar",
        bulletin_quality_low_confidence=float(os.getenv("BULLETIN_QUALITY_LOW_CONFIDENCE", "0.60")),
        bulletin_default_report_mode=os.getenv("BULLETIN_DEFAULT_REPORT_MODE", "concise").strip().lower() if os.getenv("BULLETIN_DEFAULT_REPORT_MODE", "concise").strip().lower() in {"concise", "full"} else "concise",
        bulletin_issue_number_start=int(os.getenv("BULLETIN_ISSUE_NUMBER_START", "1")),
        bulletin_show_period_on_cover=_bool("BULLETIN_SHOW_PERIOD_ON_COVER", True),
        bulletin_show_qr_codes=_bool("SHOW_QR_CODES", True),
        bulletin_qr_size_cm=float(os.getenv("QR_SIZE_CM", "2.0")),
        bulletin_generate_missing_qr=_bool("GENERATE_MISSING_QR", True),
        bulletin_qr_source_priority=tuple(x.strip() for x in os.getenv("QR_SOURCE_PRIORITY", "official,personal_page,news_agency,other").split(",") if x.strip()),
        bulletin_generate_concise=_bool("GENERATE_CONCISE_DOCX", True),
        bulletin_generate_full=_bool("GENERATE_FULL_DOCX", True),
        bulletin_generate_json=_bool("GENERATE_BULLETIN_JSON", True),
        bulletin_generate_audit_xlsx=_bool("GENERATE_AUDIT_XLSX", True),
        bulletin_generate_unregistered_xlsx=_bool("GENERATE_UNREGISTERED_XLSX", True),
        bulletin_generate_run_logs=_bool("GENERATE_RUN_LOG_FILES", True),
        bulletin_fail_on_critical_validation=_bool("FAIL_ON_CRITICAL_VALIDATION", True),
        bulletin_main_importance_threshold=float(os.getenv("BULLETIN_MAIN_IMPORTANCE_THRESHOLD", "0.30")),
        bulletin_appendix_importance_threshold=float(os.getenv("BULLETIN_APPENDIX_IMPORTANCE_THRESHOLD", "0.0")),
        bulletin_toc_update_on_open=_bool("BULLETIN_TOC_UPDATE_ON_OPEN", True),
        bulletin_require_editorial_package=_bool("REQUIRE_EDITORIAL_PACKAGE_FOR_PUBLIC_EXPORT", True),
        editorial_ai_max_retries=max(2, int(os.getenv("EDITORIAL_AI_MAX_RETRIES", "3"))),
        editorial_headline_max_words=max(6, int(os.getenv("EDITORIAL_HEADLINE_MAX_WORDS", "14"))),
        editorial_body_max_words=max(70, int(os.getenv("EDITORIAL_BODY_MAX_WORDS", "190"))),
        editorial_max_controversies=max(0, min(8, int(os.getenv("EDITORIAL_MAX_CONTROVERSIES", "5")))),
        bulletin_generate_classic_pdf=_bool("GENERATE_CLASSIC_PDF", True),
        bulletin_generate_magazine_html=_bool("GENERATE_MAGAZINE_HTML", True),
        bulletin_generate_magazine_pdf=_bool("GENERATE_MAGAZINE_PDF", True),
        magazine_browser_path=os.getenv("MAGAZINE_BROWSER_PATH", "").strip(),
        magazine_show_portraits=_bool("MAGAZINE_SHOW_PORTRAITS", True),
        magazine_show_qr_codes=_bool("MAGAZINE_SHOW_QR_CODES", True),
        magazine_columns=max(1, min(3, int(os.getenv("MAGAZINE_COLUMNS", "2")))),
        magazine_page_size=os.getenv("MAGAZINE_PAGE_SIZE", "A4").strip().upper() if os.getenv("MAGAZINE_PAGE_SIZE", "A4").strip().upper() in {"A4", "A3", "LETTER"} else "A4",
        magazine_max_body_words=max(70, int(os.getenv("MAGAZINE_MAX_BODY_WORDS", "180"))),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
        backup_dir=backup_dir.resolve(),
        backup_enabled=_bool("BACKUP_ENABLED", True),
        backup_cron=os.getenv("BACKUP_CRON", "0 3 * * *").strip(),
        max_update_attempts=int(os.getenv("MAX_UPDATE_ATTEMPTS", "3")),
        editorial_ai_provider=editorial_ai_provider,
        editorial_ai_api_keys=editorial_ai_api_keys,
        editorial_ai_base_url=os.getenv(
            "AI_EDITORIAL_BASE_URL",
            os.getenv(
                "AI_ANALYSIS_BASE_URL",
                os.getenv("AI_BASE_URL", "https://api.openai.com/v1"),
            ),
        ).strip().rstrip("/"),
        editorial_ai_model=os.getenv(
            "AI_EDITORIAL_MODEL",
            os.getenv("AI_ANALYSIS_MODEL", os.getenv("AI_MODEL", "")),
        ).strip(),
        editorial_ai_timeout_seconds=max(
            10,
            int(
                os.getenv(
                    "AI_EDITORIAL_TIMEOUT_SECONDS",
                    os.getenv("AI_TIMEOUT_SECONDS", "180"),
                )
            ),
        ),
        editorial_ai_temperature=float(
            os.getenv(
                "AI_EDITORIAL_TEMPERATURE",
                os.getenv("AI_TEMPERATURE", "0.1"),
            )
        ),
        analysis_person_api_keys=ai_api_keys,
        high_attention_ai_api_key=high_attention_ai_api_key,
        public_base_url=os.getenv("PUBLIC_BASE_URL", "https://prasad.lmskalk.ir").strip().rstrip("/"),
        layout_engine="html",
        layout_browser_path=os.getenv(
            "LAYOUT_BROWSER_PATH",
            os.getenv("MAGAZINE_BROWSER_PATH", ""),
        ).strip(),
        layout_page_size="A3",
    )
