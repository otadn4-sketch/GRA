from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import secrets
import re
import subprocess
import sys
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from openpyxl import load_workbook
import jdatetime
from PIL import Image
import qrcode

from .auth import ROLE_PERMISSIONS, ROLE_TITLES, AdminPrincipal, VALID_ROLES
from .bulletins import BulletinService
from .backup import create_backup
from .bale import BaleAPIError, BaleClient
from .bulletin_cleanup import bulletin_run_directory, remove_bulletin_run_directory
from .config import Settings
from .db import Database, EditorialDraftConflictError, comparable_utc_iso, eitan_axis_public, message_media_assets, parse_eitan_upload
from .gapgpt_status import fetch_gapgpt_status
from .live_update import apply_update_zip, current_version, last_update_status, request_reload
from .scheduler import BulletinScheduler
from .editorial_automation import EditorialAutomationService

security = HTTPBasic(auto_error=False)
WEB_ROOT = (Path(__file__).resolve().parents[1] / "web").resolve()


class SourceCreate(BaseModel):
    chat_id: int | None = None
    username: str | None = None
    title: str | None = None
    chat_type: str = "channel"
    target_chat_id: int | None = None
    target_title: str | None = None


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    target_chat_id: int | None = None
    target_title: str | None = None
    title: str | None = None


class BulletinCreate(BaseModel):
    template_id: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    date_from_jalali: str | None = None
    date_to_jalali: str | None = None
    timezone: str = "Asia/Tehran"
    source_chat_ids: list[int] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["pending"])
    issue_number: int | None = None
    report_mode: str = "concise"
    export_options: dict[str, bool] = Field(default_factory=dict)


class TemplateCreate(BaseModel):
    id: int | None = None
    name: str
    system_prompt: str
    user_prompt_template: str
    output_format: str = "text"


class ScheduleCreate(BaseModel):
    id: int | None = None
    name: str
    cron_expression: str
    timezone: str = "Asia/Tehran"
    template_id: int | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class ScheduleEnable(BaseModel):
    enabled: bool


class MessageRatingSave(BaseModel):
    rating: float = Field(ge=1, le=5)


class CrawlerRecoveryRequest(BaseModel):
    # The dashboard currently exposes a fixed, safe 48-hour recovery button.
    # Keeping this typed payload makes the endpoint usable by an API client too.
    hours: int = Field(default=48, ge=1, le=168)


class MessageAnalysisBatch(BaseModel):
    message_ids: list[int] = Field(default_factory=list)


class PersonCreate(BaseModel):
    person_id: int | None = None
    full_name: str
    category: str | None = None
    position: str | None = None
    registry_status: str = "inside"
    active: bool = True
    aliases: list[str] = Field(default_factory=list)
    chat_id: int | None = None
    username: str | None = None
    priority: int = 0
    replace_aliases: bool = True


class PersonMerge(BaseModel):
    source_person_id: int
    target_person_id: int


class PersonCategoryRename(BaseModel):
    old_category: str
    new_category: str


class PersonCategoryCreate(BaseModel):
    title: str


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TopicCreate(BaseModel):
    topic_id: int | None = None
    name: str
    keywords: list[str] = Field(default_factory=list)


class BulletinItemModerate(BaseModel):
    status: str
    edited_summary: str | None = None
    reason: str | None = None
    person_name: str | None = None
    person_id: int | None = None
    issue_tags: list[str] = Field(default_factory=list)


class EditorialAIReconcile(BaseModel):
    exclude_unlinked: bool = False


class PendingAIResolve(BaseModel):
    decision: str


class ReprocessRequest(BaseModel):
    mode: str = "all"


class EvaluationCaseCreate(BaseModel):
    notes: str | None = None


class CandidateReview(BaseModel):
    action: str
    merge_person_id: int | None = None
    category: str | None = None
    full_name: str | None = Field(default=None, max_length=240)
    aliases: list[str] = Field(default_factory=list)
    position: str | None = Field(default=None, max_length=300)
    add_detected_alias: bool = True
    add_position: bool = True


class EditorialDraftInput(BaseModel):
    message_id: int
    selected_text: str | None = None
    sort_order: int = 0


class EditorialDraftCreate(BaseModel):
    # This opt-in keeps the ordinary analysis flow from accepting an empty
    # source selection while allowing a clean, editor-created news item.
    manual: bool = False
    content_type: str = "person_statement"
    title: str | None = None
    person_id: int | None = None
    person_name: str | None = None
    position: str | None = None
    topic_id: int | None = None
    topic_name: str | None = None
    main_subject: str | None = Field(default=None, max_length=300)
    category_name: str | None = None
    event_title: str | None = None
    event_entities: list[str] = Field(default_factory=list)
    event_location: str | None = None
    event_time: str | None = None
    base_text: str | None = None
    message_inputs: list[EditorialDraftInput] = Field(default_factory=list)


class EditorialDraftSave(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = None
    base_text: str
    summary_paragraph: str | None = None
    summary_sentence: str | None = None
    summary_title: str | None = None
    detail: str | None = None
    category_name: str | None = None
    person_id: int | None = None
    person_name: str | None = None
    topic_id: int | None = None
    topic_name: str | None = None
    main_subject: str | None = Field(default=None, max_length=300)
    oration_location: str | None = None
    source_url: str | None = None
    footnote: str | None = Field(default=None, max_length=4000)
    change_reason: str = "ویرایش سردبیر"


class EditorialSourceLink(BaseModel):
    source_url: str


class EditorialGenerate(BaseModel):
    kind: str = "summaries"
    base_text: str | None = None
    persist: bool = False
    expected_version: int | None = Field(default=None, ge=1)


class EditorialDraftRestore(BaseModel):
    expected_version: int = Field(ge=1)


class EditorialFinalize(BaseModel):
    # ``finalized_at_jalali`` is convenient for API clients; the separate
    # date/time fields match the dashboard's two controls.
    finalized_at: str | None = None
    finalized_at_jalali: str | None = None
    finalized_date_jalali: str | None = None
    finalized_time: str | None = None
    timezone: str = "Asia/Tehran"


class AnalyzedSpeakerPromotion(BaseModel):
    category: str | None = None


class AnalyzedSpeakerCorrection(BaseModel):
    speaker_name: str = Field(min_length=1, max_length=240)
    position: str | None = Field(default=None, max_length=300)


class AnalyzedMessageDiscard(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class EditorialBulletinCreate(BaseModel):
    draft_ids: list[int]
    high_attention_item_ids: list[int] = Field(default_factory=list)
    title: str | None = None
    issue_number: int | None = None
    report_mode: str = "concise"
    introduction: str | None = Field(default=None, max_length=8000)


class HighAttentionGenerate(BaseModel):
    source_day: str = Field(min_length=10, max_length=10)
    draft_ids: list[int] = Field(default_factory=list)


class HighAttentionItemSave(BaseModel):
    high_attention_item_id: int
    title: str = Field(min_length=1, max_length=220)
    summary: str = Field(min_length=1, max_length=1800)


class HighAttentionItemsSave(BaseModel):
    items: list[HighAttentionItemSave] = Field(default_factory=list, max_length=4)


class LoginRequest(BaseModel):
    username: str
    password: str


class SelfProfileSave(BaseModel):
    full_name: str | None = Field(default=None, max_length=240)
    password: str | None = None


class CrawlerChannelsSave(BaseModel):
    channels: list[str] = Field(default_factory=list)


class CrawlerEnabledSave(BaseModel):
    enabled: bool


class AutomationStageStart(BaseModel):
    start_date_jalali: str = Field(min_length=8, max_length=32)
    start_time: str = Field(min_length=4, max_length=8)
    timezone: str = "Asia/Tehran"


_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PM_MARKERS = ("pm", "p.m", "p.m.", "ب.ظ", "ب ظ", "بعدازظهر", "بعد از ظهر")
_AM_MARKERS = ("am", "a.m", "a.m.", "ق.ظ", "ق ظ", "قبل‌ازظهر", "قبل از ظهر")


def _normalize_clock_24h(value: str | None) -> str | None:
    """Accept 24h or 12h (AM/PM / قبل‌ازظهر) clocks and return HH:MM."""

    raw = str(value or "").translate(_DIGIT_TRANSLATION).strip()
    if not raw:
        return None
    lowered = raw.lower().replace("٫", ":")
    is_pm = any(marker in lowered for marker in _PM_MARKERS)
    is_am = any(marker in lowered for marker in _AM_MARKERS)
    stripped = re.sub(
        r"(a\.?m\.?|p\.?m\.?|ق\.?\s*ظ\.?|ب\.?\s*ظ\.?|قبل‌?ازظهر|بعدازظهر|قبل از ظهر|بعد از ظهر)",
        "",
        lowered,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(r"[.\-]", ":", stripped)
    stripped = re.sub(r"\s+", "", stripped)
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", stripped)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    second = int(match.group(3) or 0)
    if is_pm and hour < 12:
        hour += 12
    if is_am and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _split_jalali_datetime(value: str) -> tuple[str, str | None]:
    clean = re.sub(r"\s+", " ", str(value or "").translate(_DIGIT_TRANSLATION).strip())
    if not clean:
        return "", None
    match = re.match(r"^(\d{4}[/-]\d{1,2}[/-]\d{1,2})(?:[ T]+(.+))?$", clean)
    if not match:
        return clean, None
    return match.group(1), match.group(2)


def _jalali_window_utc(
    date_jalali: str | None,
    clock: str | None,
    timezone_name: str,
    *,
    end_of_day: bool,
) -> str | None:
    if not date_jalali:
        return None
    date_part, embedded_clock = _split_jalali_datetime(date_jalali)
    resolved_clock = _normalize_clock_24h(clock) or _normalize_clock_24h(embedded_clock)
    stamp = f"{date_part} {resolved_clock}" if resolved_clock else date_part
    return _jalali_local_to_utc_iso(
        stamp,
        timezone_name,
        end_of_day=end_of_day if not resolved_clock else True if end_of_day else False,
    )


def _jalali_local_to_utc_iso(value: str, timezone_name: str, *, end_of_day: bool = False) -> str:
    clean = str(value or "").translate(_DIGIT_TRANSLATION).strip()
    clean = re.sub(r"\s+", " ", clean)
    date_part, clock_part = _split_jalali_datetime(clean)
    normalized_clock = _normalize_clock_24h(clock_part)
    if clock_part and not normalized_clock:
        raise ValueError("ساعت باید ۲۴ساعته و مانند ۱۳:۳۰ باشد.")
    if normalized_clock:
        clean = f"{date_part} {normalized_clock}"
    match = re.fullmatch(
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[ T]+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?",
        clean,
    )
    if not match:
        raise ValueError("تاریخ جلالی باید مانند ۱۴۰۵/۰۴/۲۹ ۱۳:۳۰ یا 1405/04/29 13:30 باشد.")
    year, month, day = (int(match.group(i)) for i in range(1, 4))
    if match.group(4) is None:
        hour, minute, second = (23, 59, 59) if end_of_day else (0, 0, 0)
    else:
        hour = int(match.group(4))
        minute = int(match.group(5))
        second = int(match.group(6) or 0)
        if end_of_day and match.group(6) is None:
            second = 59
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError("ساعت واردشده نامعتبر است.")
    try:
        local_naive = jdatetime.datetime(year, month, day, hour, minute, second).togregorian()
        if end_of_day and match.group(4) is None:
            local_naive = local_naive.replace(microsecond=999999)
        elif end_of_day and match.group(6) is None:
            local_naive = local_naive.replace(second=59, microsecond=999999)
        local = local_naive.replace(tzinfo=ZoneInfo(timezone_name))
    except Exception as exc:
        raise ValueError(f"تاریخ جلالی یا منطقه زمانی نامعتبر است: {exc}") from exc
    return comparable_utc_iso(
        local.astimezone(timezone.utc).isoformat(),
        end_of_instant=end_of_day,
    ) or local.astimezone(timezone.utc).isoformat()


def _finalization_time_to_utc_iso(payload: EditorialFinalize) -> str | None:
    if payload.finalized_at_jalali and payload.finalized_date_jalali:
        raise ValueError("زمان نهایی‌سازی را فقط با یکی از روش‌های ورودی وارد کنید.")
    if payload.finalized_at_jalali:
        return _jalali_local_to_utc_iso(
            payload.finalized_at_jalali,
            payload.timezone,
            end_of_day=False,
        )
    if payload.finalized_date_jalali:
        time_value = _normalize_clock_24h(payload.finalized_time)
        if not time_value:
            raise ValueError("برای تاریخ نهایی‌سازی، ساعت و دقیقه را نیز وارد کنید.")
        return _jalali_local_to_utc_iso(
            f"{payload.finalized_date_jalali} {time_value}",
            payload.timezone,
            end_of_day=False,
        )
    if payload.finalized_time:
        raise ValueError("برای ساعت نهایی‌سازی، تاریخ جلالی را نیز وارد کنید.")
    if not payload.finalized_at:
        return None
    try:
        parsed = datetime.fromisoformat(payload.finalized_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(payload.timezone))
    except Exception as exc:
        raise ValueError(f"زمان نهایی‌سازی نامعتبر است: {exc}") from exc
    return parsed.astimezone(timezone.utc).isoformat()


def _automation_start_to_utc_iso(payload: AutomationStageStart) -> str:
    """Validate the required Jalali date/time boundary for one stage."""
    time_value = _normalize_clock_24h(payload.start_time)
    if not time_value:
        raise ValueError("ساعت شروع باید مانند ۱۳:۳۰ وارد شود.")
    return _jalali_local_to_utc_iso(
        f"{payload.start_date_jalali} {time_value}",
        payload.timezone,
        end_of_day=False,
    )


def create_dashboard_router(
    db: Database,
    settings: Settings,
    bulletins: BulletinService,
    scheduler: BulletinScheduler,
    editorial_automation: EditorialAutomationService,
    get_target_chat_id: Callable[[], Any],
    bot_queue_recovery_runner: Callable[[int, int], Awaitable[None]] | None = None,
    bale: BaleClient | None = None,
) -> APIRouter:
    router = APIRouter()
    bot_queue_recovery_task: asyncio.Task[None] | None = None

    def required_permission(request: Request) -> str:
        path = request.url.path
        method = request.method.upper()
        if path.startswith("/admin/api/api-keys"):
            return "dashboard.view"
        if path.startswith("/admin/api/users/senders") or path == "/admin/api/users/roles":
            return "dashboard.view"
        if path.startswith("/admin/api/users/") and path.endswith("/profile") and method == "GET":
            return "dashboard.view"
        if path.startswith("/admin/api/users"):
            return "users.manage"
        if path.startswith("/admin/user-portraits"):
            return "dashboard.view"
        if path.startswith("/admin/api/system/update"):
            return "system.manage"
        if path.startswith("/admin/api/ai-profiles"):
            return "settings.manage"
        if path.startswith("/admin/api/crawler") and method != "GET":
            return "settings.manage"
        if path.startswith("/admin/api/sources") and method != "GET":
            return "sources.manage"
        if path.startswith("/admin/api/people") and method != "GET":
            return "people.manage"
        if path.startswith("/admin/api/analysis") and method != "GET":
            return "people.manage"
        if path.startswith("/admin/api/editorial-automation") and method != "GET":
            return "editorial.manage"
        if path.startswith("/admin/api/messages") and method != "GET":
            return "messages.review"
        if path.startswith("/admin/api/editorial-drafts") and method != "GET":
            return "editorial.manage"
        if path.startswith("/admin/api/high-attention") and method != "GET":
            return "editorial.manage"
        if path.startswith("/admin/api/editorial-bulletins"):
            return "bulletins.manage"
        if path.startswith("/admin/download/"):
            return "bulletins.export"
        if path.startswith("/admin/api/bulletin") and method != "GET":
            return "bulletins.manage"
        if path.startswith("/admin/api/templates") and method != "GET":
            return "settings.manage"
        if path.startswith("/admin/api/schedules") and method != "GET":
            return "settings.manage"
        if path.startswith("/admin/api/backup"):
            return "system.manage"
        return "dashboard.view"

    def request_api_token(request: Request) -> str | None:
        auth = str(request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token:
                return token
        header = str(
            request.headers.get("x-api-key") or request.headers.get("X-Api-Key") or ""
        ).strip()
        return header or None

    async def resolve_principal(
        request: Request,
        credentials: HTTPBasicCredentials | None,
    ) -> AdminPrincipal | None:
        if not settings.dashboard_enabled:
            raise HTTPException(status_code=404)
        principal = await db.principal_from_session(
            request.cookies.get("garaye_session")
        )
        if principal:
            return principal
        api_token = request_api_token(request)
        if api_token:
            principal = await db.principal_from_api_key(api_token)
            if principal:
                return principal
        bootstrap_valid = bool(
            credentials
            and secrets.compare_digest(credentials.username, settings.admin_username)
            and secrets.compare_digest(credentials.password, settings.admin_password)
        )
        if bootstrap_valid:
            principal = AdminPrincipal(
                user_id=None,
                username=settings.admin_username,
                full_name="مدیر سامانه",
                role="superadmin",
            )
        elif credentials and not principal:
            principal = await db.authenticate_admin_user(
                credentials.username, credentials.password
            )
        return principal

    async def admin_identity(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> str:
        principal = await resolve_principal(request, credentials)
        if not principal:
            headers = {}
            if not request_api_token(request):
                headers["WWW-Authenticate"] = 'Basic realm="Garaye Dashboard"'
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="نام کاربری یا رمز عبور نادرست است.",
                headers=headers,
            )
        permission = required_permission(request)
        if not principal.can(permission):
            raise HTTPException(
                status_code=403,
                detail=f"حساب شما مجوز «{permission}» را ندارد.",
            )
        request.state.admin_principal = principal
        return principal.username

    async def audit(request: Request, actor: str, action: str, **kwargs: Any) -> None:
        await db.add_dashboard_audit(
            actor,
            action,
            ip_address=request.client.host if request.client else None,
            **kwargs,
        )

    def can_view_user_profile(request: Request, user_id: int) -> bool:
        principal: AdminPrincipal = request.state.admin_principal
        if principal.can("users.manage"):
            return True
        return principal.user_id is not None and int(principal.user_id) == int(user_id)

    async def store_user_portrait(
        user_id: int,
        file: UploadFile,
        request: Request,
        actor: str,
    ) -> dict[str, Any]:
        user = await db.get_admin_user(user_id=user_id)
        if not user:
            raise HTTPException(404, "کاربر پیدا نشد.")
        content = await file.read()
        if not content or len(content) > 5 * 1024 * 1024:
            raise HTTPException(422, "حجم تصویر باید کمتر از پنج مگابایت باشد.")
        try:
            image = Image.open(io.BytesIO(content))
            image.verify()
            image = Image.open(io.BytesIO(content)).convert("RGB")
            image.thumbnail((1000, 1000))
        except Exception as exc:
            raise HTTPException(422, f"فایل تصویر معتبر نیست: {exc}") from exc
        portrait_dir = settings.bulletin_output_dir.parent / "user_portraits"
        portrait_dir.mkdir(parents=True, exist_ok=True)
        path = portrait_dir / f"user_{user_id}.jpg"
        image.save(path, format="JPEG", quality=90, optimize=True)
        revision = await db.mark_admin_user_avatar_updated(user_id)
        await audit(
            request,
            actor,
            "admin_user_portrait_saved",
            object_type="admin_user",
            object_id=str(user_id),
            details={"path": str(path), "revision": revision},
        )
        return {
            "ok": True,
            "user_id": user_id,
            "avatar_updated_at": revision,
            "url": f"/admin/user-portraits/{user_id}?v={revision}",
        }

    async def run_bulletin_export(run_id: int, actor: str) -> dict[str, Any]:
        try:
            result = await bulletins.export_run(run_id, actor=actor)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            await db.mark_bulletin_export_state(
                run_id, stage="export_failed", error_text=error_message
            )
            await db.add_bulletin_run_log(
                run_id,
                "manual_export",
                status="failed",
                level="ERROR",
                message="تولید خروجی خبرنامه ناموفق بود.",
                details={"error": error_message},
            )
            await db.add_system_event(
                "bulletin",
                "ERROR",
                "manual_export_failed",
                error_message,
                {"run_id": run_id},
            )
            raise
        await db.mark_bulletin_export_state(
            run_id, stage="exports_ready", error_text=None
        )
        await db.add_bulletin_run_log(
            run_id,
            "manual_export",
            status="completed",
            message="خروجی‌های صفحه‌آرایی خبرنامه آماده شدند.",
            details={"files": sorted(result.get("files", {}).keys())},
        )
        return result

    async def export_manual_run(run_id: int, actor: str) -> None:
        try:
            await run_bulletin_export(run_id, actor)
        except Exception:
            # The operation details have been persisted by run_bulletin_export.
            # BackgroundTasks must not propagate an unobserved exception.
            return

    @router.get("/login")
    async def login_page(request: Request) -> Response:
        principal = await db.principal_from_session(
            request.cookies.get("garaye_session")
        )
        if principal:
            return RedirectResponse("/admin", status_code=303)
        return FileResponse(WEB_ROOT / "login.html", media_type="text/html; charset=utf-8")

    @router.get("/auth/assets/fonts/IRZar.ttf")
    async def login_font() -> FileResponse:
        font_path = WEB_ROOT / "assets" / "fonts" / "IRZar.ttf"
        if not font_path.is_file():
            raise HTTPException(404, "فایل فونت IRZar روی سرور نصب نشده است.")
        return FileResponse(font_path, media_type="font/ttf")

    @router.get("/auth/assets/fonts/Shabnam.ttf")
    async def login_shabnam_font() -> FileResponse:
        font_path = WEB_ROOT / "assets" / "fonts" / "Shabnam.ttf"
        if not font_path.is_file():
            raise HTTPException(404, "فایل فونت Shabnam روی سرور نصب نشده است.")
        return FileResponse(font_path, media_type="font/ttf")

    @router.post("/auth/login")
    async def login(payload: LoginRequest, request: Request) -> Response:
        username = payload.username.strip()
        principal = await db.authenticate_admin_user(username, payload.password)
        # The environment bootstrap credential remains a recoverable root login.
        # This also makes an intentional ADMIN_PASSWORD rotation effective even
        # when the database was initialized with an older bootstrap password.
        if (
            not principal
            and secrets.compare_digest(username, settings.admin_username)
            and secrets.compare_digest(payload.password, settings.admin_password)
        ):
            row = await db.get_admin_user(username=settings.admin_username)
            if row and int(row.get("active") or 0):
                principal = AdminPrincipal(
                    user_id=int(row["user_id"]),
                    username=str(row["username"]),
                    full_name=str(row["full_name"]),
                    role="superadmin",
                )
        if not principal:
            raise HTTPException(401, "نام کاربری یا رمز عبور نادرست است.")
        token = await db.create_admin_session(
            principal,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        response = JSONResponse(
            {
                "ok": True,
                "username": principal.username,
                "full_name": principal.full_name,
                "role": principal.role,
                "permissions": sorted(principal.permissions),
            }
        )
        response.set_cookie(
            "garaye_session",
            token,
            max_age=12 * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return response

    @router.post("/auth/logout")
    async def logout(request: Request) -> Response:
        await db.revoke_admin_session(request.cookies.get("garaye_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("garaye_session", path="/")
        return response

    @router.get("/admin")
    async def dashboard_home(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> Response:
        principal = await resolve_principal(request, credentials)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        if not principal.can("dashboard.view"):
            raise HTTPException(403, "حساب شما مجوز مشاهده داشبورد را ندارد.")
        return FileResponse(
            WEB_ROOT / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @router.get("/admin/assets/{asset_path:path}")
    async def dashboard_asset(
        asset_path: str,
        _: str = Depends(admin_identity),
    ) -> FileResponse:
        candidate = (WEB_ROOT / "assets" / asset_path).resolve()
        assets_root = (WEB_ROOT / "assets").resolve()
        try:
            candidate.relative_to(assets_root)
        except ValueError as exc:
            raise HTTPException(404) from exc
        if not candidate.is_file():
            raise HTTPException(404, "فایل رابط کاربری پیدا نشد.")
        return FileResponse(candidate, headers={"Cache-Control": "no-store, max-age=0"})

    @router.get("/admin/api/stats")
    async def stats(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return await db.dashboard_stats()

    @router.get("/admin/api/monitoring")
    async def monitoring(
        days: int = Query(30, ge=7, le=90),
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        return await db.monitoring_sender_summary(days=days)

    @router.get("/admin/api/garaye-insights")
    async def garaye_insights(
        days: int = Query(30, ge=1, le=3660),
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            if time_from and not date_from_jalali:
                raise ValueError("برای ساعت آغاز، تاریخ آغاز را نیز وارد کنید.")
            if time_to and not date_to_jalali:
                raise ValueError("برای ساعت پایان، تاریخ پایان را نیز وارد کنید.")
            date_from = (
                _jalali_local_to_utc_iso(
                    f"{date_from_jalali} {time_from}" if time_from else date_from_jalali,
                    "Asia/Tehran",
                    end_of_day=False,
                )
                if date_from_jalali
                else None
            )
            date_to = (
                _jalali_local_to_utc_iso(
                    f"{date_to_jalali} {time_to}" if time_to else date_to_jalali,
                    "Asia/Tehran",
                    end_of_day=not bool(time_to),
                )
                if date_to_jalali
                else None
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.garaye_insights(
            date_from=date_from, date_to=date_to, days=days
        )

    @router.get("/admin/api/me")
    async def current_admin(
        request: Request,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        principal: AdminPrincipal = request.state.admin_principal
        user = (
            await db.get_admin_user(user_id=principal.user_id)
            if principal.user_id is not None
            else None
        )
        return {
            "user_id": principal.user_id,
            "username": principal.username,
            "full_name": principal.full_name,
            "role": principal.role,
            "role_title": ROLE_TITLES.get(principal.role, principal.role),
            "permissions": sorted(principal.permissions),
            "active": int((user or {}).get("active") or 1),
            "sender_key": (user or {}).get("sender_key"),
            "avatar_updated_at": (user or {}).get("avatar_updated_at"),
            "created_at": (user or {}).get("created_at"),
            "last_login_at": (user or {}).get("last_login_at"),
            "version": current_version(),
        }

    def require_named_account(request: Request) -> int:
        principal: AdminPrincipal = request.state.admin_principal
        if principal.user_id is None:
            raise HTTPException(
                422,
                "ساخت و مدیریت کلید API فقط برای حساب‌های ثبت‌شده در سامانه ممکن است.",
            )
        return int(principal.user_id)

    @router.get("/admin/api/api-keys")
    async def list_api_keys(
        request: Request,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        user_id = require_named_account(request)
        return {"items": await db.list_api_keys(user_id)}

    @router.post("/admin/api/api-keys")
    async def create_api_key(
        payload: ApiKeyCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        user_id = require_named_account(request)
        try:
            created = await db.create_api_key(user_id, payload.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "api_key_created",
            object_type="api_key",
            object_id=str(created["api_key_id"]),
            details={"name": created["name"], "token_prefix": created["token_prefix"]},
        )
        return created

    @router.delete("/admin/api/api-keys/{api_key_id}")
    async def revoke_api_key(
        api_key_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, bool]:
        user_id = require_named_account(request)
        ok = await db.revoke_api_key(user_id, api_key_id)
        if not ok:
            raise HTTPException(404, "کلید پیدا نشد.")
        await audit(
            request,
            actor,
            "api_key_revoked",
            object_type="api_key",
            object_id=str(api_key_id),
        )
        return {"ok": True}

    @router.get("/admin/api/eitan-axes")
    async def list_eitan_axes(
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        return {"items": await db.list_eitan_axes()}

    @router.post("/admin/api/eitan-axes")
    async def create_eitan_axis(
        request: Request,
        actor: str = Depends(admin_identity),
        title: str = Form(...),
        keywords_file: UploadFile = File(...),
        people_file: UploadFile = File(...),
    ) -> dict[str, Any]:
        try:
            keywords_text = parse_eitan_upload(
                keywords_file.filename,
                await keywords_file.read(),
            )
            people_text = parse_eitan_upload(
                people_file.filename,
                await people_file.read(),
            )
            created = await db.create_eitan_axis(
                title=title,
                keywords_text=keywords_text,
                people_text=people_text,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "eitan_axis_created",
            object_type="eitan_axis",
            object_id=str(created.get("axis_id") or ""),
            details={"title": created.get("title")},
        )
        return created

    @router.get("/admin/api/eitan-axes/{axis_id}/messages")
    async def eitan_axis_messages(
        axis_id: str,
        limit: int = Query(40, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        axis = await db.get_eitan_axis(axis_id)
        if not axis:
            raise HTTPException(404, "محور پیدا نشد.")
        terms = db.eitan_search_terms(axis)
        result = await db.list_messages_dashboard(
            terms=terms,
            limit=limit,
            offset=offset,
        )
        return {
            **result,
            "axis": eitan_axis_public(axis),
            "terms_count": len(terms),
        }

    @router.get("/admin/api/me/profile")
    async def current_admin_profile(
        request: Request,
        days: int = Query(30, ge=7, le=90),
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        principal: AdminPrincipal = request.state.admin_principal
        if principal.user_id is None:
            return {
                "user_id": None,
                "username": principal.username,
                "full_name": principal.full_name,
                "role": principal.role,
                "role_title": ROLE_TITLES.get(principal.role, principal.role),
                "active": 1,
                "sender_key": None,
                "avatar_updated_at": None,
                "created_at": None,
                "last_login_at": None,
                "metrics": {
                    "total_messages": 0,
                    "window_messages": 0,
                    "rated_messages": 0,
                    "average_rating": None,
                    "daily": {},
                    "sender_name": None,
                },
                "window_days": days,
                "self": True,
            }
        profile = await db.get_admin_user_profile(principal.user_id, days=days)
        if not profile:
            raise HTTPException(404, "پروفایل کاربر پیدا نشد.")
        profile["self"] = True
        return profile

    @router.post("/admin/api/me")
    async def save_current_admin_profile(
        payload: SelfProfileSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        principal: AdminPrincipal = request.state.admin_principal
        if principal.user_id is None:
            raise HTTPException(422, "حساب بازیابی‌شده از فایل محیط قابل ویرایش از پرتال نیست.")
        try:
            user = await db.update_self_profile(
                principal.user_id,
                full_name=payload.full_name,
                password=payload.password,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "self_profile_saved",
            object_type="admin_user",
            object_id=str(principal.user_id),
            details={"full_name_changed": payload.full_name is not None, "password_changed": bool(payload.password)},
        )
        return {"ok": True, "user_id": int(user["user_id"]), "full_name": user.get("full_name")}

    @router.post("/admin/api/me/portrait")
    async def upload_own_portrait(
        request: Request,
        file: UploadFile = File(...),
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        principal: AdminPrincipal = request.state.admin_principal
        if principal.user_id is None:
            raise HTTPException(422, "برای این حساب تصویر پروفایل ذخیره نمی‌شود.")
        return await store_user_portrait(principal.user_id, file, request, actor)

    @router.get("/admin/api/users")
    async def admin_users(
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        return await db.list_admin_users()

    @router.get("/admin/api/users/roles")
    async def admin_roles(
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": role,
                "title": ROLE_TITLES[role],
                "permissions": sorted(ROLE_PERMISSIONS[role]),
            }
            for role in sorted(VALID_ROLES)
        ]

    @router.get("/admin/api/users/senders")
    async def admin_user_senders(
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        return await db.list_sender_profile_candidates()

    @router.get("/admin/api/users/{user_id}/profile")
    async def admin_user_profile(
        user_id: int,
        request: Request,
        days: int = Query(30, ge=7, le=90),
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        profile = await db.get_admin_user_profile(user_id, days=days)
        if not profile:
            raise HTTPException(404, "پروفایل کاربر پیدا نشد.")
        if not can_view_user_profile(request, user_id):
            raise HTTPException(403, "اجازه مشاهده این پروفایل را ندارید.")
        profile["self"] = request.state.admin_principal.user_id == user_id
        return profile

    @router.post("/admin/api/users/{user_id}/portrait")
    async def upload_admin_user_portrait(
        user_id: int,
        request: Request,
        file: UploadFile = File(...),
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        return await store_user_portrait(user_id, file, request, actor)

    @router.get("/admin/user-portraits/{user_id}")
    async def admin_user_portrait(
        user_id: int,
        request: Request,
        _: str = Depends(admin_identity),
    ) -> FileResponse:
        if not await db.get_admin_user(user_id=user_id):
            raise HTTPException(404, "کاربر پیدا نشد.")
        if not can_view_user_profile(request, user_id):
            raise HTTPException(403, "اجازه مشاهده این تصویر را ندارید.")
        path = settings.bulletin_output_dir.parent / "user_portraits" / f"user_{user_id}.jpg"
        if not path.is_file():
            raise HTTPException(404, "تصویر پروفایل ثبت نشده است.")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/admin/api/users")
    async def save_admin_user(
        payload: AdminUserSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        principal: AdminPrincipal = request.state.admin_principal
        current = (
            await db.get_admin_user(user_id=payload.user_id)
            if payload.user_id is not None
            else None
        )
        if (
            current
            and principal.user_id is not None
            and int(current["user_id"]) == principal.user_id
            and not payload.active
        ):
            raise HTTPException(422, "نمی‌توانید حساب فعال خودتان را غیرفعال کنید.")
        if (
            current
            and str(current.get("role")) == "superadmin"
            and (payload.role != "superadmin" or not payload.active)
        ):
            active_admins = [
                row
                for row in await db.list_admin_users()
                if int(row.get("active") or 0)
                and row.get("role") == "superadmin"
            ]
            if len(active_admins) <= 1:
                raise HTTPException(422, "حداقل یک مدیرکل فعال باید باقی بماند.")
        try:
            user_id = await db.save_admin_user(
                user_id=payload.user_id,
                username=payload.username,
                full_name=payload.full_name,
                role=payload.role,
                active=payload.active,
                password=payload.password,
                sender_key=payload.sender_key,
                actor=actor,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "admin_user_saved",
            object_type="admin_user",
            object_id=str(user_id),
            details={
                "username": payload.username,
                "role": payload.role,
                "active": payload.active,
                "sender_key": payload.sender_key,
                "password_changed": bool(payload.password),
            },
        )
        return {"ok": True, "user_id": user_id}

    @router.get("/admin/api/messages")
    async def messages(
        status_value: str | None = Query(None, alias="status"),
        source_chat_id: int | None = None,
        q: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        limit: int = 500,
        offset: int = 0,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            effective_from = (
                _jalali_window_utc(date_from_jalali, time_from, "Asia/Tehran", end_of_day=False)
                if date_from_jalali
                else date_from
            )
            effective_to = (
                _jalali_window_utc(date_to_jalali, time_to, "Asia/Tehran", end_of_day=True)
                if date_to_jalali
                else date_to
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.list_messages_dashboard(
            status=status_value,
            source_chat_id=source_chat_id,
            query=q,
            date_from=effective_from,
            date_to=effective_to,
            limit=limit,
            offset=offset,
        )

    @router.get("/admin/api/messages/ids")
    async def message_ids(
        status_value: str | None = Query(None, alias="status"),
        source_chat_id: int | None = None,
        q: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            effective_from = (
                _jalali_window_utc(date_from_jalali, time_from, "Asia/Tehran", end_of_day=False)
                if date_from_jalali
                else date_from
            )
            effective_to = (
                _jalali_window_utc(date_to_jalali, time_to, "Asia/Tehran", end_of_day=True)
                if date_to_jalali
                else date_to
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.list_message_ids_dashboard(
            status=status_value,
            source_chat_id=source_chat_id,
            query=q,
            date_from=effective_from,
            date_to=effective_to,
        )

    @router.get("/admin/api/messages/progress")
    async def message_window_progress(
        date_from: str | None = None,
        date_to: str | None = None,
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            effective_from = (
                _jalali_window_utc(date_from_jalali, time_from, "Asia/Tehran", end_of_day=False)
                if date_from_jalali
                else date_from
            )
            effective_to = (
                _jalali_window_utc(date_to_jalali, time_to, "Asia/Tehran", end_of_day=True)
                if date_to_jalali
                else date_to
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.message_window_progress(
            date_from=effective_from,
            date_to=effective_to,
        )

    @router.post("/admin/api/messages/analyze")
    async def analyze_selected_messages(
        payload: MessageAnalysisBatch,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await bulletins.analyze_selected_messages(
                payload.message_ids,
                actor=actor,
            )
            resolved_ids = [
                int(item["message_id"])
                for item in result.get("results", [])
                if item.get("ok") and item.get("message_id") is not None
            ]
            result["human_control_candidates"] = await editorial_automation.reconcile_speakers(
                resolved_ids
            )
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        await audit(
            request,
            actor,
            "selected_messages_ai_analyzed",
            object_type="message_batch",
            details={
                "message_ids": payload.message_ids,
                "succeeded": result.get("succeeded"),
                "failed": result.get("failed"),
                "prompt_version": result.get("prompt_version"),
            },
        )
        return result

    @router.get("/admin/api/editorial-automation")
    async def editorial_automation_status(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return await editorial_automation.status()

    @router.post("/admin/api/editorial-automation/analysis/start")
    async def start_editorial_automation(
        payload: AutomationStageStart,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            start_at = _automation_start_to_utc_iso(payload)
            state = await editorial_automation.enable_analysis(start_at=start_at)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await db.add_system_event(
            "editorial_automation", "INFO", "analysis_automation_started",
            "Operator enabled the ten-minute first-stage analysis.",
            {"actor": actor, "start_at": start_at},
        )
        await audit(
            request, actor, "editorial_automation_analysis_started",
            object_type="editorial_automation", details={"start_at": start_at},
        )
        return state

    @router.post("/admin/api/editorial-automation/analysis/stop")
    async def stop_editorial_automation(
        request: Request, actor: str = Depends(admin_identity)
    ) -> dict[str, Any]:
        state = await editorial_automation.disable_analysis()
        await db.add_system_event(
            "editorial_automation", "INFO", "analysis_automation_stopped",
            "Operator paused the automated editorial workflow.", {"actor": actor},
        )
        await audit(request, actor, "editorial_automation_stopped", object_type="editorial_automation")
        return state

    @router.post("/admin/api/editorial-automation/drafts/start")
    async def start_editorial_drafts_automation(
        payload: AutomationStageStart,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            start_at = _automation_start_to_utc_iso(payload)
            state = await editorial_automation.enable_drafts(start_at=start_at)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await db.add_system_event(
            "editorial_automation", "INFO", "draft_automation_started",
            "Operator enabled the three-hour second-stage draft generation.",
            {"actor": actor, "start_at": start_at},
        )
        await audit(
            request, actor, "editorial_automation_drafts_started",
            object_type="editorial_automation", details={"start_at": start_at},
        )
        return state

    @router.post("/admin/api/editorial-automation/drafts/stop")
    async def stop_editorial_drafts_automation(
        request: Request, actor: str = Depends(admin_identity)
    ) -> dict[str, Any]:
        state = await editorial_automation.disable_drafts()
        await db.add_system_event(
            "editorial_automation", "INFO", "draft_automation_stopped",
            "Operator paused the automated second-stage draft generation.", {"actor": actor},
        )
        await audit(
            request, actor, "editorial_automation_drafts_stopped",
            object_type="editorial_automation",
        )
        return state

    @router.post("/admin/api/editorial-automation/analysis/run-now")
    async def run_editorial_analysis_now(
        request: Request, actor: str = Depends(admin_identity)
    ) -> dict[str, Any]:
        try:
            result = await editorial_automation.run_analysis_now()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        await audit(request, actor, "editorial_automation_analysis_run_now", object_type="editorial_automation")
        return result

    @router.post("/admin/api/editorial-automation/drafts/run-now")
    async def run_editorial_drafts_now(
        request: Request, actor: str = Depends(admin_identity)
    ) -> dict[str, Any]:
        try:
            result = await editorial_automation.run_drafts_now()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        await audit(request, actor, "editorial_automation_draft_run_now", object_type="editorial_automation")
        return result

    @router.get("/admin/api/analysis/filters")
    async def analysis_filters(
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        timezone_name: str = Query("Asia/Tehran", alias="timezone"),
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            date_from = _jalali_window_utc(date_from_jalali, time_from, timezone_name, end_of_day=False) if date_from_jalali else None
            date_to = _jalali_window_utc(date_to_jalali, time_to, timezone_name, end_of_day=True) if date_to_jalali else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.analysis_filter_catalog(date_from=date_from, date_to=date_to)

    @router.get("/admin/api/analysis/messages")
    async def analyzed_messages(
        speaker: str | None = None,
        event_message_id: int | None = None,
        general_topic: str | None = None,
        specific_topic: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        timezone_name: str = Query("Asia/Tehran", alias="timezone"),
        limit: int = 2000,
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        try:
            effective_from = (
                _jalali_window_utc(date_from_jalali, time_from, timezone_name, end_of_day=False)
                if date_from_jalali
                else date_from
            )
            effective_to = (
                _jalali_window_utc(date_to_jalali, time_to, timezone_name, end_of_day=True)
                if date_to_jalali
                else date_to
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.list_analyzed_messages(
            speaker=speaker,
            event_message_id=event_message_id,
            general_topic=general_topic,
            specific_topic=specific_topic,
            date_from=effective_from,
            date_to=effective_to,
            limit=limit,
        )

    @router.post("/admin/api/analysis/messages/{message_id}/discard")
    async def discard_analyzed_message(
        message_id: int,
        payload: AnalyzedMessageDiscard,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.discard_analyzed_message(
                message_id,
                actor=actor,
                reason=payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "analyzed_message_discarded",
            object_type="message",
            object_id=str(message_id),
            details={"reason": payload.reason},
        )
        return result

    @router.put("/admin/api/analysis/messages/{message_id}/speaker")
    async def correct_analyzed_speaker(
        message_id: int,
        payload: AnalyzedSpeakerCorrection,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.correct_analyzed_message_speaker(
                message_id,
                speaker_name=payload.speaker_name,
                position=payload.position,
                actor=actor,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "analyzed_message_speaker_corrected",
            object_type="message",
            object_id=str(message_id),
            details={"speaker_name": result.get("speaker_name")},
        )
        return result

    @router.post("/admin/api/analysis/messages/{message_id}/speakers/{tag_id}/promote-person")
    async def promote_analyzed_speaker(
        message_id: int,
        tag_id: int,
        request: Request,
        payload: AnalyzedSpeakerPromotion | None = None,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.promote_analyzed_speaker_to_registry(
                message_id,
                tag_id,
                actor=actor,
                category=payload.category if payload else None,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "analyzed_speaker_promoted_to_registry",
            object_type="person",
            object_id=str(result["person_id"]),
            details={
                "message_id": message_id,
                "tag_id": tag_id,
                "created": result["created"],
                "linked_tag_count": result["linked_tag_count"],
            },
        )
        return result

    @router.get("/admin/api/messages/{message_id}")
    async def message_detail(message_id: int, _: str = Depends(admin_identity)) -> dict[str, Any]:
        item = await db.message_detail(message_id)
        if not item:
            raise HTTPException(404, "پیام پیدا نشد.")
        return item

    @router.get("/admin/api/messages/{message_id}/media/{media_index}")
    async def message_media(
        message_id: int,
        media_index: int,
        request: Request,
        _: str = Depends(admin_identity),
    ) -> StreamingResponse:
        if bale is None or not bale.enabled:
            raise HTTPException(503, "اتصال بله برای دریافت رسانه در دسترس نیست.")
        item = await db.get_message(message_id)
        if not item:
            raise HTTPException(404, "پیام پیدا نشد.")
        assets = message_media_assets(item)
        if media_index < 0 or media_index >= len(assets):
            raise HTTPException(404, "رسانه پیدا نشد.")
        asset = assets[media_index]
        file_id = str(asset.get("file_id") or "").strip()
        if not file_id:
            raise HTTPException(404, "شناسه فایل رسانه موجود نیست.")
        if asset.get("too_large"):
            raise HTTPException(413, "حجم این فایل از سقف ۲۰ مگابایت بله بیشتر است.")
        try:
            info = await bale.get_file(file_id)
        except BaleAPIError as exc:
            raise HTTPException(502, f"دریافت فایل از بله ممکن نشد: {exc}") from exc
        file_path = str(info.get("file_path") or "").strip()
        if not file_path:
            raise HTTPException(404, "مسیر فایل از بله برنگشت.")
        upstream_headers: dict[str, str] = {}
        range_header = request.headers.get("range")
        if range_header:
            upstream_headers["Range"] = range_header
        try:
            upstream = await bale.open_file_stream(file_path, headers=upstream_headers or None)
        except BaleAPIError as exc:
            raise HTTPException(502, f"دانلود فایل از بله ممکن نشد: {exc}") from exc
        if upstream.status_code >= 400:
            await upstream.aclose()
            raise HTTPException(502, "دانلود فایل از بله ناموفق بود.")
        mime = (
            str(asset.get("mime_type") or "").strip()
            or str(upstream.headers.get("content-type") or "").split(";")[0].strip()
            or "application/octet-stream"
        )
        headers: dict[str, str] = {
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        }
        accept_ranges = upstream.headers.get("accept-ranges")
        if accept_ranges:
            headers["Accept-Ranges"] = accept_ranges
        elif range_header or mime.startswith(("video/", "audio/")):
            headers["Accept-Ranges"] = "bytes"
        if upstream.headers.get("content-range"):
            headers["Content-Range"] = upstream.headers["content-range"]
        if upstream.headers.get("content-length"):
            headers["Content-Length"] = upstream.headers["content-length"]
        file_name = str(asset.get("file_name") or "").strip()
        if file_name:
            ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._")[:80] or "media"
            headers["Content-Disposition"] = f'inline; filename="{ascii_name}"'

        async def iterator():
            try:
                async for chunk in upstream.aiter_bytes(64 * 1024):
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            iterator(),
            status_code=upstream.status_code,
            media_type=mime,
            headers=headers,
        )

    @router.put("/admin/api/messages/{message_id}/rating")
    async def save_message_rating(
        message_id: int,
        payload: MessageRatingSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        # Kept as an explicit, safe response for a browser that still has a
        # cached pre-v11.29 script. Scores are now calculated from the message
        # time, first-engine analysis and finalized-news usage.
        raise HTTPException(410, "امتیازدهی دستی حذف شده است؛ امتیاز به‌صورت خودکار محاسبه می‌شود.")

    @router.get("/admin/api/sources")
    async def sources(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_monitored_chats()

    @router.post("/admin/api/sources")
    async def add_source(
        payload: SourceCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        if payload.chat_id is None and not payload.username:
            raise HTTPException(422, "chat_id یا username لازم است.")
        if payload.chat_id is not None:
            item = await db.register_monitored_chat(
                {
                    "id": payload.chat_id,
                    "username": payload.username,
                    "title": payload.title,
                    "type": payload.chat_type,
                },
                target_chat_id=payload.target_chat_id,
                target_title=payload.target_title,
            )
        else:
            item = await db.register_monitored_username(
                payload.username or "",
                title=payload.title,
                target_chat_id=payload.target_chat_id,
                target_title=payload.target_title,
            )
        await audit(request, actor, "source_created", object_type="monitored_chat", object_id=str(item["id"]), details=item)
        return item

    @router.patch("/admin/api/sources/{source_id}")
    async def edit_source(
        source_id: int,
        payload: SourceUpdate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, bool]:
        await db.update_monitored_chat(
            source_id,
            enabled=payload.enabled,
            target_chat_id=payload.target_chat_id,
            target_title=payload.target_title,
            title=payload.title,
        )
        await audit(request, actor, "source_updated", object_type="monitored_chat", object_id=str(source_id), details=payload.model_dump())
        return {"ok": True}

    @router.get("/admin/api/templates")
    async def templates(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_bulletin_templates()

    @router.post("/admin/api/templates")
    async def save_template(
        payload: TemplateCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, int]:
        template_id = await db.save_bulletin_template(
            name=payload.name,
            system_prompt=payload.system_prompt,
            user_prompt_template=payload.user_prompt_template,
            output_format=payload.output_format,
            template_id=payload.id,
        )
        await audit(request, actor, "template_saved", object_type="bulletin_template", object_id=str(template_id))
        return {"id": template_id}

    @router.get("/admin/api/bulletins")
    async def list_bulletins(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_bulletin_runs()

    @router.post("/admin/api/bulletins")
    async def create_bulletin(
        payload: BulletinCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            date_from = (
                _jalali_local_to_utc_iso(payload.date_from_jalali, payload.timezone, end_of_day=False)
                if payload.date_from_jalali else payload.date_from
            )
            date_to = (
                _jalali_local_to_utc_iso(payload.date_to_jalali, payload.timezone, end_of_day=True)
                if payload.date_to_jalali else payload.date_to
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if date_from and date_to:
            try:
                if datetime.fromisoformat(date_from.replace("Z", "+00:00")) > datetime.fromisoformat(date_to.replace("Z", "+00:00")):
                    raise HTTPException(422, "ابتدای بازه نمی‌تواند بعد از انتهای بازه باشد.")
            except ValueError as exc:
                raise HTTPException(422, "قالب زمان ورودی نامعتبر است.") from exc
        run_id = await bulletins.create_run(
            requested_by=actor,
            template_id=payload.template_id,
            date_from=date_from,
            date_to=date_to,
            source_chat_ids=payload.source_chat_ids or None,
            statuses=payload.statuses,
            filters={"issue_number": payload.issue_number, "report_mode": payload.report_mode, "export_options": payload.export_options},
        )
        asyncio.create_task(bulletins.execute_run(run_id), name=f"bulletin-run-{run_id}")
        details = payload.model_dump()
        details.update({"normalized_date_from": date_from, "normalized_date_to": date_to})
        await audit(request, actor, "bulletin_created", object_type="bulletin_run", object_id=str(run_id), details=details)
        return {"id": run_id, "status": "queued", "date_from": date_from, "date_to": date_to}

    @router.delete("/admin/api/bulletins/{run_id}")
    async def delete_bulletin(
        run_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        run = await db.get_bulletin_run(run_id)
        if not run:
            raise HTTPException(404, "اجرای خبرنامه پیدا نشد.")
        active_stages = {"exports_queued", "build_json", "render_html_layout"}
        if str(run.get("status")) in {"queued", "running", "recovered"} or str(run.get("current_stage")) in active_stages:
            raise HTTPException(409, "این اجرا هنوز در حال پردازش است و فعلاً قابل حذف نیست.")
        try:
            output_dir = bulletin_run_directory(settings.bulletin_output_dir, run_id)
            files_removed = (
                sum(1 for item in output_dir.rglob("*") if item.is_file())
                if output_dir.is_dir()
                else 0
            )
            output_directory_removed = remove_bulletin_run_directory(
                settings.bulletin_output_dir, run_id
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"حذف فایل‌های خروجی ناموفق بود: {exc}") from exc
        if not await db.delete_bulletin_run(run_id):
            raise HTTPException(404, "اجرای خبرنامه پیدا نشد.")
        await audit(
            request,
            actor,
            "bulletin_run_deleted",
            object_type="bulletin_run",
            object_id=str(run_id),
            details={
                "output_directory": str(output_dir),
                "files_removed": files_removed,
                "output_directory_removed": output_directory_removed,
            },
        )
        return {
            "ok": True,
            "run_id": run_id,
            "files_removed": files_removed,
            "output_directory_removed": output_directory_removed,
        }

    @router.get("/admin/api/bulletins/{run_id}")
    async def bulletin_detail(run_id: int, _: str = Depends(admin_identity)) -> dict[str, Any]:
        item = await db.get_bulletin_run(run_id)
        if not item:
            raise HTTPException(404, "اجرای بولتن پیدا نشد.")
        return item

    @router.get("/admin/api/bulletins/{run_id}/logs")
    async def bulletin_logs(run_id: int, _: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        if not await db.get_bulletin_run(run_id):
            raise HTTPException(404, "اجرای بولتن پیدا نشد.")
        return await db.list_bulletin_run_logs(run_id)

    @router.get("/admin/api/bulletins/{run_id}/errors")
    async def bulletin_errors(run_id: int, _: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        if not await db.get_bulletin_run(run_id):
            raise HTTPException(404, "اجرای بولتن پیدا نشد.")
        return await db.list_processing_errors(run_id)

    @router.get("/admin/download/bulletins/{run_id}/logs.json")
    async def bulletin_logs_download(run_id: int, _: str = Depends(admin_identity)) -> JSONResponse:
        run = await db.get_bulletin_run(run_id)
        if not run:
            raise HTTPException(404, "اجرای بولتن پیدا نشد.")
        payload = {
            "run": run,
            "logs": await db.list_bulletin_run_logs(run_id, limit=5000),
            "errors": await db.list_processing_errors(run_id, limit=2000),
        }
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="bulletin_run_{run_id}_logs.json"'},
        )

    @router.get("/admin/download/bulletins/{run_id}.txt")
    async def bulletin_text(run_id: int, _: str = Depends(admin_identity)) -> PlainTextResponse:
        item = await db.get_bulletin_run(run_id)
        if not item:
            raise HTTPException(404)
        return PlainTextResponse(
            str(item.get("output_text") or item.get("error_text") or ""),
            headers={"Content-Disposition": f'attachment; filename="bulletin_{run_id}.txt"'},
        )

    @router.get("/admin/download/bulletins/{run_id}.html")
    async def bulletin_html(run_id: int, _: str = Depends(admin_identity)) -> HTMLResponse:
        item = await db.get_bulletin_run(run_id)
        if not item:
            raise HTTPException(404)
        body = item.get("output_html") or f"<pre>{item.get('output_text') or item.get('error_text') or ''}</pre>"
        return HTMLResponse(str(body))

    @router.get("/admin/download/bulletins/{run_id}.json")
    async def bulletin_json(run_id: int, _: str = Depends(admin_identity)) -> JSONResponse:
        item = await db.get_bulletin_run(run_id)
        if not item:
            raise HTTPException(404)
        return JSONResponse(item, headers={"Content-Disposition": f'attachment; filename="bulletin_{run_id}.json"'})

    @router.get("/admin/api/schedules")
    async def schedules(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_bulletin_schedules()

    @router.post("/admin/api/schedules")
    async def save_schedule(
        payload: ScheduleCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, int]:
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(payload.cron_expression, timezone=payload.timezone)
        except Exception as exc:
            raise HTTPException(422, f"عبارت زمان‌بندی نامعتبر است: {exc}") from exc
        schedule_id = await db.save_bulletin_schedule(
            name=payload.name,
            cron_expression=payload.cron_expression,
            timezone_name=payload.timezone,
            template_id=payload.template_id,
            filters=payload.filters,
            created_by=actor,
            schedule_id=payload.id,
        )
        await scheduler.reload()
        await audit(request, actor, "schedule_saved", object_type="bulletin_schedule", object_id=str(schedule_id), details=payload.model_dump())
        return {"id": schedule_id}

    @router.patch("/admin/api/schedules/{schedule_id}/enabled")
    async def enable_schedule(
        schedule_id: int,
        payload: ScheduleEnable,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, bool]:
        await db.set_bulletin_schedule_enabled(schedule_id, payload.enabled)
        await scheduler.reload()
        await audit(request, actor, "schedule_enabled_changed", object_type="bulletin_schedule", object_id=str(schedule_id), details=payload.model_dump())
        return {"ok": True}

    @router.get("/admin/api/system")
    async def system(_: str = Depends(admin_identity)) -> dict[str, Any]:
        target_id = await get_target_chat_id()
        ai_runtime = bulletins.ai_status()
        configured_models = (
            "GPT-5.6", "gpt-5.6-sol", "gpt-5.6-luna", "deepseek-v4-pro",
            settings.ai_model, settings.editorial_ai_model,
        )
        return {
            "database": await db.health_summary(),
            "stats": await db.dashboard_stats(),
            "events": await db.list_system_events(100),
            "ai_usage": await db.ai_usage_summary(configured_models),
            "version": current_version(),
            "update": last_update_status(),
            "configuration": {
                "bot_mode": settings.bot_mode,
                "input_mode": settings.input_mode,
                "miniapp_base_url": settings.miniapp_base_url,
                "miniapp_ready": bool(settings.miniapp_base_url),
                "target_chat_id": target_id,
                "ai_provider": settings.ai_provider,
                "ai_model": settings.ai_model,
                "ai_ready": bool(settings.ai_api_keys and settings.ai_model and settings.ai_provider != "disabled"),
                "ai_key_count": len(settings.ai_api_keys),
                "ai_max_concurrency": settings.ai_max_concurrency,
                "ai_concurrency_per_key": settings.ai_concurrency_per_key,
                "ai_profiles": ai_runtime.get("profiles", {}),
                "scheduler_enabled": settings.scheduler_enabled,
                "editorial_automation": await editorial_automation.status(),
                "crawler": {
                    "enabled": settings.crawler_enabled,
                    "channels_file": str(settings.crawler_channels_path),
                    "firefox_profile_configured": bool(settings.crawler_firefox_profile),
                    "destination_configured": bool(settings.crawler_destination_chat_id),
                    "repeat_seconds": settings.crawler_repeat_seconds,
                    "pid_recorded": (
                        WEB_ROOT.parent / "run" / "crawler.pid"
                    ).is_file(),
                },
            },
        }

    @router.get("/admin/api/system/gapgpt-status")
    async def gapgpt_status(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return await fetch_gapgpt_status()

    def read_crawler_channels() -> list[str]:
        path = settings.crawler_channels_path
        if not path.is_file():
            return []
        result: list[str] = []
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            value = raw.split(",", 1)[0].strip().lstrip("@")
            if value and value not in result:
                result.append(value)
        return result

    def crawler_pid_path() -> Path:
        return WEB_ROOT.parent / "run" / "crawler.pid"

    def crawler_process_id() -> int | None:
        path = crawler_pid_path()
        try:
            value = int(path.read_text(encoding="ascii").strip())
            return value if value > 0 else None
        except (OSError, ValueError):
            return None

    def crawler_is_running(pid: int | None = None) -> bool:
        candidate = pid if pid is not None else crawler_process_id()
        if not candidate:
            return False
        try:
            os.kill(candidate, 0)
        except OSError:
            return False
        return True

    async def crawler_recovery_status_payload() -> dict[str, Any]:
        run = await db.latest_crawler_recovery_run()
        return {
            "run": run,
            "process_running": bool(
                bot_queue_recovery_task and not bot_queue_recovery_task.done()
            ),
            "pid": None,
            "hours": int((run or {}).get("hours") or 48),
        }

    def crawler_status_payload(*, enabled: bool) -> dict[str, Any]:
        pid = crawler_process_id()
        return {
            "enabled": enabled,
            "configured_enabled": settings.crawler_enabled,
            "channels": read_crawler_channels(),
            "channels_file": str(settings.crawler_channels_path),
            "firefox_profile_configured": bool(settings.crawler_firefox_profile),
            "destination_configured": bool(settings.crawler_destination_chat_id),
            "repeat_seconds": settings.crawler_repeat_seconds,
            "pid_recorded": crawler_pid_path().is_file(),
            "process_running": crawler_is_running(pid),
            "pid": pid if crawler_is_running(pid) else None,
        }

    async def current_crawler_enabled() -> bool:
        saved_enabled = await db.get_setting("crawler_runtime_enabled")
        return (
            saved_enabled.strip().lower() in {"1", "true", "yes", "on"}
            if saved_enabled is not None
            else settings.crawler_enabled
        )

    @router.get("/admin/api/crawler")
    async def crawler_status(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return crawler_status_payload(enabled=await current_crawler_enabled())

    @router.patch("/admin/api/crawler/enabled")
    async def save_crawler_enabled(
        payload: CrawlerEnabledSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        await db.set_setting("crawler_runtime_enabled", "true" if payload.enabled else "false")
        await db.add_system_event(
            "crawler",
            "INFO",
            "crawler_runtime_setting_changed",
            "Crawler runtime enabled" if payload.enabled else "Crawler runtime paused",
            {"enabled": payload.enabled, "actor": actor},
        )
        await audit(
            request,
            actor,
            "crawler_runtime_enabled_changed",
            object_type="crawler",
            details={"enabled": payload.enabled},
        )
        return {"ok": True, **crawler_status_payload(enabled=payload.enabled)}

    @router.post("/admin/api/crawler/start")
    async def start_crawler(
        request: Request, actor: str = Depends(admin_identity)
    ) -> dict[str, Any]:
        """Launch the existing Selenium worker without a shell command.

        The worker itself reads the stored runtime switch before every cycle;
        starting it here therefore survives page reloads and avoids a second
        process when it is already running.
        """
        script_path = (WEB_ROOT.parent / "crawler" / "bale_crawler_api_sender.py").resolve()
        if not script_path.is_file():
            raise HTTPException(503, "فایل اجرایی کرولر پیدا نشد.")
        old_pid = crawler_process_id()
        if crawler_is_running(old_pid):
            await db.set_setting("crawler_runtime_enabled", "true")
            return {"ok": True, "started": False, "message": "کرولر از قبل در حال اجراست.", **crawler_status_payload(enabled=True)}
        pid_path = crawler_pid_path()
        if pid_path.is_file():
            # Only the dedicated one-file PID record is cleared; no process
            # is terminated merely because a stale record exists.
            pid_path.unlink(missing_ok=True)
        logs = WEB_ROOT.parent / "data" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stdout_path = logs / "crawler.out.log"
        stderr_path = logs / "crawler.err.log"
        try:
            stdout_handle = stdout_path.open("ab")
            stderr_handle = stderr_path.open("ab")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=str(WEB_ROOT.parent),
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=creationflags,
            )
            # Popen duplicates its standard handles on Windows.  Closing the
            # parent's copies keeps the dashboard process from leaking a
            # descriptor every time the worker is started.
            stdout_handle.close()
            stderr_handle.close()
        except OSError as exc:
            raise HTTPException(503, f"شروع کرولر ممکن نشد: {exc}") from exc
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(process.pid), encoding="ascii")
        await db.set_setting("crawler_runtime_enabled", "true")
        await db.add_system_event(
            "crawler", "INFO", "crawler_started_from_dashboard",
            "Selenium crawler started from automation dashboard.",
            {"actor": actor, "pid": process.pid},
        )
        await audit(request, actor, "crawler_started", object_type="crawler", details={"pid": process.pid})
        return {"ok": True, "started": True, **crawler_status_payload(enabled=True)}

    @router.post("/admin/api/crawler/stop")
    async def stop_crawler(
        request: Request, actor: str = Depends(admin_identity)
    ) -> dict[str, Any]:
        # Do not force-kill Firefox while it is navigating a channel.  The
        # worker reads this switch and completes the current channel safely.
        await db.set_setting("crawler_runtime_enabled", "false")
        await db.add_system_event(
            "crawler", "INFO", "crawler_stop_requested_from_dashboard",
            "Crawler pause requested; current channel is allowed to finish safely.",
            {"actor": actor},
        )
        await audit(request, actor, "crawler_stop_requested", object_type="crawler")
        return {
            "ok": True,
            "message": "توقف امن ثبت شد؛ کرولر پس از کانال جاری وارد دور تازه نمی‌شود.",
            **crawler_status_payload(enabled=False),
        }

    @router.get("/admin/api/crawler/recovery")
    async def crawler_recovery_status(
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        return await crawler_recovery_status_payload()

    @router.post("/admin/api/crawler/recovery")
    async def start_crawler_recovery(
        payload: CrawlerRecoveryRequest,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        """Start a bounded replay of Bot API queue/archive evidence.

        This route intentionally does not launch Selenium or read
        ``crawler/channels.csv``.  The runner uses the same in-process lock as
        normal polling, so it can safely coexist with the normal receiver.
        """
        nonlocal bot_queue_recovery_task
        if bot_queue_recovery_runner is None:
            raise HTTPException(503, "بازیابی صف ربات در این نسخه پیکربندی نشده است.")
        if bot_queue_recovery_task and not bot_queue_recovery_task.done():
            raise HTTPException(409, "بازیابی صف ربات از قبل در حال اجراست.")
        run_id = await db.create_crawler_recovery_run(
            hours=payload.hours, requested_by=actor
        )
        bot_queue_recovery_task = asyncio.create_task(
            bot_queue_recovery_runner(run_id, payload.hours),
            name=f"bot-queue-recovery-{run_id}",
        )
        await db.add_system_event(
            "bot", "INFO", "bot_queue_recovery_started",
            "48-hour Bot API queue recovery started.",
            {"run_id": run_id, "hours": payload.hours, "actor": actor},
        )
        await audit(
            request,
            actor,
            "bot_queue_recovery_started",
            object_type="bot_queue_recovery",
            object_id=str(run_id),
            details={"hours": payload.hours, "mode": "bot_api_queue_and_local_update_archive"},
        )
        return {"ok": True, "run_id": run_id, **(await crawler_recovery_status_payload())}

    @router.put("/admin/api/crawler/channels")
    async def save_crawler_channels(
        payload: CrawlerChannelsSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in payload.channels:
            value = str(raw or "").strip().lstrip("@")
            if not value:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,128}", value):
                raise HTTPException(
                    422,
                    f"نام کانال «{value}» نامعتبر است؛ فقط نام کاربری بدون @ وارد کنید.",
                )
            normalized = value.lower()
            if normalized not in seen:
                cleaned.append(value)
                seen.add(normalized)
        if not cleaned:
            raise HTTPException(422, "فهرست کانال‌های کرولر نمی‌تواند خالی باشد.")
        path = settings.crawler_channels_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
        temporary.replace(path)
        await audit(
            request,
            actor,
            "crawler_channels_updated",
            object_type="crawler",
            details={"channel_count": len(cleaned)},
        )
        return {"ok": True, "channels": cleaned, "channel_count": len(cleaned)}

    @router.post("/admin/api/backup")
    async def backup_now(request: Request, actor: str = Depends(admin_identity)) -> dict[str, Any]:
        result = await create_backup(settings.database_path, settings.backup_dir)
        await db.add_system_event("backup", "INFO", "manual_backup_completed", "Manual backup created", result)
        await audit(request, actor, "manual_backup", object_type="database", details=result)
        return result

    @router.get("/admin/api/system/update")
    async def system_update_status(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return last_update_status()

    @router.post("/admin/api/system/update")
    async def apply_system_update(
        request: Request,
        file: UploadFile = File(...),
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        principal: AdminPrincipal = request.state.admin_principal
        if not principal.can("system.manage"):
            raise HTTPException(403, "فقط مدیر سامانه می‌تواند بسته به‌روزرسانی را اعمال کند.")
        filename = str(file.filename or "").lower()
        if not filename.endswith(".zip"):
            raise HTTPException(422, "بسته به‌روزرسانی باید یک فایل ZIP باشد.")
        content = await file.read()
        try:
            result = apply_update_zip(content, actor=actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await db.add_system_event(
            "app",
            "INFO",
            "live_update_applied",
            f"Live update applied by {actor}",
            {
                "version": result.get("version"),
                "file_count": result.get("file_count"),
                "sha256": result.get("sha256"),
            },
        )
        await audit(
            request,
            actor,
            "live_update_applied",
            object_type="system",
            details={
                "version": result.get("version"),
                "file_count": result.get("file_count"),
                "python_restart_requested": result.get("python_restart_requested"),
            },
        )
        if result.get("python_restart_requested"):
            request_reload()
            result["restarting"] = True
            result["message"] = (
                "فایل‌ها جایگزین شدند. اگر سامانه با start-background یا وظیفه زمان‌بندی‌شده اجرا شده باشد، "
                "ظرف چند ثانیه با نسخه جدید برمی‌گردد."
            )
        else:
            result["restarting"] = False
            result["message"] = "فایل‌های رابط کاربری جایگزین شدند؛ با یک تازه‌سازی صفحه نسخه جدید دیده می‌شود."
        return result

    @router.get("/admin/api/audit")
    async def audit_log(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_dashboard_audit()

    @router.get("/admin/api/ai/status")
    async def ai_status(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return bulletins.ai_status()

    @router.get("/admin/api/people")
    async def people(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_people()

    @router.get("/admin/api/people/categories")
    async def people_categories(_: str = Depends(admin_identity)) -> dict[str, Any]:
        from .bulletin_models import REGISTRY_CATEGORY_OPTIONS

        stored = await db.list_person_categories()
        options: list[str] = []
        for title in list(REGISTRY_CATEGORY_OPTIONS) + stored:
            if title and title not in options:
                options.append(title)
        return {"options": options, "stored": stored}

    @router.post("/admin/api/people/categories/rename")
    async def rename_people_category(
        payload: PersonCategoryRename,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.rename_person_category(
                old_category=payload.old_category,
                new_category=payload.new_category,
                actor=actor,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "person_category_renamed",
            object_type="people",
            details=result,
        )
        return result

    @router.post("/admin/api/people/categories")
    async def create_people_category(
        payload: PersonCategoryCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.create_person_category(title=payload.title, actor=actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "person_category_created",
            object_type="people",
            details=result,
        )
        return result

    @router.post("/admin/api/people")
    async def save_person(
        payload: PersonCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, int]:
        person_id = await db.save_person(
            person_id=payload.person_id,
            full_name=payload.full_name,
            category=payload.category,
            position=payload.position,
            registry_status=payload.registry_status,
            active=payload.active,
            aliases=payload.aliases,
            priority=payload.priority,
            replace_aliases=payload.replace_aliases,
        )
        if payload.chat_id is not None or payload.username:
            await db.add_person_channel(person_id, chat_id=payload.chat_id, username=payload.username)
        await audit(request, actor, "person_saved", object_type="person", object_id=str(person_id), details=payload.model_dump())
        await db.save_person_change(person_id, action="edit" if payload.person_id else "create", old={}, new=payload.model_dump(), actor=actor)
        return {"person_id": person_id}

    @router.delete("/admin/api/people/{person_id}")
    async def delete_person(
        person_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, bool]:
        try:
            await db.delete_person(person_id, actor=actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "person_deleted",
            object_type="person",
            object_id=str(person_id),
        )
        return {"ok": True}

    @router.get("/admin/api/people/{person_id}")
    async def person_detail(person_id: int, _: str = Depends(admin_identity)) -> dict[str, Any]:
        row = await db.get_person(person_id)
        if not row:
            raise HTTPException(404, "شخص پیدا نشد.")
        return row

    @router.post("/admin/api/people/{person_id}/portrait")
    async def upload_person_portrait(
        person_id: int,
        request: Request,
        file: UploadFile = File(...),
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        person = await db.get_person(person_id)
        if not person:
            raise HTTPException(404, "شخص پیدا نشد.")
        content = await file.read()
        if not content or len(content) > 5 * 1024 * 1024:
            raise HTTPException(422, "حجم تصویر باید کمتر از پنج مگابایت باشد.")
        try:
            image = Image.open(io.BytesIO(content))
            image.verify()
            image = Image.open(io.BytesIO(content)).convert("RGB")
            image.thumbnail((1000, 1200))
        except Exception as exc:
            raise HTTPException(422, f"فایل تصویر معتبر نیست: {exc}") from exc
        portrait_dir = settings.bulletin_output_dir.parent / "portraits"
        portrait_dir.mkdir(parents=True, exist_ok=True)
        path = portrait_dir / f"person_{person_id}.jpg"
        image.save(path, format="JPEG", quality=90, optimize=True)
        await audit(request, actor, "person_portrait_saved", object_type="person", object_id=str(person_id), details={"path": str(path)})
        return {"ok": True, "person_id": person_id, "url": f"/admin/portraits/{person_id}"}

    @router.get("/admin/portraits/{person_id}")
    async def person_portrait(person_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        portrait_dir = settings.bulletin_output_dir.parent / "portraits"
        candidates = [portrait_dir / f"person_{person_id}.{ext}" for ext in ("jpg", "jpeg", "png", "webp")]
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise HTTPException(404, "تصویر شخص ثبت نشده است.")
        return FileResponse(path)

    @router.post("/admin/api/people/merge")
    async def merge_people(payload: PersonMerge, request: Request, actor: str = Depends(admin_identity)) -> dict[str, bool]:
        try:
            await db.merge_people(payload.source_person_id, payload.target_person_id, actor=actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(request, actor, "people_merged", object_type="person", object_id=str(payload.target_person_id), details=payload.model_dump())
        return {"ok": True}

    @router.post("/admin/api/people/import")
    async def import_people(
        request: Request,
        file: UploadFile = File(...),
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        content = await file.read()
        name = (file.filename or "").lower()
        rows: list[dict[str, Any]] = []
        if name.endswith(".xlsx") or name.endswith(".xlsm"):
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            sheet = workbook.active
            values = list(sheet.iter_rows(values_only=True))
            if values:
                headers = [str(x or "").strip() for x in values[0]]
                for values_row in values[1:]:
                    rows.append({headers[i]: values_row[i] for i in range(min(len(headers), len(values_row)))})
        elif name.endswith(".csv"):
            text = content.decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
        else:
            raise HTTPException(422, "فقط فایل XLSX یا CSV پذیرفته می‌شود.")

        def first(row: dict[str, Any], *keys: str) -> str:
            normalized = {str(k).strip().lower(): v for k, v in row.items()}
            for key in keys:
                value = normalized.get(key.lower())
                if value is not None and str(value).strip():
                    return str(value).strip()
            return ""

        imported = 0
        errors: list[str] = []
        for index, row in enumerate(rows, start=2):
            full_name = first(row, "نام", "نام کامل", "full_name", "name")
            if not full_name:
                continue
            alias_raw = first(row, "القاب", "نام‌های دیگر", "aliases", "alias")
            aliases = [x.strip() for x in alias_raw.replace("،", "|").replace(",", "|").split("|") if x.strip()]
            registry_raw = first(row, "وضعیت شناسنامه", "registry_status", "داخل شناسنامه")
            registry_status = "outside" if registry_raw.lower() in {"outside", "خارج", "خارج شناسنامه", "0", "false"} else "inside"
            try:
                await db.save_person(
                    full_name=full_name,
                    category=first(row, "دسته اصلی", "دسته", "category") or None,
                    position=first(row, "سمت", "position", "title") or None,
                    registry_status=registry_status,
                    aliases=aliases,
                )
                imported += 1
            except Exception as exc:
                errors.append(f"ردیف {index}: {exc}")
        await audit(request, actor, "people_imported", object_type="people", details={"filename": file.filename, "imported": imported, "errors": errors[:20]})
        return {"imported": imported, "errors": errors}

    @router.get("/admin/api/person-candidates")
    async def person_candidates(
        status_value: str | None = Query(None, alias="status"),
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        return await db.list_person_candidates(status_value)

    @router.patch("/admin/api/person-candidates/{candidate_id}")
    async def review_candidate(
        candidate_id: int,
        payload: CandidateReview,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.review_person_candidate(
                candidate_id,
                action=payload.action,
                actor=actor,
                merge_person_id=payload.merge_person_id,
                category=payload.category,
                full_name=payload.full_name,
                aliases=payload.aliases,
                position=payload.position,
                add_detected_alias=payload.add_detected_alias,
                add_position=payload.add_position,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not result.get("ok"):
            raise HTTPException(404, "نامزد شخص پیدا نشد.")
        await audit(request, actor, "person_candidate_reviewed", object_type="person_candidate", object_id=str(candidate_id), details=payload.model_dump())
        return result

    @router.get("/admin/api/topics")
    async def list_topics(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_topics_with_keywords()

    @router.post("/admin/api/topics")
    async def save_topic(
        payload: TopicCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, int]:
        topic_id = await db.save_topic(payload.name, payload.keywords, topic_id=payload.topic_id)
        await audit(request, actor, "topic_saved", object_type="topic", object_id=str(topic_id), details=payload.model_dump())
        return {"topic_id": topic_id}

    @router.get("/admin/api/editorial-drafts")
    async def editorial_drafts(
        status_value: str | None = Query(None, alias="status"),
        date_from: str | None = None,
        date_to: str | None = None,
        date_from_jalali: str | None = None,
        date_to_jalali: str | None = None,
        timezone_name: str = Query("Asia/Tehran", alias="timezone"),
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        if status_value and status_value not in {"draft", "finalized"}:
            raise HTTPException(422, "وضعیت پیش‌نویس نامعتبر است.")
        try:
            effective_from = (
                _jalali_local_to_utc_iso(date_from_jalali, timezone_name, end_of_day=False)
                if date_from_jalali
                else date_from
            )
            effective_to = (
                _jalali_local_to_utc_iso(date_to_jalali, timezone_name, end_of_day=True)
                if date_to_jalali
                else date_to
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await db.list_editorial_drafts(
            status_value=status_value,
            date_from=effective_from,
            date_to=effective_to,
        )

    @router.post("/admin/api/editorial-drafts")
    async def create_editorial_draft(
        payload: EditorialDraftCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        if not payload.message_inputs and not payload.manual:
            raise HTTPException(422, "حداقل یک پیام مبنا لازم است.")
        rows: list[dict[str, Any]] = []
        for selected in payload.message_inputs:
            message = await db.message_detail(selected.message_id)
            if not message:
                raise HTTPException(404, f"پیام {selected.message_id} پیدا نشد.")
            rows.append(message)
        is_event = str(payload.content_type or "").strip().casefold() in {
            "event",
            "رویداد",
            "رویداد مهم ایران و جهان",
        }
        first_row = rows[0] if rows else {}
        main_subject = payload.main_subject or first_row.get("analysis_main_subject")
        if is_event and rows and len(rows) != 1:
            raise HTTPException(422, "هر رویداد باید به‌صورت مستقل و با یک پیام در میز تدوین باز شود.")
        if is_event:
            source = first_row
            person_id = None
            person_name = None
            position = None
            topic_id = payload.topic_id or source.get("detected_topic_id")
            topic_name = (
                payload.topic_name
                or source.get("analysis_general_topic")
                or source.get("detected_topic_name")
            )
            event_title = (
                payload.event_title
                or source.get("analysis_event_title")
                or source.get("analysis_main_subject")
                or "رویداد بدون عنوان"
            )
            raw_entities = source.get("analysis_event_entities_json")
            try:
                source_entities = json.loads(raw_entities) if raw_entities else []
            except (TypeError, ValueError, json.JSONDecodeError):
                source_entities = []
            event_entities = payload.event_entities or (
                source_entities if isinstance(source_entities, list) else []
            )
            event_location = payload.event_location or source.get("analysis_event_location")
            event_time = payload.event_time or source.get("analysis_event_time")
            oration_location = None
            title = payload.title or str(event_title)
            category_name = payload.category_name or "وقایع و رویدادهای مهم ایران و جهان"
        else:
            matched_person = (
                await db.find_person(payload.person_name)
                if payload.person_name and payload.person_id is None
                else None
            )
            person_id = (
                payload.person_id
                or (matched_person or {}).get("person_id")
                or (
                    first_row.get("detected_person_id")
                    if not payload.person_name
                    else None
                )
            )
            person_name = (
                (matched_person or {}).get("full_name")
                or payload.person_name
                or first_row.get("detected_person_name")
            )
            primary_tags = first_row.get("speaker_tags") or []
            matching_tag = next(
                (
                    tag
                    for tag in primary_tags
                    if person_name
                    and str(tag.get("speaker_name") or "").strip()
                    == str(person_name).strip()
                ),
                primary_tags[0] if primary_tags else {},
            )
            position = (
                payload.position
                or (matched_person or {}).get("position")
                or matching_tag.get("position")
            )
            topic_id = payload.topic_id or (
                first_row.get("detected_topic_id")
                if not payload.topic_name
                else None
            )
            topic_name = payload.topic_name or first_row.get("detected_topic_name")
            event_title = None
            event_entities = []
            event_location = None
            event_time = None
            title = payload.title
            category_name = payload.category_name
            oration_location = " · ".join(
                value
                for value in (
                    matching_tag.get("expression_method_type"),
                    matching_tag.get("expression_method_context"),
                )
                if value and value != "نامشخص"
            ) or None
        source_texts: list[str] = []
        for index, selected in enumerate(payload.message_inputs):
            row = rows[index]
            text = str(
                selected.selected_text
                or row.get("text")
                or row.get("caption")
                or ""
            ).strip()
            if text and text not in source_texts:
                source_texts.append(text)
        base_text = str(payload.base_text or "\n\n".join(source_texts)).strip()
        draft_id = await db.create_editorial_draft(
            title=title,
            person_id=int(person_id) if person_id is not None else None,
            person_name=str(person_name) if person_name else None,
            position=str(position) if position else None,
            topic_id=int(topic_id) if topic_id is not None else None,
            topic_name=str(topic_name) if topic_name else None,
            main_subject=str(main_subject) if main_subject else None,
            message_inputs=[item.model_dump() for item in payload.message_inputs],
            base_text=base_text,
            created_by=actor,
            category_name=category_name,
            content_type="event" if is_event else "person_statement",
            event_title=str(event_title) if event_title else None,
            event_entities=[str(value) for value in event_entities if str(value).strip()],
            event_location=str(event_location) if event_location else None,
            event_time=str(event_time) if event_time else None,
            oration_location=oration_location,
        )
        await audit(
            request,
            actor,
            "editorial_draft_created",
            object_type="editorial_draft",
            object_id=str(draft_id),
            details={
                "manual": payload.manual,
                "message_ids": [item.message_id for item in payload.message_inputs],
            },
        )
        return {"draft_id": draft_id}

    @router.get("/admin/api/editorial-drafts/{draft_id}")
    async def editorial_draft_detail(
        draft_id: int,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        item = await db.get_editorial_draft(draft_id)
        if not item:
            raise HTTPException(404, "پیش‌نویس پیدا نشد.")
        return item

    @router.put("/admin/api/editorial-drafts/{draft_id}")
    async def save_editorial_draft(
        draft_id: int,
        payload: EditorialDraftSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        if not payload.base_text.strip():
            raise HTTPException(422, "متن پایه نمی‌تواند خالی باشد.")
        try:
            version_no = await db.save_editorial_draft_version(
                draft_id,
                title=payload.title,
                base_text=payload.base_text.strip(),
                summary_paragraph=payload.summary_paragraph,
                summary_sentence=payload.summary_sentence,
                summary_title=payload.summary_title,
                detail=payload.detail or payload.summary_title,
                category_name=payload.category_name,
                person_id=payload.person_id,
                person_name=payload.person_name,
                topic_id=payload.topic_id,
                topic_name=payload.topic_name,
                main_subject=payload.main_subject,
                oration_location=payload.oration_location,
                source_url=payload.source_url,
                footnote=payload.footnote,
                expected_version=payload.expected_version,
                change_reason=payload.change_reason,
                actor=actor,
            )
        except EditorialDraftConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "current_version": exc.current_version,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        await audit(
            request,
            actor,
            "editorial_draft_saved",
            object_type="editorial_draft",
            object_id=str(draft_id),
            details={"version_no": version_no},
        )
        return {"ok": True, "version_no": version_no}

    @router.post("/admin/api/editorial-drafts/{draft_id}/source-link")
    async def create_editorial_source_link(
        draft_id: int,
        payload: EditorialSourceLink,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, str]:
        draft = await db.get_editorial_draft(draft_id)
        if not draft:
            raise HTTPException(404, "پیش‌نویس پیدا نشد.")
        source_url = payload.source_url.strip()
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(422, "لینک منبع باید یک نشانی معتبر http یا https باشد.")
        await db.set_editorial_source_url(draft_id, source_url)
        existing = await db.short_link_for_draft(draft_id)
        code = str(existing.get("code")) if existing else secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")
        await db.upsert_short_link(draft_id, code, source_url)
        short_url = f"{settings.public_base_url}/s/{code}"
        qr_dir = settings.database_path.parent / "qrcodes"
        qr_dir.mkdir(parents=True, exist_ok=True)
        qr_path = qr_dir / f"editorial-draft-{draft_id}.png"
        qrcode.make(short_url).save(qr_path)
        await db.set_editorial_short_link(draft_id, short_url, str(qr_path))
        await audit(request, actor, "editorial_source_link_created", object_type="editorial_draft", object_id=str(draft_id), details={"host": parsed.netloc})
        return {"short_url": short_url, "qr_code_url": f"/admin/api/editorial-drafts/{draft_id}/qr"}

    @router.get("/admin/api/editorial-drafts/{draft_id}/qr")
    async def editorial_draft_qr(draft_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        draft = await db.get_editorial_draft(draft_id)
        path = Path(str((draft or {}).get("qr_code_path") or ""))
        if not draft or not path.is_file():
            raise HTTPException(404, "کد QR برای این پیش‌نویس ساخته نشده است.")
        return FileResponse(path, media_type="image/png")

    @router.delete("/admin/api/editorial-drafts/{draft_id}")
    async def delete_editorial_draft(
        draft_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, bool]:
        try:
            deleted = await db.delete_editorial_draft(draft_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "editorial_draft_deleted",
            object_type="editorial_draft",
            object_id=str(draft_id),
            details={
                "title": deleted.get("title"),
                "person_name": deleted.get("person_name"),
                "current_version": deleted.get("current_version"),
            },
        )
        return {"ok": True}

    @router.post("/admin/api/editorial-drafts/{draft_id}/generate")
    async def generate_editorial_draft(
        draft_id: int,
        payload: EditorialGenerate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            existing_draft = await db.get_editorial_draft(draft_id)
            if not existing_draft:
                raise RuntimeError("پیش‌نویس پیدا نشد.")
            result = await bulletins.generate_editorial_content(
                draft_id,
                kind=payload.kind,
                base_text=payload.base_text,
            )
            if payload.persist:
                if payload.expected_version is None:
                    raise RuntimeError("نسخهٔ بازشدهٔ پیش‌نویس برای ذخیره لازم است.")
                draft = await db.get_editorial_draft(draft_id)
                if not draft:
                    raise RuntimeError("پیش‌نویس پیدا نشد.")
                version_no = await db.save_editorial_draft_version(
                    draft_id,
                    title=draft.get("title"),
                    base_text=str(
                        result.get("base_text")
                        or payload.base_text
                        or draft.get("base_text")
                        or ""
                    ).strip(),
                    summary_paragraph=result.get("summary_paragraph")
                    or draft.get("summary_paragraph"),
                    summary_sentence=result.get("summary_sentence")
                    or draft.get("summary_sentence"),
                    summary_title=result.get("summary_title")
                    or draft.get("summary_title"),
                    detail=result.get("detail") or result.get("summary_title") or draft.get("detail") or draft.get("summary_title"),
                    category_name=draft.get("category_name"),
                    person_id=draft.get("person_id"),
                    person_name=draft.get("person_name"),
                    topic_id=draft.get("topic_id"),
                    topic_name=draft.get("topic_name"),
                    main_subject=draft.get("main_subject"),
                    oration_location=draft.get("oration_location"),
                    source_url=draft.get("source_url"),
                    footnote=draft.get("footnote"),
                    expected_version=payload.expected_version,
                    change_reason=(
                        "تولید و ذخیره خودکار محتوا با مدل دوم"
                        if result.get("ai_used")
                        else "تولید و ذخیره خودکار نسخه پشتیبان آفلاین"
                    ),
                    actor=actor,
                )
                result["persisted"] = True
                result["version_no"] = version_no
        except EditorialDraftConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "current_version": exc.current_version,
                },
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "editorial_content_generated",
            object_type="editorial_draft",
            object_id=str(draft_id),
            details={
                "kind": payload.kind,
                "ai_used": bool(result.get("ai_used")),
                "persisted": bool(result.get("persisted")),
                "fallback_reason": result.get("fallback_reason"),
            },
        )
        return result

    @router.post("/admin/api/editorial-drafts/{draft_id}/restore/{version_id}")
    async def restore_editorial_draft(
        draft_id: int,
        version_id: int,
        payload: EditorialDraftRestore,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            version_no = await db.restore_editorial_draft_version(
                draft_id,
                version_id,
                expected_version=payload.expected_version,
                actor=actor,
            )
        except EditorialDraftConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "current_version": exc.current_version,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        await audit(
            request,
            actor,
            "editorial_draft_restored",
            object_type="editorial_draft",
            object_id=str(draft_id),
            details={"source_version_id": version_id, "new_version_no": version_no},
        )
        return {"ok": True, "version_no": version_no}

    @router.post("/admin/api/editorial-drafts/{draft_id}/finalize")
    async def finalize_editorial_draft(
        draft_id: int,
        request: Request,
        payload: EditorialFinalize | None = None,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            finalized_at = _finalization_time_to_utc_iso(payload) if payload else None
            result = await db.finalize_editorial_draft(
                draft_id,
                actor=actor,
                finalized_at=finalized_at,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "editorial_draft_finalized",
            object_type="editorial_draft",
            object_id=str(draft_id),
            details={"finalized_at": result["finalized_at"]},
        )
        return result

    @router.get("/admin/api/high-attention/days")
    async def high_attention_days(_: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_finalized_editorial_days()

    @router.get("/admin/api/high-attention/drafts")
    async def high_attention_drafts(
        source_day: str,
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        try:
            return await db.list_finalized_editorial_drafts_for_day(source_day)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/admin/api/high-attention/finalized-items")
    async def finalized_high_attention_items(
        source_day: str,
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        return await db.list_finalized_high_attention_items(source_day)

    @router.get("/admin/api/high-attention")
    async def high_attention_runs(
        source_day: str | None = None,
        status_value: str | None = Query(None, alias="status"),
        _: str = Depends(admin_identity),
    ) -> list[dict[str, Any]]:
        if status_value and status_value not in {"draft", "finalized"}:
            raise HTTPException(422, "وضعیت پربازتاب نامعتبر است.")
        return await db.list_high_attention_runs(
            source_day=source_day,
            status_value=status_value,
        )

    @router.post("/admin/api/high-attention/generate")
    async def generate_high_attention(
        payload: HighAttentionGenerate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            available = await db.list_finalized_editorial_drafts_for_day(payload.source_day)
            available_ids = {int(item["draft_id"]) for item in available}
            draft_ids = list(dict.fromkeys(int(value) for value in payload.draft_ids))
            if not draft_ids:
                raise ValueError("حداقل یک خبر نهایی را برای پربازتاب انتخاب کنید.")
            if any(draft_id not in available_ids for draft_id in draft_ids):
                raise ValueError("فقط خبرهای نهایی‌شدهٔ همان روز قابل انتخاب‌اند.")
            drafts: list[dict[str, Any]] = []
            for draft_id in draft_ids:
                draft = await db.get_editorial_draft(draft_id)
                if not draft:
                    raise ValueError("یکی از خبرهای انتخاب‌شده پیدا نشد.")
                drafts.append(draft)
            result = await bulletins.generate_high_attention(drafts)
            high_attention_run_id = await db.create_high_attention_run(
                source_day=payload.source_day,
                draft_ids=draft_ids,
                items=result["items"],
                provider=result.get("provider"),
                model=result.get("model"),
                prompt_version=str(result.get("prompt_version") or ""),
                api_key_slot=result.get("api_key_slot"),
                created_by=actor,
            )
            created = await db.get_high_attention_run(high_attention_run_id)
            if not created:
                raise RuntimeError("ذخیره‌سازی تولید پربازتاب ناموفق بود.")
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "high_attention_generated",
            object_type="high_attention_run",
            object_id=str(high_attention_run_id),
            details={
                "source_day": payload.source_day,
                "draft_ids": payload.draft_ids,
                "item_count": len(created.get("items") or []),
                "api_key_slot": 8,
            },
        )
        return created

    @router.get("/admin/api/high-attention/{high_attention_run_id}")
    async def high_attention_detail(
        high_attention_run_id: int,
        _: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        item = await db.get_high_attention_run(high_attention_run_id)
        if not item:
            raise HTTPException(404, "تولید پربازتاب پیدا نشد.")
        return item

    @router.put("/admin/api/high-attention/{high_attention_run_id}/items")
    async def save_high_attention(
        high_attention_run_id: int,
        payload: HighAttentionItemsSave,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            await db.save_high_attention_items(
                high_attention_run_id,
                [item.model_dump() for item in payload.items],
            )
            saved = await db.get_high_attention_run(high_attention_run_id)
            if not saved:
                raise ValueError("تولید پربازتاب پیدا نشد.")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "high_attention_saved",
            object_type="high_attention_run",
            object_id=str(high_attention_run_id),
            details={"item_count": len(payload.items)},
        )
        return saved

    @router.post("/admin/api/high-attention/{high_attention_run_id}/finalize")
    async def finalize_high_attention(
        high_attention_run_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.finalize_high_attention_run(
                high_attention_run_id,
                actor=actor,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "high_attention_finalized",
            object_type="high_attention_run",
            object_id=str(high_attention_run_id),
            details={},
        )
        return result

    @router.post("/admin/api/editorial-bulletins")
    async def create_editorial_bulletin(
        payload: EditorialBulletinCreate,
        background_tasks: BackgroundTasks,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        if payload.report_mode not in {"concise", "full"}:
            raise HTTPException(422, "نوع گزارش باید concise یا full باشد.")
        try:
            run_id = await db.create_manual_run_from_drafts(
                draft_ids=payload.draft_ids,
                requested_by=actor,
                issue_number=payload.issue_number,
                report_mode=payload.report_mode,
                title=payload.title,
                introduction=payload.introduction,
                high_attention_item_ids=payload.high_attention_item_ids,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await db.mark_bulletin_export_state(run_id, stage="exports_queued")
        background_tasks.add_task(export_manual_run, run_id, actor)
        await audit(
            request,
            actor,
            "editorial_bulletin_created",
            object_type="bulletin_run",
            object_id=str(run_id),
            details=payload.model_dump(),
        )
        return {"ok": True, "run_id": run_id, "status": "exports_queued"}

    @router.get("/admin/api/bulletins/{run_id}/items")
    async def bulletin_items(run_id: int, _: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_bulletin_items(run_id)

    @router.get("/admin/api/bulletin-items/{item_id}")
    async def bulletin_item_detail(item_id: int, _: str = Depends(admin_identity)) -> dict[str, Any]:
        item = await db.bulletin_item_detail(item_id)
        if not item:
            raise HTTPException(404, "آیتم بولتن پیدا نشد.")
        return item

    @router.patch("/admin/api/bulletin-items/{item_id}")
    async def moderate_bulletin_item(
        item_id: int,
        payload: BulletinItemModerate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.moderate_bulletin_item(
                item_id,
                new_status=payload.status,
                actor=actor,
                edited_summary=payload.edited_summary,
                reason=payload.reason,
                corrected_person_name=payload.person_name,
                corrected_person_id=payload.person_id,
                issue_tags=payload.issue_tags,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not result.get("ok"):
            raise HTTPException(404, "آیتم پیدا نشد.")
        await audit(request, actor, "bulletin_item_moderated", object_type="bulletin_item", object_id=str(item_id), details=payload.model_dump())
        return result

    @router.get("/admin/api/bulletins/{run_id}/pending-ai")
    async def bulletin_pending_ai(run_id: int, _: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_pending_ai_messages(run_id)

    @router.post("/admin/api/bulletins/{run_id}/reconcile-editorial-ai")
    async def reconcile_editorial_ai(
        run_id: int,
        payload: EditorialAIReconcile,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.reconcile_editorial_ai_state(
                run_id,
                actor=actor,
                exclude_unlinked=payload.exclude_unlinked,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            request,
            actor,
            "editorial_ai_reconciled",
            object_type="bulletin_run",
            object_id=str(run_id),
            details=result,
        )
        return result

    @router.patch("/admin/api/bulletins/{run_id}/pending-ai/{message_id}")
    async def resolve_pending_ai(
        run_id: int,
        message_id: int,
        payload: PendingAIResolve,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await db.resolve_pending_ai_message(
                run_id,
                message_id,
                decision=payload.decision,
                actor=actor,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not result.get("ok"):
            raise HTTPException(404, "پیام در این اجرا پیدا نشد.")
        await audit(
            request,
            actor,
            "pending_ai_message_resolved",
            object_type="message",
            object_id=str(message_id),
            details=result,
        )
        return result

    @router.get("/admin/api/bulletins/{run_id}/quality")
    async def bulletin_quality(run_id: int, _: str = Depends(admin_identity)) -> dict[str, Any]:
        return await db.bulletin_quality_report(run_id)

    @router.get("/admin/api/quality")
    async def overall_quality(_: str = Depends(admin_identity)) -> dict[str, Any]:
        return await db.bulletin_quality_report(None)

    @router.post("/admin/api/bulletin-items/{item_id}/reprocess-summary")
    async def reprocess_item_summary(
        item_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await bulletins.rebuild_item_summary(item_id, actor=actor)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        await audit(request, actor, "bulletin_item_summary_reprocessed", object_type="bulletin_item", object_id=str(item_id), details=result)
        return result

    @router.post("/admin/api/bulletins/{run_id}/reprocess")
    async def reprocess_bulletin_run(
        run_id: int,
        payload: ReprocessRequest,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            new_run_id = await bulletins.reprocess_run(run_id, mode=payload.mode, actor=actor)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        result = {"ok": True, "source_run_id": run_id, "new_run_id": new_run_id, "mode": payload.mode}
        await audit(request, actor, "bulletin_run_reprocessed", object_type="bulletin_run", object_id=str(new_run_id), details=result)
        return result

    @router.post("/admin/api/bulletin-items/{item_id}/evaluation")
    async def save_evaluation_case(
        item_id: int,
        payload: EvaluationCaseCreate,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            case_id = await db.save_evaluation_case(item_id, actor=actor, notes=payload.notes)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        result = {"ok": True, "case_id": case_id}
        await audit(request, actor, "evaluation_case_saved", object_type="evaluation_case", object_id=str(case_id), details={"item_id": item_id})
        return result

    @router.get("/admin/api/evaluation-cases")
    async def evaluation_cases(run_id: int | None = None, _: str = Depends(admin_identity)) -> list[dict[str, Any]]:
        return await db.list_evaluation_cases(run_id)

    @router.get("/admin/download/evaluation-cases.csv")
    async def evaluation_cases_csv(run_id: int | None = None, _: str = Depends(admin_identity)) -> Response:
        rows = await db.list_evaluation_cases(run_id)
        output = io.StringIO()
        fields = [
            "case_id", "run_id", "item_id", "message_ids_json", "expected_person_name",
            "expected_topic_name", "expected_statement_type", "expected_summary", "expected_claims_json",
            "duplicate_label", "registry_bucket", "notes", "created_by", "created_at", "updated_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="garaye_evaluation_cases.csv"'},
        )

    @router.post("/admin/api/bulletins/{run_id}/editorial-rebuild")
    async def rebuild_editorial_output(
        run_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await bulletins.rebuild_editorial_output(run_id, actor=actor)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        await audit(request, actor, "bulletin_editorial_rebuilt", object_type="bulletin_run", object_id=str(run_id), details=result)
        return result

    @router.get("/admin/api/bulletins/{run_id}/editorial-status")
    async def editorial_status(run_id: int, _: str = Depends(admin_identity)) -> dict[str, Any]:
        return bulletins.editorial_status(run_id)

    @router.post("/admin/api/bulletins/{run_id}/export")
    async def export_bulletin(
        run_id: int,
        request: Request,
        actor: str = Depends(admin_identity),
    ) -> dict[str, Any]:
        try:
            result = await run_bulletin_export(run_id, actor)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"تولید خروجی ناموفق بود: {exc}") from exc
        await audit(request, actor, "bulletin_exported", object_type="bulletin_run", object_id=str(run_id), details=result)
        return result

    async def _export_file(run_id: int, bucket: str, format_name: str) -> Path:
        exports = await db.list_bulletin_exports(run_id)
        item = next((x for x in exports if x.get("registry_bucket") == bucket and x.get("format") == format_name), None)
        if not item:
            try:
                await run_bulletin_export(run_id, actor="download")
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(500, f"تولید خروجی ناموفق بود: {exc}") from exc
            exports = await db.list_bulletin_exports(run_id)
            item = next((x for x in exports if x.get("registry_bucket") == bucket and x.get("format") == format_name), None)
        path = Path(str(item.get("file_path") if item else ""))
        if not path.exists():
            raise HTTPException(404, "فایل خروجی پیدا نشد.")
        return path

    @router.get("/admin/download/bulletins/{run_id}/concise.docx")
    async def download_concise(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "concise", "docx")
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @router.get("/admin/download/bulletins/{run_id}/full.docx")
    async def download_full(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "full", "docx")
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @router.get("/admin/download/bulletins/{run_id}/data.json")
    async def download_data(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "data", "json")
        return FileResponse(path, filename=path.name, media_type="application/json")

    @router.get("/admin/download/bulletins/{run_id}/audit.xlsx")
    async def download_audit(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "audit", "xlsx")
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.get("/admin/download/bulletins/{run_id}/unregistered.xlsx")
    async def download_unregistered(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "unregistered", "xlsx")
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.get("/admin/download/bulletins/{run_id}/run.log")
    async def download_run_log(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "run", "log")
        return FileResponse(path, filename=path.name, media_type="text/plain")

    @router.get("/admin/download/bulletins/{run_id}/errors.log")
    async def download_errors_log(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "errors", "log")
        return FileResponse(path, filename=path.name, media_type="text/plain")

    @router.get("/admin/download/bulletins/{run_id}/classic.pdf")
    async def download_classic_pdf(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "classic", "pdf")
        return FileResponse(path, filename=path.name, media_type="application/pdf")

    @router.get("/admin/download/bulletins/{run_id}/layout.html")
    async def download_layout_html(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "layout", "html")
        return FileResponse(
            path,
            filename=path.name,
            media_type="text/html; charset=utf-8",
        )

    @router.get("/admin/preview/bulletins/{run_id}/layout")
    async def preview_layout_html(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "layout", "html")
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": f'inline; filename="{path.name}"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/admin/download/bulletins/{run_id}/layout-report.json")
    async def download_layout_report(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "layout_report", "json")
        return FileResponse(path, filename=path.name, media_type="application/json")

    @router.get("/admin/download/bulletins/{run_id}/magazine.pdf")
    async def download_magazine_pdf(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "magazine", "pdf")
        return FileResponse(path, filename=path.name, media_type="application/pdf")

    @router.get("/admin/download/bulletins/{run_id}/magazine.html")
    async def download_magazine_html(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "magazine", "html")
        return FileResponse(path, filename=path.name, media_type="text/html; charset=utf-8")

    @router.get("/admin/download/bulletins/{run_id}/all.zip")
    async def download_all(run_id: int, _: str = Depends(admin_identity)) -> FileResponse:
        path = await _export_file(run_id, "all", "zip")
        return FileResponse(path, filename=path.name, media_type="application/zip")


    return router


DASHBOARD_HTML = r'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>داشبورد PRASAD</title>
<style>
@font-face{font-family:IRZarWeb;src:url("/admin/assets/fonts/IRZar.ttf") format("truetype");font-display:swap}
:root{--bg:#fff;--card:#fff;--line:rgba(0,78,79,.18);--text:#002223;--muted:#004e4f;--accent:#008080;--danger:#00010d;--warn:#006767}
*{box-sizing:border-box}body{margin:0;font-family:IRZarWeb,IRZar,"B Zar",Tahoma,Arial,sans-serif;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:14px 22px;z-index:5;display:flex;justify-content:space-between;align-items:center}
nav button{background:transparent;color:var(--muted);border:0;padding:10px 12px;cursor:pointer;font-size:14px}nav button.active{color:var(--accent);border-bottom:2px solid var(--accent)}
main{max-width:1450px;margin:auto;padding:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}.metric{font-size:30px;font-weight:bold;margin-top:8px}.muted{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}th{color:var(--muted)}
input,select,textarea{background:#fff;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:9px;width:100%}textarea{min-height:110px}.row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}.row.two{grid-template-columns:1fr 1fr}
button.primary,button.secondary,button.danger{border:0;border-radius:7px;padding:9px 14px;cursor:pointer}.primary{background:var(--accent);color:#fff}.secondary{background:rgba(0,78,79,.10);color:#003738}.danger{background:var(--danger);color:#fff}.badge{display:inline-block;border-radius:999px;padding:3px 8px;font-size:11px;background:#004e4f;color:#fff}.approved{background:#008080}.rejected{background:#00010d}.pending{background:#006767}.actions{display:flex;gap:6px;flex-wrap:wrap}.actions button{white-space:nowrap}.time-cell{min-width:145px;line-height:1.8}.hidden{display:none}.scroll{overflow:auto;max-height:68vh}.text{max-width:520px;white-space:pre-wrap}.toast{position:fixed;bottom:20px;left:20px;background:#003738;color:#fff;border:1px solid var(--line);padding:12px 18px;border-radius:8px;display:none;z-index:9}@media(max-width:800px){.row,.row.two{grid-template-columns:1fr}header{display:block}nav{overflow:auto;white-space:nowrap}}
</style></head><body>
<header><div><b>مرکز کنترل PRASAD</b><div class="muted">پایش بله، دیتابیس و تولید بولتن</div></div><nav id="nav"><button data-page="overview" class="active">نمای کلی</button><button data-page="messages">پیام‌ها</button><button data-page="sources">منابع</button><button data-page="bulletins">بولتن</button><button data-page="schedules">زمان‌بندی</button><button data-page="system">سامانه</button></nav></header>
<main>
<section id="overview"><div id="metrics" class="grid"></div><div class="card"><h3>آمار منابع</h3><div class="scroll"><table><thead><tr><th>منبع</th><th>کل</th><th>در انتظار</th><th>تأیید</th><th>رد</th></tr></thead><tbody id="sourceStats"></tbody></table></div></div></section>
<section id="messages" class="hidden"><div class="card"><div class="row"><select id="mStatus"><option value="">همه وضعیت‌ها</option><option value="pending">در انتظار</option><option value="approved">تأییدشده</option><option value="rejected">ردشده</option></select><input id="mQuery" placeholder="جست‌وجو در متن"><input id="mFrom" type="datetime-local" title="زمان تهران"><input id="mTo" type="datetime-local" title="زمان تهران"></div><button class="primary" onclick="loadMessages()">اعمال فیلتر</button><div class="muted" style="margin-top:8px">همه زمان‌های جدول با تقویم جلالی و منطقه زمانی تهران نمایش داده می‌شوند. تعیین وضعیت دستی از ستون آخر انجام می‌شود.</div></div><div class="card scroll"><table><thead><tr><th>ID</th><th>زمان تهران (جلالی)</th><th>منبع</th><th>وضعیت</th><th>متن</th><th>مبدأ فوروارد</th><th>رسانه/لینک</th><th>تعیین وضعیت</th></tr></thead><tbody id="messageRows"></tbody></table></div></section>
<section id="sources" class="hidden"><div class="card"><h3>افزودن کانال یا گروه پایش</h3><div class="row"><input id="sChatId" placeholder="chat_id عددی (برای گروه بدون نام کاربری)"><input id="sUsername" placeholder="username بدون @"><input id="sTitle" placeholder="عنوان"><input id="sTarget" placeholder="target_chat_id اختیاری"></div><button class="primary" onclick="addSource()">ثبت منبع</button></div><div class="card scroll"><table><thead><tr><th>ID</th><th>chat_id</th><th>نام/عنوان</th><th>نوع</th><th>مقصد</th><th>فعال</th></tr></thead><tbody id="sourceRows"></tbody></table></div></section>
<section id="bulletins" class="hidden"><div class="card"><h3>تولید بولتن</h3><div class="row"><select id="bTemplate"></select><input id="bFrom" type="datetime-local"><input id="bTo" type="datetime-local"><select id="bStatus"><option value="approved">فقط تأییدشده</option><option value="pending,approved">در انتظار و تأیید</option><option value="pending,approved,rejected">همه</option></select></div><div class="muted">انتخاب منبع اختیاری است؛ در صورت خالی‌بودن همه منابع استفاده می‌شوند.</div><div id="bSources" class="grid" style="margin:12px 0"></div><button class="primary" onclick="createBulletin()">شروع تولید</button></div><div class="card scroll"><table><thead><tr><th>ID</th><th>زمان</th><th>قالب</th><th>وضعیت</th><th>پیام‌ها</th><th>مدل</th><th>خروجی</th></tr></thead><tbody id="bulletinRows"></tbody></table></div></section>
<section id="schedules" class="hidden"><div class="card"><h3>زمان‌بندی بولتن</h3><div class="row"><input id="scName" placeholder="نام برنامه"><input id="scCron" placeholder="Cron مثال: 0 8 * * *"><input id="scTimezone" value="Asia/Tehran"><select id="scTemplate"></select></div><button class="primary" onclick="saveSchedule()">ثبت زمان‌بندی</button><div class="muted" style="margin-top:8px">Cron از پنج بخش دقیقه، ساعت، روز ماه، ماه و روز هفته تشکیل می‌شود.</div></div><div class="card scroll"><table><thead><tr><th>ID</th><th>نام</th><th>Cron</th><th>منطقه زمانی</th><th>قالب</th><th>فعال</th></tr></thead><tbody id="scheduleRows"></tbody></table></div></section>
<section id="system" class="hidden"><div class="card"><h3>وضعیت سامانه</h3><button class="primary" onclick="backupNow()">تهیه پشتیبان فوری</button><pre id="systemJson" style="white-space:pre-wrap"></pre></div><div class="card"><h3>رویدادهای اخیر</h3><div class="scroll"><table><thead><tr><th>زمان</th><th>بخش</th><th>سطح</th><th>نوع</th><th>پیام</th></tr></thead><tbody id="eventRows"></tbody></table></div></div></section>
</main><div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});if(!r.ok)throw new Error((await r.text())||r.statusText);return r.json()}
function toast(t){$('toast').textContent=t;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',3500)}
function show(page){document.querySelectorAll('main>section').forEach(x=>x.classList.add('hidden'));$(page).classList.remove('hidden');document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x.dataset.page===page));if(page==='messages')loadMessages();if(page==='sources')loadSources();if(page==='bulletins')loadBulletins();if(page==='schedules')loadSchedules();if(page==='system')loadSystem()}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>show(b.dataset.page));
async function loadStats(){const d=await api('/admin/api/stats');const c=d.counts;const labels={total:'کل پیام‌ها',pending:'در انتظار',approved:'تأییدشده',rejected:'ردشده',without_source:'بدون لینک',without_oration:'بدون خطابه',delivered:'ارسال‌شده',active_sources:'منابع فعال'};$('metrics').innerHTML=Object.entries(labels).map(([k,v])=>`<div class="card"><div class="muted">${v}</div><div class="metric">${c[k]??0}</div></div>`).join('');$('sourceStats').innerHTML=d.by_source.map(x=>`<tr><td>${esc(x.source_name)}</td><td>${x.total}</td><td>${x.pending}</td><td>${x.approved}</td><td>${x.rejected}</td></tr>`).join('')}
const tehranJalaliFormatter=new Intl.DateTimeFormat('fa-IR-u-ca-persian',{timeZone:'Asia/Tehran',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
function formatTehranJalali(value){if(!value)return '—';const d=new Date(value);if(Number.isNaN(d.getTime()))return esc(value);return tehranJalaliFormatter.format(d)}
function tehranInputToUtc(value){if(!value)return '';const normalized=value.length===16?value+':00':value;const d=new Date(normalized+'+03:30');return Number.isNaN(d.getTime())?value:d.toISOString()}
const statusLabels={pending:'در انتظار',approved:'تأییدشده',rejected:'ردشده'};
function decisionButtons(){return ''}
async function setMessageStatus(){toast('چرخهٔ تأیید و رد پیام در این نسخه حذف شده است.')}
async function loadMessages(){const p=new URLSearchParams();if($('mStatus').value)p.set('status',$('mStatus').value);if($('mQuery').value)p.set('q',$('mQuery').value);if($('mFrom').value)p.set('date_from',tehranInputToUtc($('mFrom').value));if($('mTo').value)p.set('date_to',tehranInputToUtc($('mTo').value));const d=await api('/admin/api/messages?'+p);$('messageRows').innerHTML=d.items.map(x=>`<tr><td>${x.id}</td><td class="time-cell" title="${esc(x.received_at||x.created_at||'')}">${esc(formatTehranJalali(x.received_at||x.created_at))}<div class="muted">تهران</div></td><td>${esc(x.source_chat_title||x.source_chat_username||x.source_chat_id)}</td><td><span class="badge ${x.status}">${esc(statusLabels[x.status]||x.status)}</span></td><td class="text">${esc(x.text||x.caption||'[رسانه]')}</td><td>${esc(x.forwarded_origin_title||x.forwarded_origin_username||'نامشخص')}</td><td>${x.media_count||0} / ${x.link_count||0}</td><td>${decisionButtons(x)}</td></tr>`).join('')}
let sourceCache=[];async function loadSources(){sourceCache=await api('/admin/api/sources');$('sourceRows').innerHTML=sourceCache.map(x=>`<tr><td>${x.id}</td><td>${x.chat_id??''}</td><td>${esc(x.title||x.username)}</td><td>${esc(x.chat_type)}</td><td>${x.target_chat_id??''}</td><td><button class="${x.enabled?'primary':'secondary'}" onclick="toggleSource(${x.id},${!x.enabled})">${x.enabled?'فعال':'غیرفعال'}</button></td></tr>`).join('');$('bSources').innerHTML=sourceCache.filter(x=>x.enabled).map(x=>`<label class="card"><input type="checkbox" class="bsrc" value="${x.chat_id??''}" style="width:auto"> ${esc(x.title||x.username||x.chat_id)}</label>`).join('')}
async function addSource(){const body={chat_id:$('sChatId').value?Number($('sChatId').value):null,username:$('sUsername').value||null,title:$('sTitle').value||null,target_chat_id:$('sTarget').value?Number($('sTarget').value):null};await api('/admin/api/sources',{method:'POST',body:JSON.stringify(body)});toast('منبع ثبت شد');loadSources();loadStats()}
async function toggleSource(id,enabled){await api(`/admin/api/sources/${id}`,{method:'PATCH',body:JSON.stringify({enabled})});loadSources();loadStats()}
let templates=[];async function loadTemplates(){templates=await api('/admin/api/templates');const opts=templates.map(x=>`<option value="${x.id}">${esc(x.name)} (v${x.version})</option>`).join('');$('bTemplate').innerHTML=opts;$('scTemplate').innerHTML=opts}
async function loadBulletins(){await Promise.all([loadTemplates(),loadSources()]);const rows=await api('/admin/api/bulletins');$('bulletinRows').innerHTML=rows.map(x=>`<tr><td>${x.id}</td><td>${esc(x.created_at)}</td><td>${esc(x.template_name||'')}</td><td><span class="badge ${x.status}">${x.status}</span></td><td>${x.input_message_count}</td><td>${esc(x.model||'')}</td><td><a href="/admin/download/bulletins/${x.id}.txt">TXT</a> | <a href="/admin/download/bulletins/${x.id}.json">JSON</a></td></tr>`).join('')}
async function createBulletin(){const source_chat_ids=[...document.querySelectorAll('.bsrc:checked')].map(x=>Number(x.value)).filter(Boolean);const body={template_id:Number($('bTemplate').value)||null,date_from:$('bFrom').value||null,date_to:$('bTo').value||null,source_chat_ids,statuses:$('bStatus').value.split(',')};const d=await api('/admin/api/bulletins',{method:'POST',body:JSON.stringify(body)});toast('بولتن در صف قرار گرفت: '+d.id);setTimeout(loadBulletins,1000)}
async function loadSchedules(){await loadTemplates();const rows=await api('/admin/api/schedules');$('scheduleRows').innerHTML=rows.map(x=>`<tr><td>${x.id}</td><td>${esc(x.name)}</td><td>${esc(x.cron_expression)}</td><td>${esc(x.timezone)}</td><td>${esc(x.template_name||'')}</td><td><button class="${x.enabled?'primary':'secondary'}" onclick="toggleSchedule(${x.id},${!x.enabled})">${x.enabled?'فعال':'غیرفعال'}</button></td></tr>`).join('')}
async function saveSchedule(){const body={name:$('scName').value,cron_expression:$('scCron').value,timezone:$('scTimezone').value,template_id:Number($('scTemplate').value)||null,filters:{statuses:['approved']}};await api('/admin/api/schedules',{method:'POST',body:JSON.stringify(body)});toast('زمان‌بندی ثبت شد');loadSchedules()}
async function toggleSchedule(id,enabled){await api(`/admin/api/schedules/${id}/enabled`,{method:'PATCH',body:JSON.stringify({enabled})});loadSchedules()}
async function backupNow(){const d=await api('/admin/api/backup',{method:'POST'});toast('پشتیبان ساخته شد: '+d.database_backup);loadSystem()}
async function loadSystem(){const d=await api('/admin/api/system');$('systemJson').textContent=JSON.stringify({database:d.database,configuration:d.configuration,stats:d.stats},null,2);$('eventRows').innerHTML=d.events.map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.component)}</td><td>${esc(x.level)}</td><td>${esc(x.event_type)}</td><td>${esc(x.message||'')}</td></tr>`).join('')}
loadStats();loadTemplates();loadSources();setInterval(()=>{if(!$('overview').classList.contains('hidden'))loadStats();if(!$('bulletins').classList.contains('hidden'))loadBulletins()},15000);
</script></body></html>'''

BULLETIN_STUDIO_HTML = r'''<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>کارگاه سردبیری گرایه</title>
<style>
@font-face{font-family:IRZarWeb;src:url("/admin/assets/fonts/IRZar.ttf") format("truetype");font-display:swap}
:root{--bg:#fff;--card:#fff;--card2:rgba(0,128,128,.045);--line:rgba(0,78,79,.18);--text:#002223;--muted:#004e4f;--accent:#008080;--blue:#006767;--danger:#00010d;--warn:#004e4f;--ok:#008080}
*{box-sizing:border-box}body{margin:0;font-family:IRZarWeb,IRZar,"B Zar",Tahoma,Arial,sans-serif;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid var(--line);padding:12px 20px;display:flex;gap:14px;justify-content:space-between;align-items:center;flex-wrap:wrap}
header h1{font-size:19px;margin:0}nav{display:flex;gap:4px;flex-wrap:wrap}nav button{background:transparent;color:var(--muted);border:0;border-bottom:2px solid transparent;padding:10px;cursor:pointer}nav button.active{color:var(--accent);border-color:var(--accent)}
main{max-width:1500px;margin:auto;padding:18px}.hidden{display:none!important}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px;margin-bottom:13px}.subcard{background:var(--card2);border:1px solid var(--line);border-radius:9px;padding:10px;margin-top:8px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:10px}.row.two{grid-template-columns:repeat(2,minmax(0,1fr))}.metric{font-size:26px;font-weight:700;margin-top:7px}.muted{color:var(--muted);font-size:12px;line-height:1.8}.field-label{display:block;color:var(--muted);font-size:12px;margin:10px 0 5px}
input,select,textarea{width:100%;background:#fff;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:9px}textarea{min-height:105px;resize:vertical}button,.button{border:0;border-radius:7px;padding:9px 12px;cursor:pointer;color:#fff;text-decoration:none;display:inline-block;font-weight:700}.primary{background:var(--accent)}.blue{background:var(--blue)}.danger{background:var(--danger)}.warn{background:var(--warn)}.secondary{background:rgba(0,78,79,.10);color:#003738}.actions{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-top:10px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}th{color:var(--muted)}.table-wrap{overflow:auto}.badge{display:inline-block;border-radius:999px;padding:3px 8px;font-size:11px;background:#004e4f;color:#fff}.approved,.completed{background:#008080}.rejected,.failed,.blocked{background:#00010d}.review_pending,.completed_with_warnings{background:#004e4f}.running{background:#006767}.item.approved-card{border-right:4px solid var(--ok)}.item.rejected-card{border-right:4px solid var(--danger)}.evidence{background:rgba(0,128,128,.04);border:1px solid var(--line);border-radius:8px;padding:10px;margin-top:8px;white-space:pre-wrap;line-height:1.9}.sentence{border-right:3px solid var(--blue)}.issue-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:6px;margin-top:8px}.issue-grid label{background:#fff;border:1px solid var(--line);border-radius:7px;padding:7px;font-size:12px}.issue-grid input{width:auto;margin-left:5px}.code{direction:ltr;text-align:left;background:rgba(0,34,35,.04);border:1px solid var(--line);padding:10px;border-radius:7px;white-space:pre-wrap;max-height:260px;overflow:auto}.toast{position:fixed;left:18px;bottom:18px;z-index:30;background:#003738;color:#fff;padding:11px 14px;border-radius:8px;display:none;max-width:420px}.person-editor{background:rgba(0,128,128,.04);border:1px solid var(--line);border-radius:9px;padding:10px;margin:10px 0}@media(max-width:900px){.row,.row.two{grid-template-columns:1fr}main{padding:10px}}
</style>
</head>
<body>
<header><div><h1>کارگاه سردبیری و کنترل کیفیت گرایه</h1><div class="muted">نسخه ۱۱.۳.۰ — بازتدوین انسانی و خروجی کلاسیک/نشریه‌ای بدون تحلیل مجدد پیام‌ها</div></div><nav><button class="active" data-tab="runs">اجراها</button><button data-tab="registry">شناسنامه</button><button data-tab="topics">موضوعات</button><button data-tab="quality">کیفیت و ارزیابی</button><a class="button secondary" href="/admin">داشبورد اصلی</a></nav></header>
<main>
<section id="tab-runs">
<div class="card"><div class="actions" style="justify-content:space-between"><h3>وضعیت اتصال هوش</h3><button class="secondary" onclick="loadAIStatus()">تازه‌سازی</button></div><div id="aiStatus" class="grid"></div><details><summary>جزئیات امن مخزن کلیدها</summary><pre id="aiStatusRaw" class="code"></pre></details></div>
<div class="card"><h3>ایجاد اجرای بولتن</h3><div class="row"><div><label class="field-label">قالب</label><select id="template"></select></div><div><label class="field-label">شماره خبرنامه</label><input id="issueNumber" type="number" min="1" placeholder="خودکار"></div><div><label class="field-label">حالت گزارش</label><select id="reportMode"><option value="concise">خلاصه مدیریتی</option><option value="full">کامل و مستند</option></select></div><div><label class="field-label">از تاریخ جلالی و ساعت تهران</label><input id="from" placeholder="۱۴۰۵/۰۴/۳۰ ۰۰:۰۰"></div><div><label class="field-label">تا تاریخ جلالی و ساعت تهران</label><input id="to" placeholder="۱۴۰۵/۰۴/۳۰ ۲۳:۵۹:۵۹"></div><div><label class="field-label">وضعیت پیام‌ها</label><select id="statuses"><option value="approved">فقط تأییدشده</option><option value="approved,pending">تأییدشده و در انتظار</option></select></div></div><div id="sources" class="grid"></div><div class="actions"><button class="secondary" onclick="setTodayTehran()">قرار دادن بازه امروز تهران</button><button class="primary" onclick="createRun()">ایجاد و پردازش</button></div></div>
<div class="card"><div class="actions" style="justify-content:space-between"><h3>اجراهای بولتن</h3><button class="secondary" onclick="loadRuns()">تازه‌سازی</button></div><div class="table-wrap"><table><thead><tr><th>شناسه</th><th>ایجاد</th><th>وضعیت</th><th>بازبینی</th><th>پیام</th><th>آیتم</th><th>روش</th><th></th></tr></thead><tbody id="runRows"></tbody></table></div></div>
<div id="runPanel" class="hidden">
<div class="card"><div class="actions" style="justify-content:space-between"><div><h3>اجرای <span id="runId"></span></h3><div id="runPeriod" class="muted"></div><div id="runState" class="muted"></div></div><div class="actions"><select id="reprocessMode" style="width:190px"><option value="all">بازپردازش کامل</option><option value="person">تطبیق مجدد اشخاص</option><option value="topic">موضوع‌بندی مجدد</option><option value="dedup">حذف تکرار مجدد</option></select><button class="warn" onclick="reprocessRun()">ساخت اجرای جدید</button><button class="secondary" onclick="reconcileEditorialAI()">ثبت نهایی تصمیمات سردبیری</button><button class="blue" onclick="rebuildEditorial()">بازتدوین سردبیری با هوش</button><button class="primary" onclick="exportRun()">تولید خروجی‌های نهایی</button><a id="logsLink" class="button secondary">لاگ پردازش JSON</a><a id="conciseLink" class="button blue hidden">Word خلاصه</a><a id="fullLink" class="button blue hidden">Word کامل</a><a id="classicPdfLink" class="button blue hidden">PDF کلاسیک</a><a id="magazinePdfLink" class="button warn hidden">PDF نشریه‌ای</a><a id="magazineHtmlLink" class="button secondary hidden">پیش‌نمایش نشریه</a><a id="dataLink" class="button secondary hidden">JSON نهایی</a><a id="auditLink" class="button secondary hidden">ممیزی Excel</a><a id="unregisteredLink" class="button secondary hidden">افراد خارج شناسنامه</a><a id="runLogLink" class="button secondary hidden">run.log</a><a id="errorsLogLink" class="button secondary hidden">errors.log</a><a id="zipLink" class="button secondary hidden">ZIP کامل</a></div></div><div class="muted" style="margin:10px 0">«بازتدوین سردبیری با هوش» فقط آیتم‌های تأییدشده و شواهد موجود را بازنویسی می‌کند؛ تشخیص اولیه پیام‌ها و تصمیم‌های تأیید/رد دوباره اجرا نمی‌شوند.</div><div id="editorialStatus" class="subcard"></div><div id="itemStats" class="grid"></div><div id="qualityStats" class="grid"></div><details><summary>گزارش پردازش</summary><pre id="report" class="code"></pre></details><details open><summary>لاگ مرحله‌به‌مرحله</summary><div id="runLogs" class="table-wrap"></div></details><details><summary>خطاهای پیام و خوشه</summary><div id="runErrors"></div></details><details><summary>پیام‌های معلق یا حل‌نشده توسط هوش</summary><div id="pendingAI"></div></details></div>
<div id="items"></div>
</div>
</section>
<section id="tab-registry" class="hidden">
<div class="card"><h3>ثبت یا ویرایش شخص</h3><input id="personId" type="hidden"><div class="row"><input id="personName" placeholder="نام کامل"><input id="personPosition" placeholder="سمت"><input id="personCategory" placeholder="دسته"><select id="personRegistry"><option value="inside">داخل شناسنامه</option><option value="outside">خارج شناسنامه</option></select></div><div class="row"><input id="personAliases" placeholder="القاب با | جدا شوند"><input id="personChatId" placeholder="شناسه عددی کانال"><input id="personUsername" placeholder="نام کاربری"><input id="personPriority" type="number" value="0" placeholder="اولویت"><input id="personPortrait" type="file" accept="image/png,image/jpeg,image/webp" title="تصویر شخصیت"><label><input id="personActive" type="checkbox" checked style="width:auto"> فعال</label><button class="primary" onclick="savePerson()">ذخیره</button><button class="secondary" onclick="clearPersonForm()">پاک‌کردن فرم</button></div></div>
<div class="card"><h3>ورود گروهی</h3><input id="peopleFile" type="file" accept=".csv,.xlsx"><button class="primary" onclick="importPeople()">بارگذاری</button></div>
<div class="card"><h3>اشخاص</h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>نام</th><th>سمت</th><th>دسته</th><th>وضعیت</th><th>القاب</th><th>کانال</th><th>عملیات</th></tr></thead><tbody id="peopleRows"></tbody></table></div></div>
<div class="card"><h3>نامزدهای ناشناخته</h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>نام</th><th>اطمینان</th><th>نمونه</th><th>وضعیت</th><th>اقدام</th></tr></thead><tbody id="candidateRows"></tbody></table></div></div>
</section>
<section id="tab-topics" class="hidden"><div class="card"><h3>افزودن موضوع</h3><div class="row two"><input id="topicName" placeholder="نام موضوع"><input id="topicKeywords" placeholder="کلیدواژه‌ها با | جدا شوند"></div><button class="primary" onclick="saveTopic()">ثبت موضوع</button></div><div id="topicCards" class="grid"></div></section>
<section id="tab-quality" class="hidden">
<div class="card"><div class="actions" style="justify-content:space-between"><h3>گزارش کیفیت کل سامانه</h3><div><button class="secondary" onclick="loadOverallQuality()">تازه‌سازی</button><a class="button blue" href="/admin/download/evaluation-cases.csv">دریافت CSV ارزیابی</a></div></div><div id="overallQuality" class="grid"></div><div id="issueQuality" class="subcard"></div></div>
<div class="card"><h3>مجموعه ارزیابی سردبیری</h3><div class="muted">هر آیتم ذخیره‌شده، شامل شخص، موضوع، خلاصه نهایی و شناسه پیام‌های شاهد است و برای سنجش نسخه‌های بعدی استفاده می‌شود.</div><div class="table-wrap"><table><thead><tr><th>ID</th><th>اجرا</th><th>آیتم</th><th>شخص مطلوب</th><th>موضوع</th><th>خلاصه مطلوب</th><th>یادداشت</th><th>ثبت</th></tr></thead><tbody id="evaluationRows"></tbody></table></div></div>
</section>
</main>
<datalist id="peopleOptions"></datalist><div id="toast" class="toast"></div>
<script>
const $=id=>document.getElementById(id);let currentRun=null,runTimer=null,peopleCache=[];
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function toast(msg){const el=$('toast');el.textContent=msg;el.style.display='block';setTimeout(()=>el.style.display='none',4200)}
async function api(url,opt={}){const r=await fetch(url,opt);const text=await r.text();let data;try{data=JSON.parse(text)}catch{data=text}if(!r.ok)throw new Error(data.detail||data||`خطای ${r.status}`);return data}
function fdate(v){if(!v)return '—';const d=new Date(v);if(Number.isNaN(d.getTime()))return esc(v);return new Intl.DateTimeFormat('fa-IR-u-ca-persian',{timeZone:'Asia/Tehran',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(d)}
function pnum(v){return new Intl.NumberFormat('fa-IR',{maximumFractionDigits:2}).format(Number(v||0))}
function canonicalPerson(v){return String(v||'').replace(/ي/g,'ی').replace(/ك/g,'ک').replace(/\s+/g,' ').trim().toLowerCase()}
function setTodayTehran(){const parts=new Intl.DateTimeFormat('fa-IR-u-ca-persian',{timeZone:'Asia/Tehran',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());const o={};parts.forEach(x=>o[x.type]=x.value);const date=`${o.year}/${o.month}/${o.day}`;$('from').value=date+' ۰۰:۰۰:۰۰';$('to').value=date+' ۲۳:۵۹:۵۹'}
function parseJSON(v,fallback){try{return JSON.parse(v||'')}catch{return fallback}}
function qualityCards(q){const m=[['کل آیتم',q.total_items],['تأیید بدون ویرایش',q.approved_without_edit],['تأیید ویرایش‌شده',q.approved_with_edit],['ردشده',q.rejected],['نرخ تأیید',`${pnum((q.approval_rate||0)*100)}٪`],['میانگین تغییر سردبیر',`${pnum((q.average_edit_distance||0)*100)}٪`],['اطمینان پایین',q.low_confidence_items],['نمونه ارزیابی',q.evaluation_cases]];return m.map(([k,v])=>`<div class="card"><div class="muted">${esc(k)}</div><div class="metric">${esc(v??0)}</div></div>`).join('')}
document.querySelectorAll('nav button[data-tab]').forEach(b=>b.onclick=async()=>{document.querySelectorAll('nav button[data-tab]').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('main>section').forEach(x=>x.classList.add('hidden'));$('tab-'+b.dataset.tab).classList.remove('hidden');if(b.dataset.tab==='registry'){await loadPeople();await loadCandidates()}if(b.dataset.tab==='topics')await loadTopics();if(b.dataset.tab==='quality'){await loadOverallQuality();await loadEvaluationCases()}});
function refreshPeopleOptions(){const opts=[];for(const p of peopleCache){opts.push(`<option value="${esc(p.full_name)}">${esc((p.position||p.category||'')+' — '+(p.registry_status==='inside'?'داخل شناسنامه':'خارج شناسنامه'))}</option>`);for(const a of String(p.aliases||'').split('|').map(x=>x.trim()).filter(Boolean)){if(canonicalPerson(a)!==canonicalPerson(p.full_name))opts.push(`<option value="${esc(a)}">نام دیگر ${esc(p.full_name)}</option>`)}}$('peopleOptions').innerHTML=opts.join('')}
async function fetchPeople(){peopleCache=await api('/admin/api/people');refreshPeopleOptions();return peopleCache}
async function loadAIStatus(){try{const d=await api('/admin/api/ai/status');const pool=d.pool||{};$('aiStatus').innerHTML=[['وضعیت',d.enabled?'فعال':'غیرفعال'],['ارائه‌دهنده',d.provider||'—'],['مدل',d.model||'—'],['کلیدهای تنظیم‌شده',pool.configured_key_count??0],['هم‌زمانی کل',pool.max_concurrency??0],['هم‌زمانی هر کلید',pool.concurrency_per_key??0]].map(x=>`<div class="subcard"><div class="muted">${esc(x[0])}</div><div class="metric" style="font-size:18px">${esc(x[1])}</div></div>`).join('');$('aiStatusRaw').textContent=JSON.stringify(d,null,2)}catch(e){$('aiStatus').innerHTML=`<div class="subcard"><span class="badge failed">خطا</span> ${esc(e.message)}</div>`}}
async function loadSetup(){const [templates,sources]=await Promise.all([api('/admin/api/templates'),api('/admin/api/sources'),fetchPeople()]);$('template').innerHTML=templates.map(x=>`<option value="${x.id}" ${x.output_format==='json_schema'?'selected':''}>${esc(x.name)} (v${x.version})</option>`).join('');$('sources').innerHTML=sources.filter(x=>x.enabled).map(x=>`<label class="card"><input style="width:auto" type="checkbox" class="source" value="${x.chat_id||''}"> ${esc(x.title||x.username||x.chat_id)}</label>`).join('');if(!$('from').value)setTodayTehran();await loadAIStatus()}
async function createRun(){const body={template_id:Number($('template').value)||null,issue_number:Number($('issueNumber').value)||null,report_mode:$('reportMode').value,date_from_jalali:$('from').value.trim()||null,date_to_jalali:$('to').value.trim()||null,timezone:'Asia/Tehran',source_chat_ids:[...document.querySelectorAll('.source:checked')].map(x=>Number(x.value)).filter(Boolean),statuses:$('statuses').value.split(','),export_options:{concise:true,full:true,json:true,audit:true,unregistered:true,logs:true}};try{const d=await api('/admin/api/bulletins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('اجرای '+d.id+' ساخته شد');await loadRuns();await selectRun(d.id)}catch(e){toast(e.message)}}
async function loadRuns(){const rows=await api('/admin/api/bulletins');$('runRows').innerHTML=rows.map(x=>{const rep=parseJSON(x.processing_report_json,{}),displayStatus=x.status==='completed'&&Number(x.warning_count||0)>0?'completed_with_warnings':x.status;return `<tr><td>${x.id}</td><td>${fdate(x.created_at)}</td><td><span class="badge ${esc(displayStatus)}">${esc(displayStatus)}</span><div class="muted">${esc(x.current_stage||'—')}</div></td><td>${esc(x.review_status||'—')}</td><td>${x.input_message_count||0}<div class="muted">موفق ${x.processed_message_count||0} / خطادار ${x.skipped_message_count||0}</div></td><td>${rep.items??'—'}</td><td>${esc(rep.summary_version||x.pipeline_version||'—')}</td><td><button class="blue" onclick="selectRun(${x.id})">بازبینی و لاگ</button></td></tr>`}).join('')}
async function selectRun(id){currentRun=id;$('runId').textContent=id;$('runPanel').classList.remove('hidden');await loadRun();clearInterval(runTimer);runTimer=setInterval(loadRun,6000)}
async function loadRun(){if(!currentRun)return;const [run,items,q,logs,errors,pendingAI,editorial]=await Promise.all([api(`/admin/api/bulletins/${currentRun}`),api(`/admin/api/bulletins/${currentRun}/items`),api(`/admin/api/bulletins/${currentRun}/quality`),api(`/admin/api/bulletins/${currentRun}/logs`),api(`/admin/api/bulletins/${currentRun}/errors`),api(`/admin/api/bulletins/${currentRun}/pending-ai`),api(`/admin/api/bulletins/${currentRun}/editorial-status`)]);$('editorialStatus').innerHTML=editorial.exists?`<b>بسته سردبیری معتبر:</b> ${esc(editorial.version||'')} | ${editorial.people} شخصیت | ${editorial.controversies} رویداد | ${esc(fdate(editorial.generated_at))}`:'<b>بسته سردبیری ساخته نشده است.</b> پس از تعیین تکلیف آیتم‌ها، دکمه «بازتدوین سردبیری با هوش» را بزنید.';const report=parseJSON(run.processing_report_json,{}),displayStatus=run.status==='completed'&&Number(run.warning_count||0)>0?'completed_with_warnings':run.status;$('report').textContent=JSON.stringify(report,null,2);$('runPeriod').textContent=`بازه تهران: ${fdate(run.date_from)} تا ${fdate(run.date_to)} | ایجاد: ${fdate(run.created_at)}`;$('runState').innerHTML=`وضعیت: <span class="badge ${esc(displayStatus)}">${esc(displayStatus)}</span> | مرحله فعلی: ${esc(run.current_stage||'—')} | پیام موفق: ${run.processed_message_count||0} | پیام کنارگذاشته‌شده: ${run.skipped_message_count||0} | هشدار: ${run.warning_count||0} | معلق هوش: ${pendingAI.length}${run.failed_stage?` | مرحله شکست: ${esc(run.failed_stage)}`:''}`;$('logsLink').href=`/admin/download/bulletins/${currentRun}/logs.json`;const c={review_pending:0,approved:0,rejected:0,inside:0,outside:0};items.forEach(x=>{c[x.status]=(c[x.status]||0)+1;c[x.registry_bucket]=(c[x.registry_bucket]||0)+1});$('itemStats').innerHTML=Object.entries(c).map(([k,v])=>`<div class="card"><div class="muted">${esc(k)}</div><div class="metric">${v}</div></div>`).join('');$('qualityStats').innerHTML=qualityCards(q);$('items').innerHTML=items.map(itemCard).join('');$('runLogs').innerHTML=renderRunLogs(logs);$('runErrors').innerHTML=renderRunErrors(errors,run);$('pendingAI').innerHTML=renderPendingAI(pendingAI);if(['completed','failed','blocked'].includes(run.status))clearInterval(runTimer)}
function renderRunLogs(rows){if(!rows.length)return '<div class="muted">لاگی ثبت نشده است.</div>';return `<table><thead><tr><th>زمان</th><th>مرحله</th><th>وضعیت</th><th>پیام</th><th>شناسه پیام</th><th>مدت</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${fdate(x.created_at)}</td><td>${esc(x.stage)}</td><td><span class="badge ${esc(x.status)}">${esc(x.status)}</span></td><td>${esc(x.message||'')}</td><td>${x.message_id||'—'}</td><td>${x.duration_ms==null?'—':pnum(x.duration_ms)+' ms'}</td></tr>`).join('')}</tbody></table>`}
function renderRunErrors(rows,run){const fatal=run.error_text?`<div class="card rejected-card"><b>خطای نهایی اجرا</b><div class="muted">مرحله: ${esc(run.failed_stage||run.current_stage||'نامشخص')} | نوع: ${esc(run.error_type||'')}</div><pre class="code">${esc(run.error_text||'')}
${esc(run.error_traceback||'')}</pre></div>`:'';if(!rows.length)return fatal||'<div class="muted">خطای سطح پیام یا خوشه ثبت نشده است.</div>';return fatal+rows.map(x=>`<details class="subcard"><summary>خطا ${x.error_id} — مرحله ${esc(x.stage)} — پیام ${x.message_id||'—'}</summary><div class="muted">${fdate(x.created_at)} | ${esc(x.error_type)} | ${esc(x.source_chat_title||x.source_chat_username||'')}</div><pre class="code">${esc(x.error_message||'')}
${esc(x.error_traceback||'')}</pre><div class="evidence">${esc((x.text||x.caption||x.payload_snapshot||'').slice(0,2500))}</div></details>`).join('')}
function renderPendingAI(rows){if(!rows||!rows.length)return '<div class="subcard">هیچ پیام معلقی وجود ندارد؛ همه تصمیم‌های سردبیری ثبت شده‌اند.</div>';return `<div class="subcard"><b>${rows.length} پیام هنوز تعیین تکلیف مستقیم می‌خواهد.</b><div class="muted">این دکمه‌ها AI را دوباره اجرا نمی‌کنند. «پوشش داده‌شده» فقط وقتی مجاز است که پیام به آیتم‌های نهایی متصل باشد.</div></div>`+rows.map(x=>`<div class="evidence"><div class="actions" style="justify-content:space-between"><b>پیام ${x.message_id}</b><span class="badge review_pending">${esc(x.ai_enrichment_status||x.relevance_status||'pending_ai')}</span></div><div class="muted">منبع: ${esc(x.source_chat_title||x.source_chat_username||'نامشخص')} | شناسه منبع: ${esc(x.source_message_id||'—')} | اطمینان: ${pnum(x.ai_enrichment_confidence)}</div><div class="muted">شخص پیشنهادی: ${esc(x.detected_person_name||'—')} | موضوع پیشنهادی: ${esc(x.topic_name||'—')}</div><div style="margin-top:7px;white-space:pre-wrap">${esc((x.message_text||'').slice(0,1200))}</div><div class="actions" style="margin-top:10px"><button class="primary" onclick="resolvePendingAI(${x.message_id},'covered')">پوشش داده‌شده با آیتم‌ها</button><button class="danger" onclick="resolvePendingAI(${x.message_id},'exclude')">خارج از بولتن</button></div></div>`).join('')}
function itemCard(x){const summary=x.edited_summary||x.summary||'';const selected=parseJSON(x.selected_sentences_json,[]);const loc=x.statement_location_label||'محل بیان نامشخص';const versions=parseJSON(x.pipeline_versions_json,{});const breakdown=parseJSON(x.confidence_breakdown_json,{});const selectedHtml=selected.length?`<details class="subcard"><summary>جمله‌های انتخاب‌شده (${selected.length})</summary>${selected.map(s=>`<div class="evidence sentence"><b>پیام ${s.message_id}، جمله ${Number(s.sentence_index)+1}</b> — امتیاز ${pnum(s.score)}<br>${esc(s.source_sentence||s.claim||'')}<br><span class="muted">${esc((s.reasons||[]).join('، '))}</span></div>`).join('')}</details>`:'';const issues=[['name_wrong','نام نادرست'],['topic_wrong','موضوع نادرست'],['summary_incomplete','خلاصه ناقص'],['summary_redundant','خلاصه تکراری'],['meaning_changed','تغییر معنا'],['location_wrong','محل بیان نادرست'],['duplicate_error','خطای تکرار'],['other','سایر']].map(([v,l])=>`<label><input type="checkbox" name="issue-${x.item_id}" value="${v}">${l}</label>`).join('');return `<div class="card item ${x.status==='approved'?'approved-card':x.status==='rejected'?'rejected-card':''}"><div class="actions" style="justify-content:space-between"><div><b>${esc(x.person_name)}</b> — ${esc(x.topic_name)} <span class="badge ${esc(x.status)}">${esc(x.status)}</span></div><div class="muted">شاهد: ${x.evidence_count||0} | تکرار: ${x.duplicate_count||0} | اطمینان: ${pnum(x.confidence)}</div></div><div class="muted">${esc(loc)} | روش: ${esc(x.summary_method||'—')} | نسخه: ${esc(x.summary_version||versions.summary||'—')}</div>${x.review_reason?`<div class="subcard">نیازمند توجه: ${esc(x.review_reason)}</div>`:''}<div class="person-editor"><label class="field-label">نام تشخیص‌داده‌شده</label><input id="person-${x.item_id}" list="peopleOptions" value="${esc(x.person_name)}"><div class="muted">نام یا لقب را اصلاح کنید؛ تطبیق با شناسنامه هنگام ثبت انجام می‌شود.</div></div><label class="field-label">خلاصه استخراجی؛ قابل ویرایش</label><textarea id="summary-${x.item_id}">${esc(summary)}</textarea>${selectedHtml}<details class="subcard"><summary>اطمینان و نسخه اجزا</summary><pre class="code">${esc(JSON.stringify({confidence:breakdown,versions},null,2))}</pre></details><label class="field-label">علت یا نوع اصلاح</label><div class="issue-grid">${issues}</div><input id="reason-${x.item_id}" style="margin-top:8px" placeholder="توضیح اختیاری سردبیر"><div class="actions"><button class="primary" onclick="moderate(${x.item_id},'approved')">ثبت اصلاحات و تأیید</button><button class="danger" onclick="moderate(${x.item_id},'rejected')">رد</button><button class="secondary" onclick="moderate(${x.item_id},'review_pending')">فقط ذخیره</button><button class="blue" onclick="showEvidence(${x.item_id})">شواهد</button><button class="warn" onclick="reprocessSummary(${x.item_id})">بازسازی و اصلاح با هوش</button><button class="secondary" onclick="saveEvaluation(${x.item_id})">ذخیره در مجموعه ارزیابی</button></div><div id="evidence-${x.item_id}" class="hidden"></div></div>`}
function resolvePersonInput(name){const key=canonicalPerson(name);for(const p of peopleCache){if(canonicalPerson(p.full_name)===key)return p;if(String(p.aliases||'').split('|').map(canonicalPerson).includes(key))return p}return null}
async function moderate(id,status){const edited_summary=$('summary-'+id).value.trim(),person_name=$('person-'+id).value.trim(),reason=$('reason-'+id).value.trim()||null,issue_tags=[...document.querySelectorAll(`input[name="issue-${id}"]:checked`)].map(x=>x.value);if(!person_name){toast('نام شخص خالی است');return}const matched=resolvePersonInput(person_name);if(status==='approved'&&!confirm(`آیتم با نام «${matched?matched.full_name:person_name}» تأیید شود؟`))return;try{const d=await api(`/admin/api/bulletin-items/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status,edited_summary,reason,person_name,person_id:matched?matched.person_id:null,issue_tags})});toast(d.person_changed?'نام و وضعیت ثبت شد':'وضعیت ثبت شد');await loadRun()}catch(e){toast(e.message)}}
async function showEvidence(id){const box=$('evidence-'+id);if(!box.classList.contains('hidden')){box.classList.add('hidden');return}const d=await api(`/admin/api/bulletin-items/${id}`);box.innerHTML=(d.messages||[]).map(m=>`<div class="evidence"><b>پیام ${m.id} — ${esc(m.relation_type)}</b><br>${esc(m.normalized_text||m.text||m.caption||'[رسانه]')}<br><span class="muted">${esc(m.source_chat_title||m.source_chat_username||m.source_chat_id)} | ${fdate(m.published_at||m.received_at||m.created_at)} | کیفیت منبع: ${pnum(m.source_quality)} | اهمیت: ${pnum(m.importance_score)}</span></div>`).join('');box.classList.remove('hidden')}
async function reprocessSummary(id){if(!confirm('خلاصه این آیتم با هوش و چرخه اصلاح از نو ساخته شود؟ ویرایش فعلی پاک و آیتم به بررسی برمی‌گردد.'))return;try{await api(`/admin/api/bulletin-items/${id}/reprocess-summary`,{method:'POST'});toast('خلاصه با هوش بازسازی و برای کنترل مجدد ثبت شد');await loadRun()}catch(e){toast(e.message)}}
async function reprocessRun(){if(!currentRun)return;const mode=$('reprocessMode').value;if(!confirm('یک اجرای جدید بر اساس این اجرا ساخته شود؟ اجرای فعلی محفوظ می‌ماند.'))return;try{const d=await api(`/admin/api/bulletins/${currentRun}/reprocess`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});toast('اجرای جدید '+d.new_run_id+' ساخته شد');await loadRuns();await selectRun(d.new_run_id)}catch(e){toast(e.message)}}
async function reconcileEditorialAI(){if(!currentRun)return;try{const d=await api(`/admin/api/bulletins/${currentRun}/reconcile-editorial-ai`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exclude_unlinked:false})});toast(`بدون اجرای دوباره هوش: ${d.resolved_items||0} آیتم و ${d.resolved_messages||0} پیام همگام شد؛ باقی‌مانده ${d.remaining_messages||0}`);await loadRun()}catch(e){toast(e.message)}}
async function resolvePendingAI(messageId,decision){if(!currentRun)return;const label=decision==='exclude'?'خارج از بولتن':'پوشش‌داده‌شده با آیتم‌ها';if(!confirm(`پیام ${messageId} به‌عنوان «${label}» ثبت شود؟ این کار هیچ تحلیل تازه‌ای از هوش نمی‌گیرد.`))return;try{const d=await api(`/admin/api/bulletins/${currentRun}/pending-ai/${messageId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision})});toast(`پیام تعیین تکلیف شد؛ ${d.remaining_messages||0} پیام باقی مانده است`);await loadRun()}catch(e){toast(e.message)}}
async function saveEvaluation(id){const notes=prompt('یادداشت ارزیابی اختیاری:','')||null;try{await api(`/admin/api/bulletin-items/${id}/evaluation`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({notes})});toast('نمونه ارزیابی ذخیره شد')}catch(e){toast(e.message)}}
async function rebuildEditorial(){if(!currentRun)return;if(!confirm('فقط آیتم‌های تأییدشده بازتدوین شوند؟ تحلیل اولیه پیام‌ها و تصمیم‌های سردبیر تغییر نمی‌کند.'))return;try{toast('بازتدوین سردبیری آغاز شد؛ صفحه را نبندید');const d=await api(`/admin/api/bulletins/${currentRun}/editorial-rebuild`,{method:'POST'});toast(`بازتدوین کامل شد: ${d.people} شخصیت و ${d.controversies} رویداد`);await loadRun()}catch(e){toast(e.message)}}
async function exportRun(){if(!currentRun)return;try{const d=await api(`/admin/api/bulletins/${currentRun}/export`,{method:'POST'});const links={conciseLink:['concise.docx','concise_docx'],fullLink:['full.docx','full_docx'],classicPdfLink:['classic.pdf','classic_pdf'],magazinePdfLink:['magazine.pdf','magazine_pdf'],magazineHtmlLink:['magazine.html','magazine_html'],dataLink:['data.json','data_json'],auditLink:['audit.xlsx','audit_xlsx'],unregisteredLink:['unregistered.xlsx','unregistered_xlsx'],runLogLink:['run.log','run_log'],errorsLogLink:['errors.log','errors_log'],zipLink:['all.zip','zip']};for(const [id,[route,key]] of Object.entries(links)){if(d.files&&d.files[key]){$(id).href=`/admin/download/bulletins/${currentRun}/${route}`;$(id).classList.remove('hidden')}else{$(id).classList.add('hidden')}}toast(d.validation_status==='warning'?'خروجی‌ها ساخته شدند؛ هشدارهای ممیزی در گزارش ثبت شده‌اند.':'تمام خروجی‌های صفحه‌آرایی ساخته شد')}catch(e){toast(e.message)}}
async function loadPeople(){const rows=await fetchPeople();$('peopleRows').innerHTML=rows.map(x=>`<tr><td>${x.person_id}</td><td><img src="/admin/portraits/${x.person_id}" onerror="this.style.display='none'" style="width:32px;height:38px;object-fit:cover;vertical-align:middle;margin-left:6px">${esc(x.full_name)}${Number(x.active)?'':' <span class="badge rejected">غیرفعال</span>'}</td><td>${esc(x.position||'')}</td><td>${esc(x.category||'')}</td><td>${esc(x.registry_status)}</td><td>${esc(x.aliases||'')}</td><td>${x.channel_count||0}</td><td><div class="actions"><button class="blue" onclick="editPerson(${x.person_id})">ویرایش</button><button class="warn" onclick="mergePerson(${x.person_id})">ادغام</button></div></td></tr>`).join('')}
async function loadCandidates(){const rows=await api('/admin/api/person-candidates?status=pending');$('candidateRows').innerHTML=rows.map(x=>`<tr><td>${x.candidate_id}</td><td>${esc(x.detected_name)}</td><td>${pnum(x.confidence)}</td><td>${esc((x.sample_text||x.sample_caption||'').slice(0,180))}</td><td>${esc(x.status)}</td><td><div class="actions"><button class="primary" onclick="reviewCandidate(${x.candidate_id},'approve')">تأیید خارج شناسنامه</button><button class="blue" onclick="mergeCandidate(${x.candidate_id})">ادغام</button><button class="danger" onclick="reviewCandidate(${x.candidate_id},'reject')">رد</button></div></td></tr>`).join('')}
async function reviewCandidate(id,action,merge_person_id=null){await api(`/admin/api/person-candidates/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,merge_person_id})});toast('نامزد تعیین تکلیف شد');await Promise.all([loadCandidates(),loadPeople()])}
async function mergeCandidate(id){const target=prompt('شناسه عددی شخص مقصد:');if(target)await reviewCandidate(id,'merge',Number(target))}
function clearPersonForm(){['personId','personName','personPosition','personCategory','personAliases','personChatId','personUsername'].forEach(id=>$(id).value='');$('personPriority').value='0';$('personActive').checked=true;$('personRegistry').value='inside';$('personPortrait').value=''}
async function editPerson(id){const p=await api(`/admin/api/people/${id}`);$('personId').value=p.person_id;$('personName').value=p.full_name||'';$('personPosition').value=p.position||'';$('personCategory').value=p.category||'';$('personRegistry').value=p.registry_status||'inside';$('personAliases').value=(p.alias_rows||[]).filter(a=>canonicalPerson(a.alias_text)!==canonicalPerson(p.full_name)).map(a=>a.alias_text).join(' | ');$('personPriority').value=p.priority||0;$('personActive').checked=Boolean(Number(p.active));window.scrollTo({top:0,behavior:'smooth'})}
async function mergePerson(source){const target=Number(prompt('شناسه شخص اصلی مقصد را وارد کنید:'));if(!target||target===source)return;if(!confirm(`شخص ${source} در ${target} ادغام و غیرفعال شود؟`))return;try{await api('/admin/api/people/merge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_person_id:source,target_person_id:target})});toast('ادغام انجام شد');await loadPeople()}catch(e){toast(e.message)}}
async function savePerson(){const body={person_id:Number($('personId').value)||null,full_name:$('personName').value,position:$('personPosition').value||null,category:$('personCategory').value||null,registry_status:$('personRegistry').value,active:$('personActive').checked,priority:Number($('personPriority').value)||0,replace_aliases:true,aliases:$('personAliases').value.split('|').map(x=>x.trim()).filter(Boolean),chat_id:$('personChatId').value?Number($('personChatId').value):null,username:$('personUsername').value||null};try{const saved=await api('/admin/api/people',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const portrait=$('personPortrait').files[0];if(portrait){const fd=new FormData();fd.append('file',portrait);await api(`/admin/api/people/${saved.person_id}/portrait`,{method:'POST',body:fd})}toast(body.person_id?'شناسنامه ویرایش شد':'شخص ثبت شد');clearPersonForm();await loadPeople()}catch(e){toast(e.message)}}
async function importPeople(){const file=$('peopleFile').files[0];if(!file){toast('فایل را انتخاب کنید');return}const fd=new FormData();fd.append('file',file);try{const d=await api('/admin/api/people/import',{method:'POST',body:fd});toast(`${d.imported} نفر وارد شد`);await loadPeople()}catch(e){toast(e.message)}}
async function loadTopics(){const rows=await api('/admin/api/topics');$('topicCards').innerHTML=rows.map(x=>`<div class="card"><b>${esc(x.name)}</b><div class="muted" style="margin-top:8px">${(x.keywords||[]).map(k=>esc(k.keyword)).join('، ')||'بدون کلیدواژه'}</div></div>`).join('')}
async function saveTopic(){const body={name:$('topicName').value,keywords:$('topicKeywords').value.split('|').map(x=>x.trim()).filter(Boolean)};try{await api('/admin/api/topics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('موضوع ثبت شد');await loadTopics()}catch(e){toast(e.message)}}
async function loadOverallQuality(){const q=await api('/admin/api/quality');$('overallQuality').innerHTML=qualityCards(q);$('issueQuality').innerHTML='<b>بیشترین علل اصلاح</b><br>'+((q.issue_tags||[]).map(x=>`${esc(x.tag)}: ${x.count}`).join(' | ')||'هنوز بازخوردی ثبت نشده است')}
async function loadEvaluationCases(){const rows=await api('/admin/api/evaluation-cases');$('evaluationRows').innerHTML=rows.map(x=>`<tr><td>${x.case_id}</td><td>${x.run_id}</td><td>${x.item_id}</td><td>${esc(x.expected_person_name)}</td><td>${esc(x.expected_topic_name)}</td><td>${esc((x.expected_summary||'').slice(0,260))}</td><td>${esc(x.notes||'')}</td><td>${fdate(x.updated_at||x.created_at)}</td></tr>`).join('')}
loadSetup();loadRuns();
</script>
</body></html>'''
