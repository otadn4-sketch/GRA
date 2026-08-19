from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import re
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import qrcode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .bale import BaleAPIError, BaleClient
from .bot_queue_recovery import BotQueueRecoveryService
from .config import Settings, load_settings
from .db import Database
from .bulletins import BulletinService
from .scheduler import BulletinScheduler
from .editorial_automation import EditorialAutomationService
from . import __version__
from .dashboard import create_dashboard_router
from .security import MiniAppAuthError, validate_init_data

settings: Settings = load_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("prasad")
log_dir = settings.database_path.parent / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(
    log_dir / "prasad.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_file_handler)
# جلوگیری از ثبت URL کامل API و افشای توکن در لاگ‌های httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

db = Database(settings.database_path)
bale = BaleClient(settings.token)
bulletin_service = BulletinService(db, settings)
bulletin_scheduler = BulletinScheduler(db, bulletin_service, settings)
editorial_automation = EditorialAutomationService(db, bulletin_service)
polling_task: asyncio.Task | None = None
admin_cache: dict[int, tuple[float, set[int]]] = {}
# Polling and a manual Bot API recovery must never call getUpdates at the same
# time.  The recovery holds this lock until it has safely acknowledged its
# bounded queue window; the normal polling loop uses the same lock.
bot_update_ingestion_lock = asyncio.Lock()


class MiniAppMessageRequest(BaseModel):
    init_data: str
    message_id: int


class MiniAppSourceRequest(MiniAppMessageRequest):
    url: str


class MiniAppOrationRequest(MiniAppMessageRequest):
    text: str


async def source_chat_matches(chat: dict[str, Any]) -> bool:
    if not chat:
        return False
    resolved = await db.resolve_monitored_chat(chat)
    if resolved:
        return True
    if await db.monitored_chat_configured(chat):
        return False
    # سازگاری فقط پیش از seed شدن منبع پیش‌فرض.
    return (chat.get("username") or "").lstrip("@").lower() == settings.source_channel_username.lower()


def message_text(value: dict[str, Any]) -> str:
    return str(value.get("text") or value.get("caption") or "")


def google_search_url(text: str) -> str:
    # متن بدون کوتاه‌سازی و دقیقاً به عنوان پارامتر q به گوگل فرستاده می‌شود.
    return "https://www.google.com/search?q=" + quote_plus(text, safe="")


def _short_button_text(text: str, limit: int = 42) -> str:
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _is_exact_public_bale_post_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        parsed.scheme == "https"
        and parsed.netloc.lower() in {"ble.ir", "www.ble.ir"}
        and len(parts) == 3
        and re.fullmatch(r"[A-Za-z0-9_]{3,}", parts[0])
        and parts[1].isdigit()
        and int(parts[1]) > 0
        and re.fullmatch(r"\d{13}", parts[2])
    )


def _sender_display(record: dict[str, Any]) -> tuple[str, str, str, Any]:
    """Return a backward-compatible label plus the sender's explicit kind."""
    sender_kind = str(record.get("sender_kind") or "").strip().lower()
    is_chat_sender = sender_kind == "chat" or record.get("sender_chat_id") is not None
    if is_chat_sender:
        username = str(record.get("sender_chat_username") or "").strip().lstrip("@")
        sender_id = record.get("sender_chat_id")
        label = (
            str(record.get("sender_chat_title") or "").strip()
            or (f"@{username}" if username else "")
            or (str(sender_id) if sender_id is not None else "")
            or str(record.get("sender_name") or "").strip()
            or "نامشخص"
        )
        return "chat", label, username, sender_id

    username = str(record.get("sender_username") or "").strip().lstrip("@")
    sender_id = record.get("sender_id")
    label = (
        str(record.get("sender_name") or "").strip()
        or (f"@{username}" if username else "")
        or "نامشخص"
    )
    return sender_kind or ("user" if sender_id is not None else "unknown"), label, username, sender_id


def _traceability_rows(
    record: dict[str, Any] | None,
    message_db_id: int,
) -> list[list[dict[str, Any]]]:
    """Buttons shared by source, reviewed and destination copies."""
    item = record or {}
    is_forwarded = bool(item.get("is_forwarded"))

    origin_title = str(item.get("forwarded_origin_title") or "").strip()
    origin_username = str(item.get("forwarded_origin_username") or "").strip().lstrip("@")
    source_title = str(item.get("source_chat_title") or "").strip()
    source_username = str(item.get("source_chat_username") or "").strip().lstrip("@")

    shown_origin = (
        origin_title
        or (f"@{origin_username}" if origin_username else "")
        or source_title
        or (f"@{source_username}" if source_username else "")
        or "نامشخص"
    )
    channel_username = origin_username or source_username
    channel_url = (
        f"https://ble.ir/{channel_username}"
        if channel_username
        else str(item.get("forwarded_origin_url") or item.get("message_url") or "").strip()
    )
    origin_prefix = "بازنشر از" if is_forwarded else "مبدأ پیام"
    origin_button: dict[str, Any] = {
        "text": f"📍 {origin_prefix}: {_short_button_text(shown_origin, 34)}"
    }
    if channel_url:
        origin_button["url"] = channel_url
    else:
        origin_button["callback_data"] = f"origin:{message_db_id}"

    public_message_url = str(
        (
            item.get("forwarded_origin_url")
            if is_forwarded
            else item.get("message_url")
        )
        or item.get("message_url")
        or ""
    ).strip()
    news_button: dict[str, Any] = {
        "text": (
            "🌐 پیام در کانال اصلی"
            if _is_exact_public_bale_post_url(public_message_url)
            else "⚠️ پیوند دقیق پیام در دسترس نیست"
        )
    }
    if _is_exact_public_bale_post_url(public_message_url):
        news_button["url"] = public_message_url
    else:
        news_button["callback_data"] = f"newslink:{message_db_id}"

    rows: list[list[dict[str, Any]]] = [[origin_button], [news_button]]

    if str(item.get("source_chat_type") or "") in {"group", "supergroup"}:
        _, sender_label, _, _ = _sender_display(item)
        rows.append([{
            "text": f"👤 ارسال‌کننده/کارشناس: {_short_button_text(sender_label, 29)}",
            "callback_data": f"sender:{message_db_id}",
        }])

    return rows


def _input_action_button(
    kind: str,
    message_db_id: int,
    *,
    has_value: bool,
) -> dict[str, Any]:
    if kind == "source":
        text = "🔗 منبع ثبت شد / ویرایش" if has_value else "🔗 لینک / منبع"
        route = "source"
        callback = "src"
    elif kind == "oration":
        text = "📝 خطابه ثبت شد / ویرایش" if has_value else "📝 افزودن خطابه"
        route = "oration"
        callback = "ora"
    else:
        raise ValueError("Unknown input action")

    if settings.input_mode == "miniapp" and settings.miniapp_base_url:
        return {
            "text": text,
            "web_app": {"url": f"{settings.miniapp_base_url}/miniapp/{route}?m={message_db_id}"},
        }
    return {"text": text, "callback_data": f"{callback}:{message_db_id}"}


def control_keyboard(
    message_db_id: int,
    search_text: str,
    *,
    has_source: bool = False,
    has_oration: bool = False,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if search_text:
        search_button: dict[str, Any] = {
            "text": "🔎 سرچ در گوگل",
            "url": google_search_url(search_text),
        }
    else:
        search_button = {
            "text": "🔎 پیام بدون متن",
            "callback_data": f"nosearch:{message_db_id}",
        }

    rows = [
        [
            search_button,
            _input_action_button("source", message_db_id, has_value=has_source),
        ],
        [_input_action_button("oration", message_db_id, has_value=has_oration)],
    ]
    rows.extend(_traceability_rows(record, message_db_id))
    return {"inline_keyboard": rows}

def destination_keyboard(record: dict[str, Any]) -> dict[str, Any]:
    message_db_id = int(record["id"])
    rows = _traceability_rows(record, message_db_id)
    if record.get("primary_source_url"):
        rows.append([{
            "text": "🔗 لینک منبع تکمیلی",
            "url": str(record["primary_source_url"]),
        }])

    if record.get("oration_text"):
        rows.append([{
            "text": f"📝 خطابه: {_short_button_text(str(record['oration_text']), 36)}",
            "callback_data": f"showora:{message_db_id}",
        }])

    return {"inline_keyboard": rows}


def source_prompt_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "لغو ثبت منبع", "callback_data": "cancel_input"}],
        ]
    }


def oration_prompt_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "لغو ثبت خطابه", "callback_data": "cancel_input"}],
        ]
    }


def channel_prompt_keyboard(request_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "لغو این درخواست", "callback_data": f"cancelreq:{request_id}"}],
        ]
    }


def actor_display_name(actor: dict[str, Any]) -> str:
    first = str(actor.get("first_name") or "").strip()
    last = str(actor.get("last_name") or "").strip()
    full = " ".join(part for part in (first, last) if part)
    return full or str(actor.get("username") or actor.get("id") or "نامشخص")


def create_qr_code(message_db_id: int, url: str) -> tuple[str, str]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    qr_dir: Path = settings.database_path.parent / "qrcodes"
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"message_{message_db_id}_{digest[:16]}.png"

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    image.save(qr_path)
    return str(qr_path.resolve()), digest


def clean_source_url(raw_text: str) -> str | None:
    text = raw_text.strip()
    if not text:
        return None

    # کاربر می‌تواند فقط لینک یا متنی حاوی لینک بفرستد.
    match = re.search(r"https?://[^\s<>\"']+", text, flags=re.IGNORECASE)
    if match:
        candidate = match.group(0)
    elif text.lower().startswith("www.") and " " not in text:
        candidate = "https://" + text
    else:
        candidate = text

    candidate = candidate.rstrip(".,،؛;!؟?)]}>")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if len(candidate) > 2048:
        return None
    return candidate


async def get_target_chat_id() -> int | None:
    if settings.target_chat_id is not None:
        return settings.target_chat_id
    saved = await db.get_setting("target_chat_id")
    return int(saved) if saved else None


async def register_target_if_match(message: dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    if chat.get("type") in {"group", "supergroup"} and chat.get("title") == settings.target_chat_title:
        await db.set_setting("target_chat_id", str(chat["id"]))
        logger.info("Target group registered: %s (%s)", chat.get("title"), chat.get("id"))


async def reviewer_allowed(user_id: int, source_chat_id: int) -> bool:
    if user_id in settings.reviewer_user_ids:
        return True
    if await db.reviewer_is_active(user_id):
        return True

    cache_time, ids = admin_cache.get(source_chat_id, (0.0, set()))
    if monotonic() - cache_time > 60:
        try:
            admins = await bale.get_chat_administrators(source_chat_id)
            ids = {
                int(item["user"]["id"])
                for item in admins
                if item.get("user") and item.get("status") in {"creator", "administrator"}
            }
            admin_cache[source_chat_id] = (monotonic(), ids)
        except Exception:
            logger.exception("Could not refresh administrators for chat %s", source_chat_id)
    return user_id in ids


def _largest_photo_file_id(message: dict[str, Any]) -> str | None:
    photos = message.get("photo") or []
    if not isinstance(photos, list) or not photos:
        return None
    candidates = [item for item in photos if isinstance(item, dict) and item.get("file_id")]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)))
    return str(best["file_id"])


def _single_file_id(message: dict[str, Any], field: str) -> str | None:
    value = message.get(field)
    if isinstance(value, dict) and value.get("file_id"):
        return str(value["file_id"])
    return None


async def send_clone_to_chat(
    message: dict[str, Any],
    chat_id: int,
    reply_markup: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """پیام را با متد send* و صفحه‌کلید در همان درخواست ایجاد می‌کند."""
    caption = message.get("caption") or None

    if message.get("text") is not None:
        sent = await bale.send_message(chat_id, str(message.get("text") or ""), reply_markup=reply_markup)
        return sent, "sendMessage_with_keyboard"

    photo_id = _largest_photo_file_id(message)
    if photo_id:
        sent = await bale.send_photo(chat_id, photo_id, caption=caption, reply_markup=reply_markup)
        return sent, "sendPhoto_with_keyboard"

    for field, method in (
        ("animation", bale.send_animation),
        ("audio", bale.send_audio),
        ("document", bale.send_document),
        ("video", bale.send_video),
        ("voice", bale.send_voice),
    ):
        file_id = _single_file_id(message, field)
        if file_id:
            sent = await method(chat_id, file_id, caption=caption, reply_markup=reply_markup)
            return sent, f"send{field.title()}_with_keyboard"

    location = message.get("location")
    if isinstance(location, dict) and location.get("latitude") is not None and location.get("longitude") is not None:
        sent = await bale.send_location(
            chat_id,
            float(location["latitude"]),
            float(location["longitude"]),
            reply_markup=reply_markup,
        )
        return sent, "sendLocation_with_keyboard"

    contact = message.get("contact")
    if isinstance(contact, dict) and contact.get("phone_number") and contact.get("first_name"):
        sent = await bale.send_contact(
            chat_id,
            str(contact["phone_number"]),
            str(contact["first_name"]),
            last_name=str(contact["last_name"]) if contact.get("last_name") else None,
            reply_markup=reply_markup,
        )
        return sent, "sendContact_with_keyboard"

    raise RuntimeError("نوع پیام برای ارسال مستقیم با دکمه پشتیبانی نمی‌شود.")


async def send_clone_with_keyboard(
    message: dict[str, Any], message_db_id: int
) -> tuple[dict[str, Any], str]:
    chat_id = int(message["chat"]["id"])
    record = await db.get_message(message_db_id)
    markup = control_keyboard(message_db_id, message_text(message), record=record)
    return await send_clone_to_chat(message, chat_id, markup)


async def replace_with_bot_message(message: dict[str, Any], message_db_id: int) -> bool:
    """
    پیام را با دکمه در همان درخواست API بازنشر می‌کند و تنها پس از موفقیت، پیام
    اولیه را حذف می‌کند. این عملیات دیگر به editMessageReplyMarkup وابسته نیست.
    """
    chat_id = int(message["chat"]["id"])
    original_message_id = int(message["message_id"])
    sent: dict[str, Any] | None = None

    try:
        sent, mode = await send_clone_with_keyboard(message, message_db_id)
        managed_message_id = int(sent["message_id"])

        # ابتدا نگاشت را ثبت می‌کنیم تا آپدیت پیام بازو دوباره پردازش نشود.
        await db.set_managed_message(
            message_db_id,
            chat_id,
            original_message_id,
            managed_message_id,
            mode,
        )

        try:
            await bale.delete_message(chat_id, original_message_id)
            await db.add_action(
                message_db_id,
                None,
                "original_message_deleted",
                details={"message_id": original_message_id},
            )
        except Exception as exc:
            logger.warning("Managed message created, but original could not be deleted: %s", exc)
            await db.add_action(
                message_db_id,
                None,
                "original_message_delete_failed",
                details={"message_id": original_message_id, "error": str(exc)},
            )
        return True

    except Exception as exc:
        logger.warning("Could not create bot-owned message with inline keyboard: %s", exc)
        if sent and sent.get("message_id"):
            try:
                await bale.delete_message(chat_id, int(sent["message_id"]))
            except Exception:
                logger.exception("Could not remove incomplete replacement message")
        await db.add_action(
            message_db_id,
            None,
            "message_replacement_failed",
            details={"error": str(exc)},
        )
        return False


async def attach_controls(message: dict[str, Any], message_db_id: int) -> None:
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    record = await db.get_message(message_db_id)
    markup = control_keyboard(message_db_id, message_text(message), record=record)

    if settings.replace_original_message and await replace_with_bot_message(message, message_db_id):
        return

    # ویرایش مستقیم پیام ورودی کانال در API بله عملاً 403 می‌دهد؛ برای چت‌های
    # غیرکانالی می‌توان آن را امتحان کرد.
    if (message.get("chat") or {}).get("type") != "channel":
        try:
            await bale.edit_reply_markup(chat_id, message_id, markup)
            await db.add_action(message_db_id, None, "keyboard_attached", details={"mode": "same_message"})
            return
        except Exception as exc:
            logger.warning("Could not attach keyboard to incoming message: %s", exc)

    # مسیر جایگزین برای نوع‌های پشتیبانی‌نشده یا خطاهای API.
    try:
        control = await bale.send_message(
            chat_id,
            f"⚙️ کنترل پیام #{message_db_id}",
            reply_markup=markup,
            reply_to_message_id=message_id,
        )
    except BaleAPIError as exc:
        if not exc.is_forbidden:
            raise
        # برخی پیام‌های کانال قابلیت reply_to ندارند؛ کارت بدون reply ارسال می‌شود.
        control = await bale.send_message(
            chat_id,
            f"⚙️ کنترل پیام #{message_db_id}",
            reply_markup=markup,
        )

    await db.set_control_message(message_db_id, chat_id, int(control["message_id"]))
    await db.add_action(message_db_id, None, "keyboard_attached", details={"mode": "control_card"})


async def handle_source_message(
    message: dict[str, Any],
    *,
    received_at: str | None = None,
) -> int | None:
    chat_id = int(message["chat"]["id"])
    source_message_id = int(message["message_id"])

    # پیام بازنشرشده توسط خود بازو دوباره به عنوان پیام تازه پردازش نشود.
    existing = await db.get_message_by_source(chat_id, source_message_id)
    if existing and existing.get("managed_mode"):
        return None
    if await db.is_control_message(chat_id, source_message_id):
        return None
    if await db.is_interaction_prompt(chat_id, source_message_id):
        return None
    system_prefixes = (
        "⚙️ کنترل پیام #",
        "🔗 درخواست ثبت منبع #",
        "📝 درخواست ثبت خطابه #",
        "⚠️ ورودی درخواست #",
        "⚠️ ساخت QR برای درخواست #",
        "⚠️ خطابه درخواست #",
    )
    if (message.get("text") or "").startswith(system_prefixes):
        return None

    try:
        message_db_id = await db.upsert_message(message, received_at=received_at)
    except Exception as exc:
        await db.record_monitored_chat_error(chat_id, exc)
        raise
    record = await db.get_message(message_db_id)
    if record and record["status"] == "pending" and not record["control_message_id"] and not record.get("managed_mode"):
        try:
            await attach_controls(message, message_db_id)
        except Exception as exc:
            # خطای ساخت دکمه نباید صف getUpdates را برای همیشه متوقف و پیام را تکثیر کند.
            logger.exception("Controls could not be attached to message %s", message_db_id)
            await db.add_action(
                message_db_id,
                None,
                "controls_attach_failed",
                details={"error": str(exc)},
            )
    return message_db_id


def _raw_message_from_record(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("raw_message_json")
    if raw:
        try:
            value = json.loads(str(raw))
            if isinstance(value, dict):
                return value
        except (TypeError, json.JSONDecodeError):
            pass

    # مسیر پشتیبان برای رکوردهای قدیمی که JSON خام ندارند.
    message: dict[str, Any] = {
        "chat": {"id": int(record["source_chat_id"])},
        "message_id": int(record["source_message_id"]),
    }
    if record.get("text") is not None:
        message["text"] = str(record.get("text") or "")
    elif record.get("caption") is not None:
        message["caption"] = str(record.get("caption") or "")
    return message


async def _edit_source_keyboard(record: dict[str, Any], markup: dict[str, Any]) -> None:
    targets: list[tuple[int, int, str]] = []
    if record.get("managed_message_id"):
        targets.append((
            int(record["source_chat_id"]),
            int(record["managed_message_id"]),
            "managed",
        ))
    if record.get("control_chat_id") and record.get("control_message_id"):
        targets.append((
            int(record["control_chat_id"]),
            int(record["control_message_id"]),
            "control",
        ))
    if record.get("source_chat_id") and record.get("source_message_id"):
        targets.append((
            int(record["source_chat_id"]),
            int(record["source_message_id"]),
            "original",
        ))

    seen: set[tuple[int, int]] = set()
    errors: list[str] = []
    edited = 0
    for chat_id, message_id, target_kind in targets:
        key = (chat_id, message_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            await bale.edit_reply_markup(chat_id, message_id, markup)
            edited += 1
        except Exception as exc:
            errors.append(f"{target_kind}:{type(exc).__name__}")
    if edited == 0:
        logger.error(
            "Could not refresh message keyboard for record %s; attempted %s",
            record.get("id"),
            ", ".join(errors) or "no editable target",
        )
    elif errors:
        logger.warning(
            "Message keyboard refreshed on %s target(s) for record %s; failed targets: %s",
            edited,
            record.get("id"),
            ", ".join(errors),
        )


async def refresh_message_keyboards(message_db_id: int) -> None:
    record = await db.get_message(message_db_id)
    if not record:
        return

    source_markup = control_keyboard(
        message_db_id,
        message_text(record),
        has_source=bool(record.get("primary_source_url")),
        has_oration=bool(record.get("oration_text")),
        record=record,
    )
    await _edit_source_keyboard(record, source_markup)
async def _safe_delete_message(chat_id: int, message_id: int) -> None:
    try:
        await bale.delete_message(chat_id, message_id)
    except Exception:
        logger.debug("Could not delete temporary channel interaction message", exc_info=True)


async def _create_channel_interaction_prompt(
    *,
    callback_id: str,
    actor: dict[str, Any],
    record: dict[str, Any],
    kind: str,
) -> None:
    user_id = int(actor["id"])
    actor_name = actor_display_name(actor)
    request = await db.create_interaction_request(
        message_id=int(record["id"]),
        kind=kind,
        requested_by=user_id,
        requested_by_name=actor_name,
        source_chat_id=int(record["source_chat_id"]),
        target_message_id=int(record["source_message_id"]),
        timeout_seconds=settings.source_input_timeout,
    )
    request_id = int(request["id"])
    for old_prompt in request.get("cancelled_prompts", []):
        await _safe_delete_message(
            int(old_prompt["prompt_chat_id"]),
            int(old_prompt["prompt_message_id"]),
        )
    if kind == "source":
        prompt_text = (
            f"🔗 درخواست ثبت منبع #{request_id}\n"
            f"{actor_name}، لینک را فقط با Reply به همین پیام ارسال کنید.\n"
            "لینک باید با http:// یا https:// شروع شود. QR Code نیز خودکار ذخیره می‌شود."
        )
        action_type = "source_input_requested_in_channel"
        callback_text = "در کانال، لینک را در پاسخ به پیام راهنما بفرستید."
    else:
        prompt_text = (
            f"📝 درخواست ثبت خطابه #{request_id}\n"
            f"{actor_name}، متن خطابه را فقط با Reply به همین پیام ارسال کنید.\n"
            "حداکثر طول خطابه ۲۰۰۰ نویسه است."
        )
        action_type = "oration_input_requested_in_channel"
        callback_text = "در کانال، خطابه را در پاسخ به پیام راهنما بفرستید."

    try:
        try:
            prompt = await bale.send_message(
                int(record["source_chat_id"]),
                prompt_text,
                reply_markup=channel_prompt_keyboard(request_id),
                reply_to_message_id=int(record["source_message_id"]),
            )
        except BaleAPIError as exc:
            if not exc.is_forbidden:
                raise
            prompt = await bale.send_message(
                int(record["source_chat_id"]),
                prompt_text,
                reply_markup=channel_prompt_keyboard(request_id),
            )
        prompt_message_id = int(prompt["message_id"])
        await db.attach_interaction_prompt(
            request_id,
            int(record["source_chat_id"]),
            prompt_message_id,
        )
        await db.add_action(
            int(record["id"]),
            user_id,
            action_type,
            details={
                "request_id": request_id,
                "prompt_chat_id": int(record["source_chat_id"]),
                "prompt_message_id": prompt_message_id,
            },
        )
        await bale.answer_callback(callback_id, callback_text)
    except Exception as exc:
        await db.cancel_interaction(request_id, str(exc))
        logger.exception("Could not create channel interaction prompt")
        await bale.answer_callback(
            callback_id,
            "ساخت پیام راهنما در کانال ناموفق بود.",
            show_alert=True,
        )


async def handle_channel_input_reply(message: dict[str, Any]) -> bool:
    """ورودی لینک/خطابه را از Reply به پیام راهنما در خود کانال دریافت می‌کند."""
    chat = message.get("chat") or {}
    if not await source_chat_matches(chat):
        return False

    reply = message.get("reply_to_message") or {}
    prompt_message_id = reply.get("message_id")
    if prompt_message_id is None:
        return False

    chat_id = int(chat["id"])
    request = await db.get_interaction_by_prompt(chat_id, int(prompt_message_id))
    if not request:
        return await db.is_interaction_prompt(chat_id, int(prompt_message_id))

    actor = message.get("from") or {}
    actual_user_id = int(actor["id"]) if actor.get("id") is not None else None
    if actual_user_id is not None:
        await db.upsert_user(actor)
        if not await reviewer_allowed(actual_user_id, int(request["source_chat_id"])):
            await db.add_action(
                int(request["message_id"]),
                actual_user_id,
                "unauthorized_channel_input",
                details={"interaction_request_id": int(request["id"])},
            )
            return True

    # پیام‌های کانال ممکن است from نداشته باشند؛ در آن حالت صاحب درخواست
    # (فردی که دکمه را زده) به‌عنوان ثبت‌کننده نگهداری می‌شود.
    submitted_by = actual_user_id or int(request["requested_by"])
    submitted_name = (
        actor_display_name(actor)
        if actual_user_id is not None
        else str(request.get("requested_by_name") or request["requested_by"])
    )
    raw_input = str(message.get("text") or message.get("caption") or "").strip()
    response_message_id = int(message["message_id"])
    request_id = int(request["id"])
    message_db_id = int(request["message_id"])

    if request["kind"] == "source":
        url = clean_source_url(raw_input)
        if not url:
            await bale.send_message(
                chat_id,
                f"⚠️ ورودی درخواست #{request_id} لینک معتبر نیست. دوباره با Reply به همان پیام، لینک کامل را بفرستید.",
                reply_to_message_id=response_message_id,
            )
            return True
        try:
            qr_path, qr_sha256 = await asyncio.to_thread(create_qr_code, message_db_id, url)
        except Exception as exc:
            logger.exception("QR code generation failed")
            await bale.send_message(
                chat_id,
                f"⚠️ ساخت QR برای درخواست #{request_id} ناموفق بود؛ دوباره تلاش کنید.",
                reply_to_message_id=response_message_id,
            )
            await db.add_action(
                message_db_id,
                submitted_by,
                "qr_generation_failed",
                details={"request_id": request_id, "error": str(exc)},
            )
            return True

        completed, _ = await db.complete_source_interaction(
            request_id,
            url=url,
            qr_path=qr_path,
            qr_sha256=qr_sha256,
            submitted_by=submitted_by,
            submitted_by_name=submitted_name,
            response_chat_id=chat_id,
            response_message_id=response_message_id,
        )
    else:
        oration = "\n".join(line.rstrip() for line in raw_input.splitlines()).strip()
        if not oration:
            await bale.send_message(
                chat_id,
                f"⚠️ خطابه درخواست #{request_id} خالی است. دوباره با Reply به همان پیام متن را بفرستید.",
                reply_to_message_id=response_message_id,
            )
            return True
        if len(oration) > 2000:
            await bale.send_message(
                chat_id,
                f"⚠️ خطابه درخواست #{request_id} بیش از ۲۰۰۰ نویسه است.",
                reply_to_message_id=response_message_id,
            )
            return True
        completed, _ = await db.complete_oration_interaction(
            request_id,
            text=oration,
            submitted_by=submitted_by,
            submitted_by_name=submitted_name,
            response_chat_id=chat_id,
            response_message_id=response_message_id,
        )

    if not completed:
        if settings.delete_channel_input_messages:
            await _safe_delete_message(chat_id, response_message_id)
        return True

    await refresh_message_keyboards(message_db_id)
    if settings.delete_channel_input_messages:
        await _safe_delete_message(chat_id, int(prompt_message_id))
        await _safe_delete_message(chat_id, response_message_id)
    return True


async def source_request(callback_id: str, actor: dict[str, Any], message_db_id: int) -> None:
    record = await db.get_message(message_db_id)
    if not record:
        await bale.answer_callback(callback_id, "رکورد پیام پیدا نشد.", show_alert=True)
        return
    if actor.get("id") is None:
        await bale.answer_callback(callback_id, "هویت کاربر قابل تشخیص نیست.", show_alert=True)
        return

    user_id = int(actor["id"])
    await db.upsert_user(actor)
    if not await reviewer_allowed(user_id, int(record["source_chat_id"])):
        await db.add_action(message_db_id, user_id, "unauthorized_source_request")
        await bale.answer_callback(callback_id, "شما مجاز به ثبت منبع نیستید.", show_alert=True)
        return

    if settings.input_mode == "miniapp":
        await refresh_message_keyboards(message_db_id)
        await bale.answer_callback(
            callback_id,
            "دکمه به فرم جدید تبدیل شد؛ دوباره روی «لینک / منبع» بزنید.",
            show_alert=True,
        )
        return

    if settings.input_mode == "channel":
        await _create_channel_interaction_prompt(
            callback_id=callback_id,
            actor=actor,
            record=record,
            kind="source",
        )
        return

    await db.set_pending_source(user_id, message_db_id, settings.source_input_timeout)
    await db.add_action(message_db_id, user_id, "source_input_requested_private")
    try:
        await bale.send_message(
            user_id,
            (
                f"🔗 لینک منبع پیام #{message_db_id} را همین‌جا ارسال کنید.\n\n"
                "لینک باید با http:// یا https:// شروع شود. هم‌زمان QR Code آن ساخته و ذخیره می‌شود. "
                "برای انصراف /cancel را بفرستید."
            ),
            reply_markup=source_prompt_keyboard(),
        )
        await bale.answer_callback(callback_id, "گفتگوی خصوصی بازو را باز کنید و لینک را بفرستید.")
    except Exception:
        logger.exception("Could not send private source prompt")
        await bale.answer_callback(
            callback_id,
            "ابتدا گفتگوی خصوصی بازو را باز و Start کنید؛ سپس دوباره این دکمه را بزنید.",
            show_alert=True,
        )


async def oration_request(callback_id: str, actor: dict[str, Any], message_db_id: int) -> None:
    record = await db.get_message(message_db_id)
    if not record:
        await bale.answer_callback(callback_id, "رکورد پیام پیدا نشد.", show_alert=True)
        return
    if actor.get("id") is None:
        await bale.answer_callback(callback_id, "هویت کاربر قابل تشخیص نیست.", show_alert=True)
        return

    user_id = int(actor["id"])
    await db.upsert_user(actor)
    if not await reviewer_allowed(user_id, int(record["source_chat_id"])):
        await db.add_action(message_db_id, user_id, "unauthorized_oration_request")
        await bale.answer_callback(callback_id, "شما مجاز به ثبت خطابه نیستید.", show_alert=True)
        return

    if settings.input_mode == "miniapp":
        await refresh_message_keyboards(message_db_id)
        await bale.answer_callback(
            callback_id,
            "دکمه به فرم جدید تبدیل شد؛ دوباره روی «افزودن خطابه» بزنید.",
            show_alert=True,
        )
        return

    if settings.input_mode == "channel":
        await _create_channel_interaction_prompt(
            callback_id=callback_id,
            actor=actor,
            record=record,
            kind="oration",
        )
        return

    await db.set_pending_oration(user_id, message_db_id, settings.source_input_timeout)
    await db.add_action(message_db_id, user_id, "oration_input_requested_private")
    try:
        await bale.send_message(
            user_id,
            (
                f"📝 خطابه پیام #{message_db_id} را همین‌جا ارسال کنید.\n\n"
                "متن کامل در دیتابیس ذخیره می‌شود. برای انصراف /cancel را بفرستید."
            ),
            reply_markup=oration_prompt_keyboard(),
        )
        await bale.answer_callback(callback_id, "گفتگوی خصوصی بازو را باز کنید و خطابه را بفرستید.")
    except Exception:
        logger.exception("Could not send private oration prompt")
        await bale.answer_callback(
            callback_id,
            "ابتدا گفتگوی خصوصی بازو را باز و Start کنید؛ سپس دوباره این دکمه را بزنید.",
            show_alert=True,
        )


async def handle_callback(callback: dict[str, Any]) -> None:
    callback_id = str(callback["id"])
    actor = callback.get("from") or {}
    data = callback.get("data") or ""

    if data in {"cancel_input", "cancel_source", "cancel_oration"}:
        if actor.get("id") is not None:
            await db.clear_pending_inputs(int(actor["id"]))
        await bale.answer_callback(callback_id, "درخواست ثبت لغو شد.")
        return

    if data.startswith("cancelreq:"):
        try:
            request_id = int(data.split(":", 1)[1])
        except (TypeError, ValueError):
            await bale.answer_callback(callback_id, "درخواست نامعتبر است.", show_alert=True)
            return
        request = await db.get_interaction_request(request_id)
        if not request:
            await bale.answer_callback(callback_id, "درخواست پیدا نشد.", show_alert=True)
            return
        actor_id = int(actor["id"]) if actor.get("id") is not None else None
        allowed = actor_id == int(request["requested_by"]) if actor_id is not None else False
        if actor_id is not None and not allowed:
            allowed = await reviewer_allowed(actor_id, int(request["source_chat_id"]))
        if not allowed:
            await bale.answer_callback(callback_id, "اجازه لغو این درخواست را ندارید.", show_alert=True)
            return
        await db.cancel_interaction(request_id, "cancelled_by_user")
        if request.get("prompt_chat_id") and request.get("prompt_message_id"):
            await _safe_delete_message(int(request["prompt_chat_id"]), int(request["prompt_message_id"]))
        await bale.answer_callback(callback_id, "درخواست لغو شد.")
        return

    if data.startswith(("ok:", "no:")):
        await bale.answer_callback(callback_id, "چرخهٔ تأیید و رد از سامانه حذف شده است.")
        return

    if data.startswith("done:"):
        await bale.answer_callback(callback_id, "این پیام قبلاً تعیین تکلیف شده است.")
        return

    if data.startswith("nosearch:"):
        await bale.answer_callback(callback_id, "این پیام متن یا کپشن قابل جست‌وجو ندارد.", show_alert=True)
        return

    if data.startswith("origin:"):
        try:
            message_db_id = int(data.split(":", 1)[1])
        except (ValueError, TypeError):
            await bale.answer_callback(callback_id, "دستور نامعتبر است.", show_alert=True)
            return
        record = await db.get_message(message_db_id)
        if not record:
            await bale.answer_callback(callback_id, "اطلاعات منبع پیدا نشد.", show_alert=True)
            return
        title = record.get("forwarded_origin_title") or "نامشخص"
        username = record.get("forwarded_origin_username")
        msg_id = record.get("forwarded_origin_message_id")
        details = f"منبع فوروارد: {title}"
        if username:
            details += f"\n@{str(username).lstrip('@')}"
        if msg_id:
            details += f"\nشناسه پیام اصلی: {msg_id}"
        await bale.answer_callback(callback_id, details[:190], show_alert=True)
        return

    if data.startswith("newslink:"):
        try:
            message_db_id = int(data.split(":", 1)[1])
        except (ValueError, TypeError):
            await bale.answer_callback(callback_id, "دستور نامعتبر است.", show_alert=True)
            return
        record = await db.get_message(message_db_id)
        if not record:
            await bale.answer_callback(callback_id, "اطلاعات پیام پیدا نشد.", show_alert=True)
            return
        await bale.answer_callback(
            callback_id,
            "پیوند عمومیِ دقیق این پیام در دسترس نیست؛ کانال مبدأ باید عمومی، دارای نام کاربری و دارای شناسهٔ معتبر پیام باشد.",
            show_alert=True,
        )
        return

    if data.startswith("sender:"):
        try:
            message_db_id = int(data.split(":", 1)[1])
        except (ValueError, TypeError):
            await bale.answer_callback(callback_id, "دستور نامعتبر است.", show_alert=True)
            return
        record = await db.get_message(message_db_id)
        if not record:
            await bale.answer_callback(callback_id, "اطلاعات ارسال‌کننده پیدا نشد.", show_alert=True)
            return
        sender_kind, name, username, sender_id = _sender_display(record)
        details = f"ارسال‌کننده/کارشناس: {name}"
        if sender_kind == "chat":
            details += "\nنوع فرستنده: ارسال از طرف کانال/گروه یا مدیر ناشناس"
        if username:
            details += f"\n@{username}"
        if sender_id is not None:
            id_label = "شناسه کانال/گروه" if sender_kind == "chat" else "شناسه کاربر"
            details += f"\n{id_label}: {sender_id}"
        await bale.answer_callback(callback_id, details[:190], show_alert=True)
        return

    if data.startswith("showora:"):
        try:
            message_db_id = int(data.split(":", 1)[1])
        except (ValueError, TypeError):
            await bale.answer_callback(callback_id, "دستور نامعتبر است.", show_alert=True)
            return
        record = await db.get_message(message_db_id)
        text = str((record or {}).get("oration_text") or "خطابه‌ای ثبت نشده است.")
        await bale.answer_callback(callback_id, text[:190], show_alert=True)
        return

    if data.startswith("src:"):
        try:
            message_db_id = int(data.split(":", 1)[1])
        except (ValueError, TypeError):
            await bale.answer_callback(callback_id, "دستور نامعتبر است.", show_alert=True)
            return
        await source_request(callback_id, actor, message_db_id)
        return

    if data.startswith("ora:"):
        try:
            message_db_id = int(data.split(":", 1)[1])
        except (ValueError, TypeError):
            await bale.answer_callback(callback_id, "دستور نامعتبر است.", show_alert=True)
            return
        await oration_request(callback_id, actor, message_db_id)
        return

    await bale.answer_callback(callback_id, "دستور پیام منقضی یا نامعتبر است.")


async def handle_private_message(message: dict[str, Any]) -> bool:
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return False

    actor = message.get("from") or {}
    if actor.get("id") is None:
        return True
    user_id = int(actor["id"])
    text = (message.get("text") or "").strip()
    pending_source = await db.get_pending_source(user_id)
    pending_oration = await db.get_pending_oration(user_id)

    if text.startswith("/cancel"):
        await db.clear_pending_inputs(user_id)
        await bale.send_message(user_id, "درخواست ثبت لغو شد.")
        return True

    if text.startswith("/start"):
        if pending_source:
            await bale.send_message(
                user_id,
                f"لینک منبع پیام #{pending_source['message_id']} را ارسال کنید. برای انصراف /cancel را بفرستید.",
                reply_markup=source_prompt_keyboard(),
            )
        elif pending_oration:
            await bale.send_message(
                user_id,
                f"خطابه پیام #{pending_oration['message_id']} را ارسال کنید. برای انصراف /cancel را بفرستید.",
                reply_markup=oration_prompt_keyboard(),
            )
        else:
            await bale.send_message(
                user_id,
                "بازوی پایش فعال است. برای ثبت لینک یا خطابه، دکمه مربوط را زیر پیام کانال بزنید.",
            )
        return True

    pending = pending_source or pending_oration
    if not pending:
        return True

    record = await db.get_message(int(pending["message_id"]))
    if not record:
        await db.clear_pending_inputs(user_id)
        await bale.send_message(user_id, "پیام مربوط به این درخواست پیدا نشد. دوباره از دکمه زیر پیام استفاده کنید.")
        return True

    if not await reviewer_allowed(user_id, int(record["source_chat_id"])):
        await db.clear_pending_inputs(user_id)
        await bale.send_message(user_id, "شما دیگر مجوز ثبت اطلاعات این پیام را ندارید.")
        return True

    if pending_source:
        url = clean_source_url(text)
        if not url:
            await bale.send_message(
                user_id,
                "لینک معتبر پیدا نشد. یک لینک با http:// یا https:// ارسال کنید؛ یا /cancel را بفرستید.",
            )
            return True

        try:
            qr_path, qr_sha256 = await asyncio.to_thread(create_qr_code, int(record["id"]), url)
        except Exception as exc:
            logger.exception("QR code generation failed")
            await bale.send_message(user_id, f"ساخت QR Code ناموفق بود؛ لینک ذخیره نشد: {type(exc).__name__}")
            return True

        await db.add_source(
            int(record["id"]),
            url,
            user_id,
            qr_path=qr_path,
            qr_sha256=qr_sha256,
        )
        await db.clear_pending_source(user_id)
        await refresh_message_keyboards(int(record["id"]))
        await bale.send_message(
            user_id,
            f"✅ لینک و QR Code ذخیره شدند.\n\nلینک: {url}\nمسیر QR: {qr_path}",
        )
        return True

    oration = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not oration:
        await bale.send_message(user_id, "خطابه خالی است. متن را ارسال کنید یا /cancel را بفرستید.")
        return True
    if len(oration) > 2000:
        await bale.send_message(user_id, "خطابه بیش از ۲۰۰۰ نویسه است؛ لطفاً کوتاه‌تر ارسال کنید.")
        return True

    await db.add_oration(int(record["id"]), oration, user_id)
    await db.clear_pending_oration(user_id)
    await refresh_message_keyboards(int(record["id"]))
    await bale.send_message(user_id, "✅ خطابه ذخیره و به دکمه‌های پیام مقصد اضافه شد.")
    return True


async def handle_message(message: dict[str, Any]) -> None:
    await db.upsert_chat(message.get("chat"))
    await db.upsert_user(message.get("from"))
    await register_target_if_match(message)

    if await handle_private_message(message):
        return

    chat = message.get("chat") or {}
    if await source_chat_matches(chat) and await handle_channel_input_reply(message):
        return

    text = (message.get("text") or "").strip()
    actor = message.get("from") or {}
    actor_id = int(actor["id"]) if actor.get("id") is not None else None

    if text.startswith("/set_target") and chat.get("type") in {"group", "supergroup"}:
        if actor_id is None or not await reviewer_allowed(actor_id, int(chat["id"])):
            await bale.send_message(int(chat["id"]), "⛔ فقط مدیر گروه می‌تواند مقصد را ثبت کند.")
            return
        await db.set_setting("target_chat_id", str(chat["id"]))
        await db.set_setting("target_chat_title", str(chat.get("title") or ""))
        await bale.send_message(int(chat["id"]), "✅ این گروه به‌عنوان مقصد پیش‌فرض ثبت شد.")
        return

    if text.startswith("/register_source"):
        if actor_id is None or not await reviewer_allowed(actor_id, int(chat["id"])):
            await bale.send_message(int(chat["id"]), "⛔ فقط مدیر این گفتگو می‌تواند آن را به پایش اضافه کند.")
            return
        default_target = await get_target_chat_id()
        source = await db.register_monitored_chat(
            chat,
            target_chat_id=default_target,
            target_title=settings.target_chat_title,
            added_by=actor_id,
        )
        await db.add_system_event(
            "bot", "INFO", "source_registered", "Monitored chat registered", source
        )
        await bale.send_message(
            int(chat["id"]),
            "✅ این کانال/گروه به فهرست پایش اضافه شد. پیام‌های بعدی در جریان اخبار ثبت می‌شوند.",
        )
        return

    if text.startswith("/unregister_source"):
        source = await db.resolve_monitored_chat(chat)
        if not source:
            await bale.send_message(int(chat["id"]), "این گفتگو در فهرست پایش نیست.")
            return
        if actor_id is None or not await reviewer_allowed(actor_id, int(chat["id"])):
            await bale.send_message(int(chat["id"]), "⛔ فقط مدیر این گفتگو می‌تواند پایش را غیرفعال کند.")
            return
        await db.update_monitored_chat(int(source["id"]), enabled=False)
        await bale.send_message(int(chat["id"]), "⏸ پایش این گفتگو غیرفعال شد.")
        return

    if await source_chat_matches(chat):
        await handle_source_message(message)


async def process_update(update: dict[str, Any]) -> None:
    update_id = int(update["update_id"])
    should_process = await db.store_update(update)
    if not should_process:
        return
    try:
        callback = update.get("callback_query")
        if callback:
            await handle_callback(callback)
        else:
            message = update.get("message") or update.get("channel_post")
            if message:
                await handle_message(message)
            else:
                edited = update.get("edited_message") or update.get("edited_channel_post")
                if edited and await source_chat_matches(edited.get("chat") or {}):
                    existing = await db.get_message_by_source(
                        int(edited["chat"]["id"]), int(edited["message_id"])
                    )
                    if not (existing and existing.get("managed_mode")):
                        await db.upsert_message(edited)
        await db.mark_update_processed(update_id)
    except Exception as exc:
        await db.mark_update_failed(update_id, str(exc))
        raise


async def recover_source_message_from_bot_queue(
    message: dict[str, Any],
    published_at: str,
    _run_id: int,
) -> int | None:
    """Use the regular source path, but preserve its original Bale timestamp."""
    return await handle_source_message(message, received_at=published_at)


async def run_bot_queue_recovery(run_id: int, hours: int) -> None:
    """Recover only Bot API evidence; no crawler/browser is involved."""
    service = BotQueueRecoveryService(
        db,
        bale,
        process_regular_update=process_update,
        source_matches=source_chat_matches,
        process_source_message=recover_source_message_from_bot_queue,
        ingestion_lock=bot_update_ingestion_lock,
    )
    try:
        await service.run(run_id=run_id, hours=hours)
    except asyncio.CancelledError:
        await db.finish_crawler_recovery_run(
            run_id,
            status="cancelled",
            scanned=0,
            existing=0,
            imported=0,
            failed=0,
            details={"mode": "bot_api_queue_and_local_update_archive", "hours": hours},
            error_text="بازسازی با توقف سامانه لغو شد.",
        )
        raise
    except Exception:
        # The service has already written a detailed, user-visible failure
        # record.  Keeping the exception out of FastAPI's task log avoids a
        # duplicate opaque traceback while preserving the regular app log.
        logger.exception("Bot API queue recovery failed for run %s", run_id)


async def polling_loop() -> None:
    raw_offset = await db.get_setting("polling_offset")
    offset = int(raw_offset) if raw_offset else None
    logger.info("Polling started; offset=%s", offset)
    while True:
        try:
            async with bot_update_ingestion_lock:
                updates = await bale.get_updates(offset=offset, timeout=25)
                for update in updates:
                    update_id = int(update["update_id"])
                    try:
                        await process_update(update)
                    except Exception:
                        attempts = await db.get_update_attempts(update_id)
                        if attempts < settings.max_update_attempts:
                            logger.exception(
                                "Failed to process update %s (attempt %s/%s); it will be retried",
                                update_id,
                                attempts,
                                settings.max_update_attempts,
                            )
                            # با نگه‌داشتن offset، همین آپدیت در دور بعد دوباره دریافت می‌شود.
                            break

                        # آپدیت سمی نباید کل صف را برای همیشه متوقف کند. JSON خام و
                        # last_error در دیتابیس باقی می‌مانند تا بعداً قابل بررسی باشند.
                        logger.exception(
                            "Failed to process update %s after %s attempts; skipping it to keep polling alive",
                            update_id,
                            attempts,
                            settings.max_update_attempts,
                        )
                        offset = update_id + 1
                        await db.set_setting("polling_offset", str(offset))
                        continue

                    offset = update_id + 1
                    await db.set_setting("polling_offset", str(offset))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Polling failure")
            await asyncio.sleep(3)


async def cleanup_legacy_channel_interactions() -> None:
    """پیام‌های موقت نسخه channel را هنگام مهاجرت به MiniApp حذف می‌کند."""
    if settings.input_mode != "miniapp" or not settings.cleanup_legacy_channel_inputs:
        return
    artifacts = await db.collect_interaction_artifacts_for_cleanup()
    seen: set[tuple[int, int]] = set()
    for item in artifacts:
        for chat_key, message_key in (
            ("prompt_chat_id", "prompt_message_id"),
            ("response_chat_id", "response_message_id"),
        ):
            if item.get(chat_key) is None or item.get(message_key) is None:
                continue
            pair = (int(item[chat_key]), int(item[message_key]))
            if pair in seen:
                continue
            seen.add(pair)
            await _safe_delete_message(*pair)
    if seen:
        logger.info("Legacy channel interaction cleanup attempted for %s messages", len(seen))


async def reconcile_message_keyboards() -> None:
    """دکمه‌های کانال و گروه مقصد را از روی آخرین داده SQLite بازسازی می‌کند."""
    records = await db.list_review_messages_for_reconciliation()
    source_refreshed = 0
    destination_refreshed = 0
    for record in records:
        try:
            markup = control_keyboard(
                int(record["id"]),
                message_text(record),
                has_source=bool(record.get("primary_source_url")),
                has_oration=bool(record.get("oration_text")),
                record=record,
            )
            await _edit_source_keyboard(record, markup)
            source_refreshed += 1
        except Exception:
            logger.warning(
                "Could not reconcile source keyboard for message %s",
                record.get("id"),
                exc_info=True,
            )

    if records:
        logger.info(
            "Keyboard reconciliation: source=%s/%s destination=%s",
            source_refreshed,
            len(records),
            destination_refreshed,
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global polling_task
    await db.init()
    await db.ensure_bootstrap_admin(
        username=settings.admin_username,
        password=settings.admin_password,
    )
    logger.info("SQLite database ready: %s", settings.database_path)
    default_target = await get_target_chat_id()
    if settings.source_channel_username:
        await db.register_monitored_username(
            settings.source_channel_username,
            title=settings.source_channel_username,
            target_chat_id=default_target,
            target_title=settings.target_chat_title,
        )
    if settings.input_mode == "miniapp" and not settings.miniapp_base_url:
        logger.warning("INPUT_MODE=miniapp است اما MINIAPP_BASE_URL تنظیم نشده؛ فرم‌های لینک و خطابه باز نخواهند شد.")
    if settings.dashboard_enabled and not settings.admin_password:
        logger.warning("Dashboard enabled but ADMIN_PASSWORD is empty; /admin will return 503")
    if settings.bot_mode != "disabled":
        await cleanup_legacy_channel_interactions()
        await reconcile_message_keyboards()
    await bulletin_scheduler.start()
    await editorial_automation.start()
    recovered_runs = await db.recover_incomplete_bulletin_runs()
    for run_id in recovered_runs:
        asyncio.create_task(
            bulletin_service.execute_run(run_id),
            name=f"recovered-bulletin-{run_id}",
        )
    if recovered_runs:
        logger.info("Recovered %s queued bulletin run(s)", len(recovered_runs))
    if settings.bot_mode == "polling":
        polling_task = asyncio.create_task(polling_loop(), name="bale-polling")
    await db.add_system_event("app", "INFO", "startup", f"Garaye Newsletter v{__version__} started")
    yield
    await editorial_automation.shutdown()
    await bulletin_scheduler.shutdown()
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    await bulletin_service.close()
    await bale.close()


app = FastAPI(title="سامانه خبرنامه گرایه", version=__version__, lifespan=lifespan)
app.include_router(
    create_dashboard_router(
        db,
        settings,
        bulletin_service,
        bulletin_scheduler,
        editorial_automation,
        get_target_chat_id,
        run_bot_queue_recovery,
        bale,
    )
)


@app.get("/s/{code}", include_in_schema=False)
async def editorial_short_link(code: str) -> RedirectResponse:
    """Public redirect used by the QR shown in the editorial desk."""
    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", code):
        raise HTTPException(404, "پیوند کوتاه پیدا نشد.")
    target = await db.short_link_target(code)
    if not target:
        raise HTTPException(404, "پیوند کوتاه پیدا نشد.")
    return RedirectResponse(target, status_code=307)


@app.middleware("http")
async def miniapp_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/miniapp"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https://tapi.bale.ai; "
            "script-src 'self' 'unsafe-inline' https://tapi.bale.ai; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "frame-ancestors https://*.bale.ai; frame-src https://*.bale.ai"
        )
    return response


@app.get("/health")
async def health() -> dict[str, Any]:
    database = await db.health_summary()
    sources = await db.list_monitored_chats(enabled_only=True)
    ai_profiles = bulletin_service.ai_status().get("profiles", {})
    return {
        "ok": not database["missing_tables"] and database["quick_check"].lower() == "ok",
        "mode": settings.bot_mode,
        "input_mode": settings.input_mode,
        "miniapp_ready": bool(settings.miniapp_base_url) if settings.input_mode == "miniapp" else None,
        "target_chat_id": await get_target_chat_id(),
        "replace_original_message": settings.replace_original_message,
        "active_sources": len(sources),
        "dashboard_enabled": settings.dashboard_enabled,
        "ai_ready": bool(settings.ai_api_keys and settings.ai_model and settings.ai_provider != "disabled"),
        "ai_provider": settings.ai_provider,
        "ai_model": settings.ai_model or None,
        "ai_key_count": len(settings.ai_api_keys),
        "ai_profiles": ai_profiles,
        "ai_max_concurrency": settings.ai_max_concurrency,
        "scheduler_enabled": settings.scheduler_enabled,
        "crawler": {
            "enabled": settings.crawler_enabled,
            "channels_file": str(settings.crawler_channels_path),
            "firefox_profile_configured": bool(settings.crawler_firefox_profile),
            "destination_configured": bool(settings.crawler_destination_chat_id),
            "repeat_seconds": settings.crawler_repeat_seconds,
        },
        "database": database,
        "version": __version__,
    }


@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request) -> dict[str, bool]:
    if settings.bot_mode != "webhook" or not settings.webhook_secret or secret != settings.webhook_secret:
        raise HTTPException(status_code=404)
    update = await request.json()
    await process_update(update)
    return {"ok": True}


# مسیرهای /miniapp/api علاوه بر مسیرهای قدیمی ارائه می‌شوند تا MiniApp پشت
# reverse proxy و حتی زیر یک sub-path نیز درخواست را به همان upstream بفرستد.
async def miniapp_identity(init_data: str, message_id: int):
    try:
        identity = validate_init_data(
            init_data,
            settings.token,
            max_age=settings.miniapp_auth_max_age,
        )
    except MiniAppAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    record = await db.get_message(message_id)
    if not record:
        raise HTTPException(status_code=404, detail="پیام پیدا نشد.")
    if not await reviewer_allowed(identity.user_id, int(record["source_chat_id"])):
        raise HTTPException(status_code=403, detail="دسترسی بررسی ندارید.")
    return identity, record


@app.post("/api/miniapp/message")
@app.post("/miniapp/api/message")
async def miniapp_message(payload: MiniAppMessageRequest) -> dict[str, Any]:
    identity, record = await miniapp_identity(payload.init_data, payload.message_id)
    text = message_text(record)
    await db.add_action(payload.message_id, identity.user_id, "miniapp_search_opened")
    return {
        "message_id": payload.message_id,
        "text": text,
        "status": record["status"],
        "primary_source_url": record.get("primary_source_url") or "",
        "oration_text": record.get("oration_text") or "",
    }


@app.post("/api/miniapp/source")
@app.post("/miniapp/api/source")
async def miniapp_source(payload: MiniAppSourceRequest) -> dict[str, Any]:
    identity, record = await miniapp_identity(payload.init_data, payload.message_id)
    clean_url = clean_source_url(payload.url)
    if not clean_url:
        raise HTTPException(status_code=422, detail="لینک باید با http:// یا https:// آغاز شود.")
    try:
        qr_path, qr_sha256 = await asyncio.to_thread(create_qr_code, int(record["id"]), clean_url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ساخت QR Code ناموفق بود: {type(exc).__name__}") from exc
    await db.add_source(
        int(record["id"]),
        clean_url,
        identity.user_id,
        qr_path=qr_path,
        qr_sha256=qr_sha256,
    )
    await refresh_message_keyboards(int(record["id"]))
    return {"ok": True, "message": "منبع اصلی و QR Code ذخیره شدند و دکمه‌های گروه مقصد به‌روزرسانی شدند."}


@app.post("/api/miniapp/oration")
@app.post("/miniapp/api/oration")
async def miniapp_oration(payload: MiniAppOrationRequest) -> dict[str, Any]:
    identity, record = await miniapp_identity(payload.init_data, payload.message_id)
    oration = "\n".join(line.rstrip() for line in payload.text.splitlines()).strip()
    if not oration:
        raise HTTPException(status_code=422, detail="خطابه نمی‌تواند خالی باشد.")
    if len(oration) > 2000:
        raise HTTPException(status_code=422, detail="خطابه باید حداکثر ۲۰۰۰ نویسه باشد.")
    await db.add_oration(int(record["id"]), oration, identity.user_id)
    await refresh_message_keyboards(int(record["id"]))
    return {"ok": True, "message": "خطابه ذخیره شد و دکمه‌های گروه مقصد به‌روزرسانی شدند."}


@app.get("/miniapp/api/health")
async def miniapp_health() -> dict[str, Any]:
    return {
        "ok": True,
        "input_mode": settings.input_mode,
        "miniapp_ready": bool(settings.miniapp_base_url),
        "version": __version__,
    }


def miniapp_shell(title: str, body: str, script: str) -> str:
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>{html.escape(title)}</title>
  <script src="https://tapi.bale.ai/miniapp.js?3"></script>
  <style>
    @font-face {{ font-family:IRZarWeb; src:url("/auth/assets/fonts/IRZar.ttf") format("truetype"); font-display:swap; }}
    :root {{ color-scheme:light; --bg:#fff; --text:#002223; --card:rgba(0,128,128,.045); --line:rgba(0,78,79,.20); --button:#008080; --button-hover:#006767; --muted:#004e4f; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:20px; font-family:IRZarWeb,IRZar,"B Zar",Tahoma,Arial,sans-serif; background:#fff; color:var(--text); }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px; margin-bottom:14px; }}
    h1 {{ font-size:19px; margin:0 0 14px; }}
    p {{ line-height:1.8; white-space:pre-wrap; overflow-wrap:anywhere; }}
    input, textarea {{ width:100%; padding:13px; border:1px solid var(--line); border-radius:12px; font-size:16px; background:#fff; color:inherit; outline:none; }}
    input:focus, textarea:focus {{ border-color:#008080; box-shadow:0 0 0 3px rgba(0,128,128,.10); }}
    input {{ direction:ltr; }}
    textarea {{ min-height:180px; resize:vertical; line-height:1.8; direction:rtl; }}
    button {{ width:100%; border:1px solid var(--line); border-radius:12px; padding:13px; margin-top:10px; font-size:16px; cursor:pointer; background:#fff; color:#003738; }}
    button.primary {{ border-color:var(--button); background:var(--button); color:#fff; }}
    button.primary:hover {{ background:var(--button-hover); }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .error {{ color:#00010d; }}
    .success {{ color:#006767; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  {body}
  <script>
    const WA = window.Bale && window.Bale.WebApp;
    if (WA) {{
      WA.ready(); WA.expand();
    }}
    const qs = new URLSearchParams(location.search);
    const messageId = Number(qs.get('m'));
    const initData = WA ? WA.initData : '';
    async function postJSON(url, data) {{
      const r = await fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}});
      const raw = await r.text();
      let j;
      try {{
        j = raw ? JSON.parse(raw) : {{}};
      }} catch (_) {{
        const preview = raw.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 120);
        throw new Error(`پاسخ غیر JSON از سرور (HTTP ${{r.status}})${{preview ? ': ' + preview : ''}}`);
      }}
      if (!r.ok) throw new Error(j.detail || 'خطا');
      return j;
    }}
    {script}
  </script>
</body>
</html>"""


@app.get("/miniapp/search", response_class=HTMLResponse)
async def search_page() -> HTMLResponse:
    body = """
<div class="card"><p id="message">در حال دریافت متن پیام…</p></div>
<button class="primary" id="search" disabled>جست‌وجوی متن در گوگل</button>
<button id="close">بستن</button>
<p class="muted">نتیجه در مرورگر داخلی بله باز می‌شود.</p>
<p id="state" class="error"></p>
"""
    script = """
let queryText = '';
(async () => {
  try {
    const data = await postJSON('./api/message', {init_data:initData, message_id:messageId});
    queryText = data.text || '';
    document.getElementById('message').textContent = queryText || 'این پیام متن یا کپشن قابل جست‌وجو ندارد.';
    document.getElementById('search').disabled = !queryText;
  } catch (e) { document.getElementById('state').textContent = e.message; }
})();
document.getElementById('search').onclick = () => {
  const url = 'https://www.google.com/search?q=' + encodeURIComponent(queryText);
  if (WA) WA.openLink(url, {try_instant_view:true}); else location.href = url;
};
document.getElementById('close').onclick = () => { if (WA) WA.close(); else history.back(); };
"""
    return HTMLResponse(miniapp_shell("جست‌وجوی پیام", body, script))


@app.get("/miniapp/source", response_class=HTMLResponse)
async def source_page() -> HTMLResponse:
    body = """
<div class="card"><p>لینک منبع اصلی پیام را وارد کنید. هیچ پیامی در کانال ایجاد نمی‌شود.</p></div>
<input id="url" type="url" placeholder="https://example.com/original-post" autocomplete="url">
<button class="primary" id="save">ذخیره منبع</button>
<button id="close">انصراف</button>
<p id="state"></p>
"""
    script = """
(async () => {
  try {
    const data = await postJSON('./api/message', {init_data:initData, message_id:messageId});
    document.getElementById('url').value = data.primary_source_url || '';
  } catch (e) { const state=document.getElementById('state'); state.className='error'; state.textContent=e.message; }
})();
document.getElementById('save').onclick = async () => {
  const state = document.getElementById('state');
  state.className = '';
  try {
    const url = document.getElementById('url').value.trim();
    const data = await postJSON('./api/source', {init_data:initData, message_id:messageId, url});
    state.className = 'success'; state.textContent = data.message;
    setTimeout(() => { if (WA) WA.close(); }, 900);
  } catch (e) { state.className = 'error'; state.textContent = e.message; }
};
document.getElementById('close').onclick = () => { if (WA) WA.close(); else history.back(); };
"""
    return HTMLResponse(miniapp_shell("ثبت لینک / منبع", body, script))


@app.get("/miniapp/oration", response_class=HTMLResponse)
async def oration_page() -> HTMLResponse:
    body = """
<div class="card"><p>متن خطابه را وارد کنید. متن کامل در دیتابیس ذخیره و دکمه آن زیر پیام گروه مقصد درج می‌شود.</p></div>
<textarea id="oration" maxlength="2000" placeholder="متن خطابه…"></textarea>
<p class="muted"><span id="count">۰</span> از ۲۰۰۰ نویسه</p>
<button class="primary" id="save">ذخیره خطابه</button>
<button id="close">انصراف</button>
<p id="state"></p>
"""
    script = """
const box = document.getElementById('oration');
const count = document.getElementById('count');
const updateCount = () => { count.textContent = String(box.value.length); };
box.addEventListener('input', updateCount);
(async () => {
  try {
    const data = await postJSON('./api/message', {init_data:initData, message_id:messageId});
    box.value = data.oration_text || ''; updateCount();
  } catch (e) { const state=document.getElementById('state'); state.className='error'; state.textContent=e.message; }
})();
document.getElementById('save').onclick = async () => {
  const state = document.getElementById('state');
  state.className = '';
  try {
    const text = box.value.trim();
    const data = await postJSON('./api/oration', {init_data:initData, message_id:messageId, text});
    state.className = 'success'; state.textContent = data.message;
    setTimeout(() => { if (WA) WA.close(); }, 900);
  } catch (e) { state.className = 'error'; state.textContent = e.message; }
};
document.getElementById('close').onclick = () => { if (WA) WA.close(); else history.back(); };
"""
    return HTMLResponse(miniapp_shell("ثبت خطابه", body, script))
