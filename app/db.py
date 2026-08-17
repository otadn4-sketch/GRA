from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiosqlite

from .auth import (
    ROLE_TITLES,
    VALID_ROLES,
    AdminPrincipal,
    hash_password,
    new_session_token,
    token_digest,
    verify_password,
)
from .persian_text import canonical_key, normalize_persian
from .wordcloud import WORD_CLOUD_WEIGHTS, content_words, ranked_word_cloud


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def comparable_utc_iso(value: str | None, *, end_of_instant: bool = False) -> str | None:
    """Normalize a stored/API timestamp so SQLite text comparison is reliable."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if end_of_instant:
            parsed = parsed.replace(microsecond=999999)
        return parsed.isoformat(timespec="microseconds")
    except (TypeError, ValueError):
        return raw


def utc_second_key(value: str | None, *, end_of_instant: bool = False) -> str | None:
    """YYYY-MM-DDTHH:MM:SS prefix that sorts correctly for mixed ISO text."""
    normalized = comparable_utc_iso(value, end_of_instant=end_of_instant)
    if not normalized:
        return None
    return normalized.replace(" ", "T").replace("Z", "+")[:19]


def _sql_utc_second(expr: str) -> str:
    return f"substr(replace(replace(COALESCE({expr}, ''), ' ', 'T'), 'Z', '+'), 1, 19)"


def _append_message_window(
    where: list[str],
    params: list[Any],
    date_from: str | None,
    date_to: str | None,
    *,
    alias: str = "m",
) -> None:
    """Keep every message that was published or received inside the window.

    The stream previously compared only ``COALESCE(published_at, received_at)``
    and capped the page at 150 rows, so a busy Jalali day silently dropped
    items that had arrived that day with an older channel timestamp.
    Bounds are compared on the UTC second so stored values with or without
    microseconds still match the selected Jalali day.
    """
    start = utc_second_key(date_from, end_of_instant=False)
    end = utc_second_key(date_to, end_of_instant=True)
    if not start and not end:
        return
    published = _sql_utc_second(
        f"COALESCE({alias}.published_at,{alias}.received_at,{alias}.created_at)"
    )
    received = _sql_utc_second(f"COALESCE({alias}.received_at,{alias}.created_at)")
    if start and end:
        where.append(
            f"(({published}>=? AND {published}<=?) OR ({received}>=? AND {received}<=?))"
        )
        params.extend([start, end, start, end])
        return
    if start:
        where.append(f"({published}>=? OR {received}>=?)")
        params.extend([start, start])
        return
    where.append(f"({published}<=? OR {received}<=?)")
    params.extend([end, end])


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class EditorialDraftConflictError(RuntimeError):
    """Raised when a client saves an editorial draft opened at an old version."""

    def __init__(self, *, expected_version: int, current_version: int) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            "این پیش‌نویس هم‌زمان توسط کاربر دیگری تغییر کرده است؛ "
            "صفحه را تازه کنید تا نسخهٔ جدید را ببینید."
        )


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _tehran_flow_fields(value: Any) -> dict[str, str | None]:
    """Expose a stable local timestamp for the editorial flow UI.

    Stored timestamps remain UTC.  These derived values let clients render a
    message's date and minute without guessing the server timezone.
    """
    raw = str(value or "").strip()
    if not raw:
        return {
            "flow_timestamp": None,
            "flow_datetime": None,
            "flow_date": None,
            "flow_time": None,
            "flow_timezone": "Asia/Tehran",
        }
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(ZoneInfo("Asia/Tehran"))
    except (TypeError, ValueError):
        return {
            "flow_timestamp": raw,
            "flow_datetime": raw,
            "flow_date": None,
            "flow_time": None,
            "flow_timezone": "Asia/Tehran",
        }
    return {
        "flow_timestamp": parsed.astimezone(timezone.utc).isoformat(),
        "flow_datetime": local.isoformat(),
        "flow_date": local.date().isoformat(),
        "flow_time": local.strftime("%H:%M"),
        "flow_timezone": "Asia/Tehran",
    }


def _attach_flow_timestamp(item: dict[str, Any], *fields: str) -> dict[str, Any]:
    """Add Tehran date/time fields using the first populated source field."""
    value = next((item.get(field) for field in fields if item.get(field)), None)
    item.update(_tehran_flow_fields(value))
    return item


def _automatic_editorial_rating(item: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Return the 1..5 score mandated for editorial monitoring.

    A message always starts with a reception-time score in Tehran time: 3 from
    08:00 to 22:59, 2 from 23:00 to 23:59, and 1 from 00:00 to 07:59.  A
    completed first-engine analysis and inclusion in a finalized news item add
    one point each, with a hard ceiling of five.
    """
    raw_timestamp = (
        item.get("published_at")
        or item.get("received_at")
        or item.get("created_at")
    )
    time_score = 1
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        hour = parsed.astimezone(ZoneInfo("Asia/Tehran")).hour
        time_score = 3 if 8 <= hour < 23 else (2 if hour == 23 else 1)
    except (TypeError, ValueError):
        pass
    analysis_score = 1 if bool(item.get("is_analyzed")) else 0
    finalization_score = 1 if bool(item.get("is_finalized")) else 0
    score = min(5, time_score + analysis_score + finalization_score)
    return score, {
        "time": time_score,
        "analysis": analysis_score,
        "finalization": finalization_score,
    }


def _epoch_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except ValueError:
            pass
    try:
        numeric = float(value)
        if numeric >= 100_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _epoch_millis(value: Any) -> str | None:
    """Normalize Bale's seconds/milliseconds/ISO dates for public post permalinks."""
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
        if numeric <= 0:
            return None
        milliseconds = numeric * 1000 if numeric < 100_000_000_000 else numeric
        return str(int(milliseconds))
    except (TypeError, ValueError, OverflowError):
        pass
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return str(int(parsed.timestamp() * 1000))
        except ValueError:
            return None
    return None


def _bale_public_message_url(
    username: Any,
    chat_id: Any,
    date_or_uid: Any,
) -> str | None:
    """Build Bale's exact no-login public post URL; never return a channel-only guess."""
    clean_username = str(username or "").strip().lstrip("@")
    if not clean_username:
        return None
    timestamp = _epoch_millis(date_or_uid)
    clean_chat_id = str(chat_id or "").strip()
    if not clean_chat_id.isdigit() or int(clean_chat_id) <= 0:
        return None
    if not timestamp or len(timestamp) != 13:
        return None
    return f"https://ble.ir/{clean_username}/{clean_chat_id}/{timestamp}"


def _is_exact_bale_public_message_url(value: Any) -> bool:
    """Recognize only Bale's public post preview, not a channel or logged-in web URL."""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"ble.ir", "www.ble.ir"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        len(parts) == 3
        and re.fullmatch(r"[A-Za-z0-9_]{3,}", parts[0])
        and parts[1].isdigit()
        and int(parts[1]) > 0
        and re.fullmatch(r"\d{13}", parts[2])
    )


def _display_name(user: dict[str, Any] | None) -> str | None:
    if not user:
        return None
    full = " ".join(
        str(user.get(key) or "").strip()
        for key in ("first_name", "last_name")
    ).strip()
    return (
        full
        or str(user.get("display_name") or user.get("name") or "").strip()
        or (f"@{user['username']}" if user.get("username") else str(user.get("id") or ""))
        or None
    )


def _message_from_raw_payload(value: Any) -> dict[str, Any]:
    """Find the Bale message inside a stored update envelope.

    Old webhook and crawler versions saved slightly different envelopes.  The
    monitoring migration must understand all of them, otherwise an already
    known sender could disappear from the statistics simply because the old
    raw JSON had one extra wrapper around its ``message`` object.
    """
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("chat"), dict) or value.get("message_id") is not None:
        return value
    for key in ("message", "update", "payload", "data", "result"):
        nested = value.get(key)
        if isinstance(nested, dict):
            found = _message_from_raw_payload(nested)
            if found:
                return found
    return value


def _sender_identity(message: dict[str, Any]) -> dict[str, Any]:
    """Return the real Bale sender without treating its anonymous placeholder as a user."""
    message = _message_from_raw_payload(message)
    raw_user = next(
        (
            message.get(key)
            for key in ("from", "sender", "from_user", "sender_user", "author", "user")
            if isinstance(message.get(key), dict)
        ),
        None,
    )
    user = raw_user if isinstance(raw_user, dict) else {}
    raw_sender_chat = next(
        (
            message.get(key)
            for key in ("sender_chat", "from_chat", "author_chat")
            if isinstance(message.get(key), dict)
        ),
        None,
    )
    sender_chat = raw_sender_chat if isinstance(raw_sender_chat, dict) else {}
    chat_id = sender_chat.get("id")
    chat_username = (
        str(sender_chat.get("username") or "").strip().lstrip("@") or None
    )
    chat_title = str(sender_chat.get("title") or "").strip() or None
    is_chat_sender = chat_id is not None or chat_username is not None or chat_title is not None

    if is_chat_sender:
        # Bale/Telegram-compatible payloads may also contain a synthetic ``from``
        # user for anonymous admins. It must not be persisted as the expert.
        chat_label = (
            chat_title
            or (f"@{chat_username}" if chat_username else None)
            or (str(chat_id) if chat_id is not None else None)
        )
        return {
            "kind": "chat",
            "user": None,
            "chat": sender_chat,
            "sender_id": None,
            "sender_username": None,
            # Keep the legacy dashboard contract useful while the explicit chat
            # fields let new clients distinguish a person from a chat.
            "sender_name": chat_label,
            "sender_chat_id": chat_id,
            "sender_chat_username": chat_username,
            "sender_chat_title": chat_title,
        }

    has_user = any(
        user.get(key) not in (None, "")
        for key in ("id", "username", "first_name", "last_name")
    )
    return {
        "kind": "user" if has_user else "unknown",
        "user": user if has_user else None,
        "chat": None,
        "sender_id": user.get("id") if has_user else None,
        "sender_username": user.get("username") if has_user else None,
        "sender_name": _display_name(user) if has_user else None,
        "sender_chat_id": None,
        "sender_chat_username": None,
        "sender_chat_title": None,
    }


def _sender_profile_key(record: dict[str, Any]) -> str:
    """Return the stable sender key used to link a message to an account profile."""

    sender_kind = str(record.get("sender_kind") or "").strip().lower()
    is_chat_sender = sender_kind == "chat" or any(
        record.get(field) not in (None, "")
        for field in ("sender_chat_id", "sender_chat_username", "sender_chat_title")
    )
    if is_chat_sender:
        identity = (
            record.get("sender_chat_id")
            or record.get("source_chat_id")
            or record.get("sender_chat_username")
            or record.get("source_chat_username")
            or "unknown"
        )
        return f"chat:{identity}"
    identity = (
        record.get("sender_id")
        or record.get("sender_username")
        or record.get("sender_name")
        or record.get("source_chat_id")
        or record.get("id")
        or "unknown"
    )
    return f"person:{identity}"


def _media_payload(message: dict[str, Any]) -> tuple[str, str, int]:
    media: list[dict[str, Any]] = []
    message_type = "text"
    for kind in ("photo", "video", "document", "audio", "voice", "animation", "sticker"):
        value = message.get(kind)
        if not value:
            continue
        message_type = kind
        if kind == "photo" and isinstance(value, list):
            media.append({"type": kind, "items": value})
        else:
            media.append({"type": kind, "item": value})
    if message.get("location"):
        message_type = "location"
        media.append({"type": "location", "item": message["location"]})
    if message.get("contact"):
        message_type = "contact"
        media.append({"type": "contact", "item": message["contact"]})
    if message.get("poll"):
        message_type = "poll"
        media.append({"type": "poll", "item": message["poll"]})
    return message_type, dumps(media), len(media)


SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=10000;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_reviewer INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    sender_key TEXT,
    avatar_updated_at TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(user_id) REFERENCES admin_users(user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_admin_sessions_token
ON admin_sessions(token_hash,expires_at);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    title TEXT,
    chat_type TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitored_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    username TEXT,
    username_norm TEXT,
    title TEXT,
    chat_type TEXT NOT NULL DEFAULT 'channel',
    source_kind TEXT NOT NULL DEFAULT 'monitored_channel',
    target_chat_id INTEGER,
    target_title TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_received_at TEXT,
    last_success_at TEXT,
    last_error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_monitored_chat_id
ON monitored_chats(chat_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_monitored_username
ON monitored_chats(username_norm);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    source_chat_username TEXT,
    source_chat_title TEXT,
    source_chat_type TEXT,
    source_kind TEXT NOT NULL DEFAULT 'monitored_channel',
    sender_id INTEGER,
    sender_username TEXT,
    sender_name TEXT,
    sender_kind TEXT NOT NULL DEFAULT 'unknown',
    sender_chat_id INTEGER,
    sender_chat_username TEXT,
    sender_chat_title TEXT,
    text TEXT,
    caption TEXT,
    normalized_text TEXT,
    message_type TEXT NOT NULL DEFAULT 'text',
    media_json TEXT NOT NULL DEFAULT '[]',
    media_count INTEGER NOT NULL DEFAULT 0,
    link_count INTEGER NOT NULL DEFAULT 0,
    message_url TEXT,
    published_at TEXT,
    received_at TEXT NOT NULL,
    edited_at TEXT,
    is_forwarded INTEGER NOT NULL DEFAULT 0,
    forwarded_origin_chat_id INTEGER,
    forwarded_origin_username TEXT,
    forwarded_origin_title TEXT,
    forwarded_origin_url TEXT,
    forwarded_origin_message_id INTEGER,
    forwarded_origin_sender_name TEXT,
    forwarded_origin_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    editorial_rating_half INTEGER,
    approved_by INTEGER,
    approved_by_name TEXT,
    approved_by_username TEXT,
    rejected_by INTEGER,
    rejected_by_name TEXT,
    rejected_by_username TEXT,
    reviewed_at TEXT,
    primary_source_url TEXT,
    primary_source_qr_path TEXT,
    primary_source_qr_sha256 TEXT,
    oration_text TEXT,
    oration_by INTEGER,
    oration_at TEXT,
    target_chat_id INTEGER,
    target_message_id INTEGER,
    delivered_at TEXT,
    control_chat_id INTEGER,
    control_message_id INTEGER,
    managed_original_message_id INTEGER,
    managed_message_id INTEGER,
    managed_mode TEXT,
    raw_message_json TEXT,
    text_sha256 TEXT,
    detected_person_name TEXT,
    detected_person_id INTEGER,
    person_candidate_id INTEGER,
    person_match_method TEXT,
    person_confidence REAL,
    detected_topic_id INTEGER,
    detected_topic_name TEXT,
    topic_confidence REAL,
    statement_type TEXT,
    statement_location_type TEXT,
    statement_location_label TEXT,
    relevance_status TEXT,
    relevance_reason TEXT,
    source_quality REAL,
    importance_score REAL,
    ai_enrichment_status TEXT,
    ai_enrichment_confidence REAL,
    ai_analysis_json TEXT,
    analysis_content_type TEXT,
    analysis_main_subject TEXT,
    analysis_general_topic TEXT,
    analysis_event_title TEXT,
    analysis_event_entities_json TEXT,
    analysis_event_location TEXT,
    analysis_event_time TEXT,
    duplicate_of INTEGER,
    processing_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_chat_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_status ON messages(status, received_at DESC);
CREATE INDEX IF NOT EXISTS ix_messages_source ON messages(source_chat_id, source_message_id);
CREATE INDEX IF NOT EXISTS ix_messages_person ON messages(detected_person_id, detected_person_name);
CREATE INDEX IF NOT EXISTS ix_messages_topic ON messages(detected_topic_id);

CREATE TABLE IF NOT EXISTS message_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    actor_id INTEGER,
    action_type TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS message_speaker_tags (
    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    speaker_name TEXT NOT NULL,
    canonical_speaker TEXT NOT NULL,
    position TEXT,
    specific_topic TEXT NOT NULL,
    general_topic TEXT NOT NULL,
    evidence TEXT,
    expression_method_type TEXT,
    expression_method_context TEXT,
    expression_method_evidence TEXT,
    person_id INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    analyzed_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(message_id, sort_order),
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY(person_id) REFERENCES people(person_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_message_speaker_tags_speaker
ON message_speaker_tags(canonical_speaker, message_id);
CREATE INDEX IF NOT EXISTS ix_message_speaker_tags_topics
ON message_speaker_tags(general_topic, specific_topic);

CREATE TABLE IF NOT EXISTS bot_updates (
    update_id INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    attempts INTEGER NOT NULL DEFAULT 1,
    error_text TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS interaction_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    requested_by INTEGER NOT NULL,
    requested_by_name TEXT,
    source_chat_id INTEGER NOT NULL,
    target_message_id INTEGER NOT NULL,
    prompt_chat_id INTEGER,
    prompt_message_id INTEGER,
    response_chat_id INTEGER,
    response_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    result_text TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pending_inputs (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, kind),
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL
);

-- گزارش بازیابی کنترل‌شدهٔ پیام‌های جاافتادهٔ ربات. خودِ محتوا همچنان در
-- messages ذخیره می‌شود؛ این جدول فقط برای نمایش نتیجه و امکان پیگیری است.
CREATE TABLE IF NOT EXISTS crawler_recovery_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hours INTEGER NOT NULL DEFAULT 48,
    status TEXT NOT NULL DEFAULT 'queued',
    scanned_count INTEGER NOT NULL DEFAULT 0,
    existing_count INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT,
    requested_by TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_crawler_recovery_runs_created
ON crawler_recovery_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    ip_address TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    person_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    category TEXT,
    position TEXT,
    organization TEXT,
    previous_positions TEXT,
    registry_status TEXT NOT NULL DEFAULT 'inside',
    active INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    merged_into INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(merged_into) REFERENCES people(person_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_people_canonical_active
ON people(canonical_name) WHERE merged_into IS NULL;

CREATE TABLE IF NOT EXISTS person_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    alias_text TEXT NOT NULL,
    canonical_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(person_id, canonical_alias),
    FOREIGN KEY(person_id) REFERENCES people(person_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_person_alias_canonical ON person_aliases(canonical_alias);

CREATE TABLE IF NOT EXISTS person_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    chat_id INTEGER,
    username TEXT,
    username_norm TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(person_id) REFERENCES people(person_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS person_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    old_json TEXT,
    new_json TEXT,
    actor TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(person_id) REFERENCES people(person_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS person_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    candidate_kind TEXT NOT NULL DEFAULT 'speaker',
    detected_position TEXT,
    suggested_name TEXT,
    web_query TEXT,
    web_evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    sample_message_id INTEGER,
    sample_text TEXT,
    sample_caption TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    merged_person_id INTEGER,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_candidate_links (
    candidate_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    tag_id INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY(candidate_id,message_id,tag_id),
    FOREIGN KEY(candidate_id) REFERENCES person_candidates(candidate_id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES message_speaker_tags(tag_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_person_candidate_links_message
ON person_candidate_links(message_id,tag_id);

CREATE TABLE IF NOT EXISTS editorial_automation_state (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
    initial_analysis_enabled INTEGER NOT NULL DEFAULT 0,
    analysis_start_at TEXT,
    analysis_status TEXT NOT NULL DEFAULT 'stopped',
    analysis_last_started_at TEXT,
    analysis_last_completed_at TEXT,
    analysis_last_error TEXT,
    analysis_last_processed INTEGER NOT NULL DEFAULT 0,
    drafts_enabled INTEGER NOT NULL DEFAULT 0,
    draft_start_at TEXT,
    draft_status TEXT NOT NULL DEFAULT 'idle',
    draft_last_started_at TEXT,
    draft_last_completed_at TEXT,
    draft_last_error TEXT,
    draft_last_processed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    canonical_keyword TEXT NOT NULL,
    UNIQUE(topic_id, canonical_keyword),
    FOREIGN KEY(topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulletin_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    user_prompt_template TEXT NOT NULL,
    output_format TEXT NOT NULL DEFAULT 'json_schema',
    version INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bulletin_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_by TEXT,
    template_id INTEGER,
    date_from TEXT,
    date_to TEXT,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    statuses_json TEXT NOT NULL DEFAULT '["approved"]',
    filters_json TEXT NOT NULL DEFAULT '{}',
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT,
    failed_stage TEXT,
    issue_number INTEGER,
    report_mode TEXT NOT NULL DEFAULT 'concise',
    input_message_count INTEGER NOT NULL DEFAULT 0,
    processed_message_count INTEGER NOT NULL DEFAULT 0,
    skipped_message_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    output_text TEXT,
    output_html TEXT,
    raw_response_json TEXT,
    usage_json TEXT,
    processing_report_json TEXT,
    error_text TEXT,
    error_type TEXT,
    error_traceback TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES bulletin_templates(id)
);
CREATE INDEX IF NOT EXISTS ix_bulletin_runs_status ON bulletin_runs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS bulletin_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    person_id INTEGER,
    person_candidate_id INTEGER,
    person_name TEXT NOT NULL,
    position TEXT,
    category TEXT,
    registry_bucket TEXT NOT NULL DEFAULT 'outside',
    topic_id INTEGER,
    topic_name TEXT NOT NULL,
    main_subject TEXT,
    statement_type TEXT,
    statement_location_type TEXT,
    statement_location_label TEXT,
    summary TEXT NOT NULL,
    summary_detailed TEXT,
    edited_summary TEXT,
    detail TEXT,
    editorial_category TEXT,
    editorial_source_url TEXT,
    editorial_qr_code_path TEXT,
    summary_method TEXT,
    summary_version TEXT,
    status TEXT NOT NULL DEFAULT 'review_pending',
    confidence REAL NOT NULL DEFAULT 0,
    confidence_breakdown_json TEXT,
    consensus_method TEXT,
    selected_sentences_json TEXT,
    pipeline_versions_json TEXT,
    importance_score REAL NOT NULL DEFAULT 0.5,
    include_in_main INTEGER NOT NULL DEFAULT 1,
    include_in_appendix INTEGER NOT NULL DEFAULT 1,
    editorial_order INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    issue_tags_json TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES bulletin_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(person_id) REFERENCES people(person_id),
    FOREIGN KEY(topic_id) REFERENCES topics(topic_id)
);
CREATE INDEX IF NOT EXISTS ix_bulletin_items_run ON bulletin_items(run_id, status, editorial_order);

CREATE TABLE IF NOT EXISTS bulletin_item_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'evidence',
    sentence_index INTEGER,
    score REAL,
    created_at TEXT NOT NULL,
    UNIQUE(item_id, message_id),
    FOREIGN KEY(item_id) REFERENCES bulletin_items(item_id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulletin_run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    message_id INTEGER,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT,
    details_json TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(run_id) REFERENCES bulletin_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processing_errors (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    message_id INTEGER,
    stage TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    error_traceback TEXT,
    payload_snapshot TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES bulletin_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulletin_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    registry_bucket TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, format, registry_bucket),
    FOREIGN KEY(run_id) REFERENCES bulletin_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulletin_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    timezone TEXT NOT NULL,
    template_id INTEGER,
    filters_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    item_id INTEGER,
    input_hash TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    request_kind TEXT,
    raw_request_json TEXT,
    raw_response_json TEXT,
    parsed_response_json TEXT,
    token_usage_json TEXT,
    latency_ms INTEGER,
    status TEXT,
    error_text TEXT,
    key_slot INTEGER,
    attempt_number INTEGER,
    endpoint TEXT,
    http_status INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_cache
ON ai_requests(input_hash, provider, model, prompt_version, request_kind, status);

CREATE TABLE IF NOT EXISTS reprocess_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    item_id INTEGER,
    mode TEXT NOT NULL,
    actor TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    details_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS bulletin_controversies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES bulletin_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS export_validation_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    code TEXT,
    severity TEXT,
    object_type TEXT,
    object_id TEXT,
    message TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES bulletin_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluation_cases (
    case_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    message_ids_json TEXT,
    expected_person_name TEXT,
    expected_topic_name TEXT,
    expected_statement_type TEXT,
    expected_summary TEXT,
    expected_claims_json TEXT,
    duplicate_label TEXT,
    registry_bucket TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS editorial_drafts (
    draft_id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type TEXT NOT NULL DEFAULT 'person_statement',
    title TEXT,
    person_id INTEGER,
    person_name TEXT,
    position TEXT,
    topic_id INTEGER,
    topic_name TEXT,
    main_subject TEXT,
    category_name TEXT,
    base_text TEXT NOT NULL DEFAULT '',
    summary_paragraph TEXT,
    summary_sentence TEXT,
    summary_title TEXT,
    detail TEXT,
    oration_location TEXT,
    source_url TEXT,
    short_url TEXT,
    qr_code_path TEXT,
    event_title TEXT,
    event_entities_json TEXT,
    event_location TEXT,
    event_time TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finalized_at TEXT
);

CREATE TABLE IF NOT EXISTS editorial_draft_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    selected_text TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(draft_id, message_id, sort_order),
    FOREIGN KEY(draft_id) REFERENCES editorial_drafts(draft_id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS editorial_message_decisions (
    message_id INTEGER PRIMARY KEY,
    decision TEXT NOT NULL CHECK(decision IN ('discarded')),
    reason TEXT,
    decided_by TEXT,
    decided_at TEXT NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_editorial_message_decisions_decision
ON editorial_message_decisions(decision,decided_at DESC);

CREATE TABLE IF NOT EXISTS editorial_draft_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    base_text TEXT NOT NULL,
    summary_paragraph TEXT,
    summary_sentence TEXT,
    summary_title TEXT,
    category_name TEXT,
    topic_name TEXT,
    main_subject TEXT,
    detail TEXT,
    change_reason TEXT,
    actor TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(draft_id, version_no),
    FOREIGN KEY(draft_id) REFERENCES editorial_drafts(draft_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS high_attention_runs (
    high_attention_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_day TEXT NOT NULL,
    selected_draft_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    api_key_slot INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finalized_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_high_attention_runs_day
ON high_attention_runs(source_day,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS high_attention_items (
    high_attention_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    high_attention_run_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_draft_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(high_attention_run_id) REFERENCES high_attention_runs(high_attention_run_id) ON DELETE CASCADE,
    UNIQUE(high_attention_run_id, sort_order)
);
CREATE INDEX IF NOT EXISTS ix_high_attention_items_run
ON high_attention_items(high_attention_run_id,sort_order);

CREATE TABLE IF NOT EXISTS short_links (
    code TEXT PRIMARY KEY,
    target_url TEXT NOT NULL,
    draft_id INTEGER UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(draft_id) REFERENCES editorial_drafts(draft_id) ON DELETE CASCADE
);
"""


DEFAULT_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("سیاست داخلی", ("دولت", "مجلس", "انتخابات", "قانون", "حکمرانی")),
    ("سیاست خارجی", ("دیپلماسی", "مذاکره", "تحریم", "سفارت", "وزارت خارجه")),
    ("اقتصاد", ("اقتصاد", "تورم", "بودجه", "بانک", "بازار", "تولید", "معیشت")),
    ("اجتماعی", ("جامعه", "آموزش", "سلامت", "خانواده", "آسیب اجتماعی")),
    ("فرهنگ و رسانه", ("فرهنگ", "رسانه", "کتاب", "سینما", "صداوسیما")),
    ("انرژی", ("نفت", "گاز", "برق", "انرژی", "پتروشیمی")),
    ("امنیت و دفاع", ("امنیت", "دفاع", "نظامی", "جنگ", "موشک", "سپاه", "ارتش")),
)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=10000")
        return conn

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        conn = await self._connect()
        try:
            cursor = await conn.execute(sql, params)
            await conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            await conn.close()

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        conn = await self._connect()
        try:
            cursor = await conn.execute(sql, params)
            return _row(await cursor.fetchone())
        finally:
            await conn.close()

    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        conn = await self._connect()
        try:
            cursor = await conn.execute(sql, params)
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await self._connect()
        try:
            await conn.executescript(SCHEMA)
            # A dashboard account may be linked to one Bale sender identity.
            # This is deliberately additive, so old operator accounts and all
            # incoming-message history remain intact after the upgrade.
            admin_user_columns = {
                str(row["name"])
                for row in await (await conn.execute("PRAGMA table_info(admin_users)")).fetchall()
            }
            if "sender_key" not in admin_user_columns:
                await conn.execute("ALTER TABLE admin_users ADD COLUMN sender_key TEXT")
                admin_user_columns.add("sender_key")
            if "avatar_updated_at" not in admin_user_columns:
                await conn.execute(
                    "ALTER TABLE admin_users ADD COLUMN avatar_updated_at TEXT"
                )
                admin_user_columns.add("avatar_updated_at")
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_admin_users_sender_key "
                "ON admin_users(sender_key) WHERE sender_key IS NOT NULL"
            )
            message_columns = {
                str(row["name"])
                for row in await (await conn.execute("PRAGMA table_info(messages)")).fetchall()
            }
            if "forwarded_origin_message_id" not in message_columns:
                await conn.execute(
                    "ALTER TABLE messages ADD COLUMN forwarded_origin_message_id INTEGER"
                )
                message_columns.add("forwarded_origin_message_id")
            sender_migrations = {
                "sender_kind": "TEXT NOT NULL DEFAULT 'unknown'",
                "sender_chat_id": "INTEGER",
                "sender_chat_username": "TEXT",
                "sender_chat_title": "TEXT",
                # Stored as 2..10 so the dashboard can support half stars
                # without any floating-point rounding ambiguity.
                "editorial_rating_half": "INTEGER",
            }
            for column, definition in sender_migrations.items():
                if column not in message_columns:
                    await conn.execute(
                        f"ALTER TABLE messages ADD COLUMN {column} {definition}"
                    )
                    message_columns.add(column)

            # Model-one analysis now separates direct statements from news
            # events.  All additions are nullable so old installations keep
            # their analysis history and become compatible at startup.
            for column, definition in {
                "analysis_content_type": "TEXT",
                "analysis_main_subject": "TEXT",
                "analysis_general_topic": "TEXT",
                "analysis_event_title": "TEXT",
                "analysis_event_entities_json": "TEXT",
                "analysis_event_location": "TEXT",
                "analysis_event_time": "TEXT",
            }.items():
                if column not in message_columns:
                    await conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
                    message_columns.add(column)

            speaker_tag_columns = {
                str(row["name"])
                for row in await (await conn.execute("PRAGMA table_info(message_speaker_tags)")).fetchall()
            }
            for column, definition in {
                "expression_method_type": "TEXT",
                "expression_method_context": "TEXT",
                "expression_method_evidence": "TEXT",
            }.items():
                if column not in speaker_tag_columns:
                    await conn.execute(
                        f"ALTER TABLE message_speaker_tags ADD COLUMN {column} {definition}"
                    )
                    speaker_tag_columns.add(column)

            # Identity research is deliberately retained as an auditable
            # human-review queue.  Older databases get these fields lazily at
            # startup, without touching prior candidate decisions.
            candidate_columns = {
                str(row["name"])
                for row in await (
                    await conn.execute("PRAGMA table_info(person_candidates)")
                ).fetchall()
            }
            for column, definition in {
                "candidate_kind": "TEXT NOT NULL DEFAULT 'speaker'",
                "detected_position": "TEXT",
                "suggested_name": "TEXT",
                "web_query": "TEXT",
                "web_evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if column not in candidate_columns:
                    await conn.execute(
                        f"ALTER TABLE person_candidates ADD COLUMN {column} {definition}"
                    )
                    candidate_columns.add(column)

            # Stage one and stage two are intentionally independent.  The
            # selected lower bound is stored in UTC, while the dashboard
            # accepts the operator's Jalali date and Tehran time.
            automation_columns = {
                str(row["name"])
                for row in await (
                    await conn.execute("PRAGMA table_info(editorial_automation_state)")
                ).fetchall()
            }
            for column, definition in {
                "analysis_start_at": "TEXT",
                "drafts_enabled": "INTEGER NOT NULL DEFAULT 0",
                "draft_start_at": "TEXT",
            }.items():
                if column not in automation_columns:
                    await conn.execute(
                        f"ALTER TABLE editorial_automation_state ADD COLUMN {column} {definition}"
                    )
                    automation_columns.add(column)
            await conn.execute(
                """
                INSERT OR IGNORE INTO editorial_automation_state(singleton_id,updated_at)
                VALUES (1,?)
                """,
                (utc_now(),),
            )

            # These additions are intentionally additive: installations that
            # already contain editorial history keep every existing draft.
            editorial_columns = {
                str(row["name"])
                for row in await (await conn.execute("PRAGMA table_info(editorial_drafts)")).fetchall()
            }
            for column, definition in {
                "category_name": "TEXT",
                "detail": "TEXT",
                "short_url": "TEXT",
                "qr_code_path": "TEXT",
                "content_type": "TEXT NOT NULL DEFAULT 'person_statement'",
                "event_title": "TEXT",
                "event_entities_json": "TEXT",
                "event_location": "TEXT",
                "event_time": "TEXT",
                "main_subject": "TEXT",
            }.items():
                if column not in editorial_columns:
                    await conn.execute(f"ALTER TABLE editorial_drafts ADD COLUMN {column} {definition}")
            version_columns = {
                str(row["name"])
                for row in await (await conn.execute("PRAGMA table_info(editorial_draft_versions)")).fetchall()
            }
            for column, definition in {
                "category_name": "TEXT",
                "topic_name": "TEXT",
                "main_subject": "TEXT",
                "detail": "TEXT",
            }.items():
                if column not in version_columns:
                    await conn.execute(f"ALTER TABLE editorial_draft_versions ADD COLUMN {column} {definition}")
            bulletin_item_columns = {
                str(row["name"])
                for row in await (await conn.execute("PRAGMA table_info(bulletin_items)")).fetchall()
            }
            for column, definition in {
                "detail": "TEXT",
                "main_subject": "TEXT",
                "editorial_category": "TEXT",
                "editorial_source_url": "TEXT",
                "editorial_qr_code_path": "TEXT",
            }.items():
                if column not in bulletin_item_columns:
                    await conn.execute(
                        f"ALTER TABLE bulletin_items ADD COLUMN {column} {definition}"
                    )

            # Upgrade old and partially migrated rows from their raw Bale
            # payload.  Earlier versions sometimes stored the sender name but
            # not the kind, and some webhook envelopes had the message one or
            # two levels below the saved JSON.  Repair both forms here so the
            # monitoring page and the stream always count the same people.
            legacy_sender_rows = await (
                await conn.execute(
                    """
                    SELECT id,raw_message_json,sender_id,sender_username,sender_name,
                           sender_kind,sender_chat_id,sender_chat_username,sender_chat_title
                    FROM messages
                    WHERE raw_message_json IS NOT NULL
                      AND (
                        sender_kind IS NULL OR sender_kind='' OR sender_kind='unknown'
                        OR sender_name IS NULL OR TRIM(sender_name)=''
                        OR (sender_kind='chat' AND (
                          sender_chat_id IS NULL
                          AND (sender_chat_username IS NULL OR TRIM(sender_chat_username)='')
                          AND (sender_chat_title IS NULL OR TRIM(sender_chat_title)='')
                        ))
                      )
                    """
                )
            ).fetchall()
            for row in legacy_sender_rows:
                raw_message = _message_from_raw_payload(
                    loads(row["raw_message_json"], {})
                )
                identity = _sender_identity(
                    raw_message if isinstance(raw_message, dict) else {}
                )
                sender_kind = str(identity["kind"] or row["sender_kind"] or "unknown")
                if sender_kind == "unknown" and any(
                    row[key] not in (None, "")
                    for key in ("sender_id", "sender_username", "sender_name")
                ):
                    sender_kind = "user"
                if sender_kind == "chat":
                    await conn.execute(
                        """
                        UPDATE messages
                        SET sender_kind=?,sender_id=NULL,sender_username=NULL,sender_name=?,
                            sender_chat_id=?,sender_chat_username=?,sender_chat_title=?,
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            sender_kind,
                            identity["sender_name"] or row["sender_name"],
                            identity["sender_chat_id"] or row["sender_chat_id"],
                            identity["sender_chat_username"] or row["sender_chat_username"],
                            identity["sender_chat_title"] or row["sender_chat_title"],
                            utc_now(),
                            int(row["id"]),
                        ),
                    )
                elif sender_kind == "user":
                    await conn.execute(
                        """
                        UPDATE messages
                        SET sender_kind=?,sender_id=?,sender_username=?,sender_name=?,updated_at=?
                        WHERE id=?
                        """,
                        (
                            sender_kind,
                            identity["sender_id"] or row["sender_id"],
                            identity["sender_username"] or row["sender_username"],
                            identity["sender_name"] or row["sender_name"],
                            utc_now(),
                            int(row["id"]),
                        ),
                    )
            permalink_rows = await (
                await conn.execute(
                    """
                    SELECT id,source_chat_username,source_chat_id,published_at,message_url,
                           forwarded_origin_username,forwarded_origin_chat_id,
                           forwarded_origin_date,forwarded_origin_url
                    FROM messages
                    WHERE source_chat_username IS NOT NULL
                       OR forwarded_origin_username IS NOT NULL
                    """
                )
            ).fetchall()
            for row in permalink_rows:
                current_message_url = str(row["message_url"] or "").strip()
                exact_message_url = (
                    current_message_url
                    if _is_exact_bale_public_message_url(current_message_url)
                    else _bale_public_message_url(
                        row["source_chat_username"],
                        row["source_chat_id"],
                        row["published_at"],
                    )
                )
                current_origin_url = str(row["forwarded_origin_url"] or "").strip()
                exact_origin_url = (
                    current_origin_url
                    if _is_exact_bale_public_message_url(current_origin_url)
                    else _bale_public_message_url(
                        row["forwarded_origin_username"],
                        row["forwarded_origin_chat_id"],
                        row["forwarded_origin_date"],
                    )
                )
                if current_message_url and not current_message_url.startswith("https://ble.ir/"):
                    exact_message_url = current_message_url
                if current_origin_url and not current_origin_url.startswith("https://ble.ir/"):
                    exact_origin_url = current_origin_url
                if (
                    exact_message_url != (current_message_url or None)
                    or exact_origin_url != (current_origin_url or None)
                ):
                    await conn.execute(
                        """
                        UPDATE messages
                        SET message_url=?,forwarded_origin_url=?,updated_at=?
                        WHERE id=?
                        """,
                        (
                            exact_message_url,
                            exact_origin_url,
                            utc_now(),
                            int(row["id"]),
                        ),
                    )
            await conn.commit()
        finally:
            await conn.close()
        await self._seed()

    async def _seed(self) -> None:
        now = utc_now()
        conn = await self._connect()
        try:
            row = await (await conn.execute("SELECT COUNT(*) AS c FROM bulletin_templates")).fetchone()
            if int(row["c"]) == 0:
                await conn.execute(
                    """
                    INSERT INTO bulletin_templates
                    (name, system_prompt, user_prompt_template, output_format, version, active, created_at, updated_at)
                    VALUES (?, ?, ?, 'json_schema', 1, 1, ?, ?)
                    """,
                    (
                        "قالب استاندارد گرایه",
                        "فقط بر پایه شواهد ورودی تحلیل کن. نام شخص، موضوع، محل بیان و سه سطح خلاصه را به فارسی برگردان.",
                        "پیام‌های تأییدشده را بدون افزودن ادعای تازه تحلیل و خلاصه کن.",
                        now,
                        now,
                    ),
                )
            for name, words in DEFAULT_TOPICS:
                await conn.execute(
                    """
                    INSERT INTO topics(name, canonical_name, active, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(name) DO NOTHING
                    """,
                    (name, canonical_key(name), now, now),
                )
                topic = await (
                    await conn.execute("SELECT topic_id FROM topics WHERE name=?", (name,))
                ).fetchone()
                for word in words:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO topic_keywords(topic_id, keyword, canonical_keyword)
                        VALUES (?, ?, ?)
                        """,
                        (int(topic["topic_id"]), word, canonical_key(word)),
                    )
            await conn.commit()
        finally:
            await conn.close()

    async def ensure_bootstrap_admin(
        self,
        *,
        username: str,
        password: str,
        full_name: str = "مدیر سامانه",
    ) -> int | None:
        username = str(username or "").strip()
        if not username or not password:
            return None
        existing = await self._fetchone(
            "SELECT user_id FROM admin_users WHERE username=? COLLATE NOCASE",
            (username,),
        )
        if existing:
            return int(existing["user_id"])
        now = utc_now()
        return await self._execute(
            """
            INSERT INTO admin_users(
              username,full_name,password_hash,role,active,created_by,created_at,updated_at
            ) VALUES (?,?,?,'superadmin',1,'bootstrap',?,?)
            """,
            (username, full_name, hash_password(password), now, now),
        )

    async def list_admin_users(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            """
            SELECT user_id,username,full_name,role,active,created_by,created_at,
                   updated_at,last_login_at,sender_key,avatar_updated_at
            FROM admin_users ORDER BY active DESC,full_name,username
            """
        )
        for row in rows:
            row["role_title"] = ROLE_TITLES.get(str(row.get("role")), "نامشخص")
        return rows

    async def list_sender_profile_candidates(self) -> list[dict[str, Any]]:
        """Return known Bale senders as selectable account-profile links.

        The keys are intentionally the same stable values used by monitoring
        (``person:<id>`` or ``chat:<id>``).  Names may change over time; the
        identity key does not, so an edited profile still owns its history.
        """
        summary = await self.monitoring_sender_summary(days=90)
        return [
            {
                "sender_key": row.get("sender_key"),
                "sender_name": row.get("sender_name"),
                "sender_username": row.get("sender_username"),
                "sender_type": row.get("sender_type"),
                "profile_user_id": row.get("profile_user_id"),
                "profile_full_name": row.get("profile_full_name"),
            }
            for row in summary.get("people", [])
            if row.get("sender_key")
        ]

    async def attach_sender_profile_displays(
        self, records: Sequence[dict[str, Any]]
    ) -> None:
        """Expose the chosen profile name on message records.

        Source payload names and IDs remain intact for traceability.  The UI,
        however, can consistently prefer ``sender_display_name`` whenever an
        operator has linked that identity to a profile with its own display
        name.
        """

        if not records:
            return
        profile_rows = await self._fetchall(
            """
            SELECT user_id,full_name,username,sender_key
            FROM admin_users
            WHERE active=1 AND sender_key IS NOT NULL AND TRIM(sender_key)<>''
            """
        )
        profile_by_key = {
            str(row["sender_key"]): row
            for row in profile_rows
            if row.get("sender_key")
        }
        for record in records:
            sender_key = _sender_profile_key(record)
            profile = profile_by_key.get(sender_key)
            fallback = (
                str(record.get("sender_chat_title") or "").strip()
                or str(record.get("sender_name") or "").strip()
                or str(record.get("sender_chat_username") or "").strip()
                or str(record.get("sender_username") or "").strip()
                or "نامشخص"
            )
            record["sender_key"] = sender_key
            record["sender_profile_user_id"] = (
                int(profile["user_id"]) if profile else None
            )
            record["sender_profile_full_name"] = (
                str(profile["full_name"]) if profile else None
            )
            record["sender_display_name"] = (
                str(profile["full_name"]) if profile else fallback
            )

    async def get_admin_user_profile(
        self, user_id: int, *, days: int = 30
    ) -> dict[str, Any] | None:
        """Return an operational profile plus its real sender statistics."""
        user = await self.get_admin_user(user_id=user_id)
        if not user:
            return None
        summary = await self.monitoring_sender_summary(days=days)
        matching = next(
            (
                row
                for row in summary.get("people", [])
                if row.get("sender_key")
                and row.get("sender_key") == user.get("sender_key")
            ),
            None,
        )
        return {
            "user_id": int(user["user_id"]),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "role": user.get("role"),
            "role_title": ROLE_TITLES.get(str(user.get("role")), "نامشخص"),
            "active": int(user.get("active") or 0),
            "sender_key": user.get("sender_key"),
            "avatar_updated_at": user.get("avatar_updated_at"),
            "created_at": user.get("created_at"),
            "last_login_at": user.get("last_login_at"),
            "metrics": matching
            or {
                "sender_key": user.get("sender_key"),
                "sender_name": user.get("full_name"),
                "total_messages": 0,
                "window_messages": 0,
                "rated_messages": 0,
                "window_rated_messages": 0,
                "average_rating": None,
                "daily": {},
            },
            "window_days": summary.get("window_days", days),
        }

    async def get_admin_user(
        self,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, Any] | None:
        if user_id is not None:
            return await self._fetchone(
                "SELECT * FROM admin_users WHERE user_id=?", (user_id,)
            )
        if username:
            return await self._fetchone(
                "SELECT * FROM admin_users WHERE username=? COLLATE NOCASE",
                (username.strip(),),
            )
        return None

    async def mark_admin_user_avatar_updated(self, user_id: int) -> str:
        """Record a cache-busting revision after safely storing a profile image."""

        if not await self.get_admin_user(user_id=user_id):
            raise ValueError("کاربر پیدا نشد.")
        updated_at = utc_now()
        await self._execute(
            "UPDATE admin_users SET avatar_updated_at=?,updated_at=? WHERE user_id=?",
            (updated_at, updated_at, user_id),
        )
        return updated_at

    async def update_self_profile(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        current = await self.get_admin_user(user_id=user_id)
        if not current:
            raise ValueError("کاربر پیدا نشد.")
        updates: list[str] = []
        params: list[Any] = []
        if full_name is not None:
            clean_name = normalize_persian(full_name)
            if not clean_name:
                raise ValueError("نام نمایشی کاربر خالی است.")
            updates.append("full_name=?")
            params.append(clean_name)
        if password:
            updates.append("password_hash=?")
            params.append(hash_password(password))
        if not updates:
            return current
        now = utc_now()
        updates.append("updated_at=?")
        params.extend([now, user_id])
        await self._execute(
            f"UPDATE admin_users SET {','.join(updates)} WHERE user_id=?",
            params,
        )
        updated = await self.get_admin_user(user_id=user_id)
        if not updated:
            raise ValueError("کاربر پیدا نشد.")
        return updated

    async def save_admin_user(
        self,
        *,
        username: str,
        full_name: str,
        role: str,
        active: bool,
        actor: str,
        password: str | None = None,
        user_id: int | None = None,
        sender_key: str | None = None,
    ) -> int:
        clean_username = str(username or "").strip()
        clean_name = normalize_persian(full_name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", clean_username):
            raise ValueError(
                "نام کاربری باید ۳ تا ۶۴ نویسه و شامل حروف لاتین، عدد، نقطه، خط تیره یا زیرخط باشد."
            )
        if not clean_name:
            raise ValueError("نام نمایشی کاربر خالی است.")
        if role not in VALID_ROLES:
            raise ValueError("نقش کاربر نامعتبر است.")
        clean_sender_key = str(sender_key or "").strip() or None
        if clean_sender_key and not re.fullmatch(r"(?:person|chat):[^\s]{1,160}", clean_sender_key):
            raise ValueError("هویت ارسال‌کنندهٔ انتخاب‌شده نامعتبر است.")
        now = utc_now()
        if user_id is None:
            if not password:
                raise ValueError("برای کاربر جدید رمز عبور لازم است.")
            try:
                return await self._execute(
                    """
                    INSERT INTO admin_users(
                      username,full_name,password_hash,role,active,sender_key,created_by,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        clean_username,
                        clean_name,
                        hash_password(password),
                        role,
                        int(active),
                        clean_sender_key,
                        actor,
                        now,
                        now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise ValueError("نام کاربری یا هویت ارسال‌کننده قبلاً به پروفایل دیگری متصل شده است.") from exc
        current = await self.get_admin_user(user_id=user_id)
        if not current:
            raise ValueError("کاربر پیدا نشد.")
        params: list[Any] = [
            clean_username,
            clean_name,
            role,
            int(active),
            clean_sender_key,
            now,
        ]
        password_sql = ""
        if password:
            password_sql = ",password_hash=?"
            params.append(hash_password(password))
        params.append(user_id)
        try:
            await self._execute(
                f"""
                UPDATE admin_users SET username=?,full_name=?,role=?,active=?,sender_key=?,
                  updated_at=?{password_sql} WHERE user_id=?
                """,
                params,
            )
        except aiosqlite.IntegrityError as exc:
            raise ValueError("نام کاربری یا هویت ارسال‌کننده قبلاً به پروفایل دیگری متصل شده است.") from exc
        if not active:
            await self._execute(
                "UPDATE admin_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )
        return user_id

    async def authenticate_admin_user(
        self, username: str, password: str
    ) -> AdminPrincipal | None:
        row = await self.get_admin_user(username=username)
        if not row or not int(row.get("active") or 0):
            return None
        if not verify_password(password, str(row.get("password_hash") or "")):
            return None
        now = utc_now()
        await self._execute(
            "UPDATE admin_users SET last_login_at=?,updated_at=? WHERE user_id=?",
            (now, now, int(row["user_id"])),
        )
        return AdminPrincipal(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            full_name=str(row["full_name"]),
            role=str(row["role"]),
        )

    async def create_admin_session(
        self,
        principal: AdminPrincipal,
        *,
        ip_address: str | None,
        user_agent: str | None,
        lifetime_hours: int = 12,
    ) -> str:
        if principal.user_id is None:
            raise ValueError("برای حساب bootstrap نشست دیتابیسی ساخته نمی‌شود.")
        token = new_session_token()
        created = datetime.now(timezone.utc)
        expires = created + timedelta(hours=max(1, min(lifetime_hours, 168)))
        await self._execute(
            """
            INSERT INTO admin_sessions(
              user_id,token_hash,ip_address,user_agent,created_at,expires_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                principal.user_id,
                token_digest(token),
                ip_address,
                str(user_agent or "")[:500] or None,
                created.isoformat(),
                expires.isoformat(),
            ),
        )
        return token

    async def principal_from_session(self, token: str | None) -> AdminPrincipal | None:
        if not token:
            return None
        row = await self._fetchone(
            """
            SELECT u.user_id,u.username,u.full_name,u.role,u.active
            FROM admin_sessions s JOIN admin_users u ON u.user_id=s.user_id
            WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?
            """,
            (token_digest(token), utc_now()),
        )
        if not row or not int(row.get("active") or 0):
            return None
        return AdminPrincipal(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            full_name=str(row["full_name"]),
            role=str(row["role"]),
        )

    async def revoke_admin_session(self, token: str | None) -> None:
        if token:
            await self._execute(
                """
                UPDATE admin_sessions SET revoked_at=?
                WHERE token_hash=? AND revoked_at IS NULL
                """,
                (utc_now(), token_digest(token)),
            )

    async def get_setting(self, key: str) -> str | None:
        item = await self._fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return str(item["value"]) if item and item.get("value") is not None else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._execute(
            """
            INSERT INTO settings(key,value,updated_at) VALUES (?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, utc_now()),
        )

    async def upsert_chat(self, chat: dict[str, Any] | None) -> None:
        if not chat or chat.get("id") is None:
            return
        await self._execute(
            """
            INSERT INTO chats(chat_id,username,title,chat_type,updated_at) VALUES (?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
              username=excluded.username,title=excluded.title,chat_type=excluded.chat_type,updated_at=excluded.updated_at
            """,
            (
                int(chat["id"]),
                chat.get("username"),
                chat.get("title"),
                chat.get("type"),
                utc_now(),
            ),
        )

    async def upsert_user(self, user: dict[str, Any] | None) -> None:
        if not user or user.get("id") is None:
            return
        await self._execute(
            """
            INSERT INTO users(user_id,username,first_name,last_name,updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,first_name=excluded.first_name,last_name=excluded.last_name,
              active=1,updated_at=excluded.updated_at
            """,
            (
                int(user["id"]),
                user.get("username"),
                user.get("first_name"),
                user.get("last_name"),
                utc_now(),
            ),
        )

    async def reviewer_is_active(self, user_id: int) -> bool:
        row = await self._fetchone(
            "SELECT 1 AS ok FROM users WHERE user_id=? AND active=1 AND is_reviewer=1",
            (user_id,),
        )
        return bool(row)

    async def register_monitored_chat(
        self,
        chat: dict[str, Any],
        *,
        target_chat_id: int | None = None,
        target_title: str | None = None,
        added_by: int | None = None,
    ) -> dict[str, Any]:
        if chat.get("id") is None:
            raise ValueError("chat_id لازم است.")
        chat_id = int(chat["id"])
        username = str(chat.get("username") or "").strip().lstrip("@") or None
        username_norm = username.lower() if username else None
        chat_type = str(chat.get("type") or "channel").strip().lower()
        if chat_type not in {"channel", "group", "supergroup"}:
            raise ValueError("نوع منبع باید channel، group یا supergroup باشد.")
        source_kind = "support_group" if chat_type in {"group", "supergroup"} else "monitored_channel"
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute(
                """
                INSERT INTO monitored_chats
                (chat_id,username,username_norm,title,chat_type,source_kind,target_chat_id,target_title,
                 enabled,added_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,1,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                  username=excluded.username,username_norm=excluded.username_norm,title=excluded.title,
                  chat_type=excluded.chat_type,source_kind=excluded.source_kind,
                  target_chat_id=COALESCE(excluded.target_chat_id,monitored_chats.target_chat_id),
                  target_title=COALESCE(excluded.target_title,monitored_chats.target_title),
                  enabled=1,updated_at=excluded.updated_at
                """,
                (
                    chat_id,
                    username,
                    username_norm,
                    chat.get("title") or username or str(chat_id),
                    chat_type,
                    source_kind,
                    target_chat_id,
                    target_title,
                    added_by,
                    now,
                    now,
                ),
            )
            await conn.commit()
            cursor = await conn.execute("SELECT * FROM monitored_chats WHERE chat_id=?", (chat_id,))
            return dict(await cursor.fetchone())
        finally:
            await conn.close()

    async def register_monitored_username(
        self,
        username: str,
        *,
        title: str | None = None,
        target_chat_id: int | None = None,
        target_title: str | None = None,
        added_by: int | None = None,
    ) -> dict[str, Any]:
        clean = username.strip().lstrip("@")
        if not clean:
            raise ValueError("username لازم است.")
        norm = clean.lower()
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute(
                """
                INSERT INTO monitored_chats
                (username,username_norm,title,chat_type,source_kind,target_chat_id,target_title,
                 enabled,added_by,created_at,updated_at)
                VALUES (?,?,?,'channel','monitored_channel',?,?,1,?,?,?)
                ON CONFLICT(username_norm) DO UPDATE SET
                  username=excluded.username,title=excluded.title,
                  target_chat_id=COALESCE(excluded.target_chat_id,monitored_chats.target_chat_id),
                  target_title=COALESCE(excluded.target_title,monitored_chats.target_title),
                  enabled=1,updated_at=excluded.updated_at
                """,
                (
                    clean,
                    norm,
                    title or clean,
                    target_chat_id,
                    target_title,
                    added_by,
                    now,
                    now,
                ),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT * FROM monitored_chats WHERE username_norm=?", (norm,)
            )
            return dict(await cursor.fetchone())
        finally:
            await conn.close()

    async def update_monitored_chat(
        self,
        source_id: int,
        *,
        enabled: bool | None = None,
        target_chat_id: int | None = None,
        target_title: str | None = None,
        title: str | None = None,
        source_kind: str | None = None,
    ) -> None:
        allowed_kinds = {"monitored_channel", "support_group"}
        if source_kind is not None and source_kind not in allowed_kinds:
            raise ValueError("نوع منبع نامعتبر است.")
        fields: list[str] = ["updated_at=?"]
        values: list[Any] = [utc_now()]
        for column, value in (
            ("enabled", None if enabled is None else int(enabled)),
            ("target_chat_id", target_chat_id),
            ("target_title", target_title),
            ("title", title),
            ("source_kind", source_kind),
        ):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(value)
        values.append(source_id)
        await self._execute(
            f"UPDATE monitored_chats SET {', '.join(fields)} WHERE id=?",
            values,
        )

    async def record_monitored_chat_error(self, chat_id: int, error: Exception | str) -> None:
        """Keep the latest ingestion failure visible without disabling the source."""
        message = str(error).strip() or "خطای نامشخص در دریافت منبع"
        await self._execute(
            "UPDATE monitored_chats SET last_error=?,updated_at=? WHERE chat_id=?",
            (message[:1800], utc_now(), int(chat_id)),
        )

    async def list_monitored_chats(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled=1" if enabled_only else ""
        return await self._fetchall(
            f"""
            SELECT mc.*,
              (SELECT COUNT(*) FROM messages m WHERE m.source_chat_id=mc.chat_id) AS message_count,
              (SELECT COUNT(*) FROM messages m WHERE m.source_chat_id=mc.chat_id AND m.status='pending') AS pending_count
            FROM monitored_chats mc {where}
            ORDER BY enabled DESC, title COLLATE NOCASE
            """
        )

    async def resolve_monitored_chat(self, chat: dict[str, Any]) -> dict[str, Any] | None:
        if chat.get("id") is not None:
            found = await self._fetchone(
                "SELECT * FROM monitored_chats WHERE chat_id=? AND enabled=1",
                (int(chat["id"]),),
            )
            if found:
                return found
        username = str(chat.get("username") or "").strip().lstrip("@").lower()
        if username:
            found = await self._fetchone(
                "SELECT * FROM monitored_chats WHERE username_norm=? AND enabled=1",
                (username,),
            )
            if found and found.get("chat_id") is None and chat.get("id") is not None:
                await self._execute(
                    """
                    UPDATE monitored_chats SET chat_id=?,title=COALESCE(?,title),chat_type=COALESCE(?,chat_type),
                    updated_at=? WHERE id=?
                    """,
                    (
                        int(chat["id"]),
                        chat.get("title"),
                        chat.get("type"),
                        utc_now(),
                        int(found["id"]),
                    ),
                )
                found = await self._fetchone(
                    "SELECT * FROM monitored_chats WHERE id=?", (int(found["id"]),)
                )
            return found
        return None

    async def monitored_chat_configured(self, chat: dict[str, Any]) -> bool:
        if chat.get("id") is not None:
            row = await self._fetchone(
                "SELECT 1 AS ok FROM monitored_chats WHERE chat_id=?", (int(chat["id"]),)
            )
            if row:
                return True
        username = str(chat.get("username") or "").strip().lstrip("@").lower()
        if username:
            return bool(
                await self._fetchone(
                    "SELECT 1 AS ok FROM monitored_chats WHERE username_norm=?", (username,)
                )
            )
        return False

    async def target_for_source(
        self, chat: dict[str, Any], default_target: int | None
    ) -> int | None:
        source = await self.resolve_monitored_chat(chat)
        if source and source.get("target_chat_id") is not None:
            return int(source["target_chat_id"])
        return default_target

    async def upsert_message(
        self,
        message: dict[str, Any],
        *,
        received_at: str | None = None,
    ) -> int:
        """Insert or refresh one Bale message.

        ``received_at`` normally records the time at which the dashboard saw
        the update.  The bounded bot-queue recovery deliberately supplies the
        source timestamp instead, so a recovered message is shown in the
        correct historical position rather than at recovery time.
        """
        chat = message.get("chat") or {}
        if chat.get("id") is None or message.get("message_id") is None:
            raise ValueError("پیام فاقد chat.id یا message_id است.")
        await self.upsert_chat(chat)
        sender_identity = _sender_identity(message)
        await self.upsert_user(sender_identity["user"])
        if sender_identity["chat"]:
            await self.upsert_chat(sender_identity["chat"])
        source = await self.resolve_monitored_chat(chat)
        chat_id = int(chat["id"])
        message_id = int(message["message_id"])
        text = message.get("text")
        caption = message.get("caption")
        combined = str(text if text is not None else caption or "")
        message_type, media_json, media_count = _media_payload(message)
        urls = re.findall(r"https?://[^\s<>\"']+", combined, flags=re.IGNORECASE)

        forward_origin = message.get("forward_origin") or {}
        if not isinstance(forward_origin, dict):
            forward_origin = {}
        origin_chat = (
            message.get("forward_from_chat")
            or forward_origin.get("chat")
            or forward_origin.get("sender_chat")
            or {}
        )
        origin_user = (
            message.get("forward_from")
            or forward_origin.get("sender_user")
            or forward_origin.get("user")
            or {}
        )
        if not isinstance(origin_chat, dict):
            origin_chat = {}
        if not isinstance(origin_user, dict):
            origin_user = {}
        origin_name = (
            message.get("forward_sender_name")
            or forward_origin.get("sender_user_name")
            or forward_origin.get("sender_name")
            or _display_name(origin_user)
            or origin_chat.get("title")
        )
        origin_username = str(origin_chat.get("username") or origin_user.get("username") or "").strip().lstrip("@") or None
        origin_title = origin_chat.get("title") or origin_name
        origin_chat_id = origin_chat.get("id") or origin_user.get("id")
        origin_message_id = (
            message.get("forward_from_message_id")
            or forward_origin.get("message_id")
        )
        origin_date_raw = message.get("forward_date") or forward_origin.get("date")
        is_forwarded = bool(
            origin_chat
            or origin_user
            or message.get("forward_sender_name")
            or message.get("forward_date")
            or forward_origin
        )
        origin_url = message.get("forward_origin_url")
        if (
            not origin_url
            and origin_chat
            and str(origin_chat.get("type") or "").lower() == "channel"
        ):
            origin_url = _bale_public_message_url(
                origin_username,
                origin_chat_id,
                origin_date_raw
                or (
                    origin_message_id
                    if len(str(origin_message_id or "").lstrip("-")) == 13
                    else None
                ),
            )

        username = str(chat.get("username") or "").strip().lstrip("@")
        message_url = message.get("url") or message.get("link")
        if not message_url and str(chat.get("type") or "").lower() == "channel":
            message_url = _bale_public_message_url(
                username,
                chat_id,
                message.get("date"),
            )
        now = utc_now()
        published = _epoch_iso(message.get("date")) or now
        stored_received_at = str(received_at or now).strip() or now
        edited = _epoch_iso(message.get("edit_date"))
        source_kind = str((source or {}).get("source_kind") or ("support_group" if chat.get("type") in {"group", "supergroup"} else "monitored_channel"))
        normalized = normalize_persian(combined)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None

        conn = await self._connect()
        try:
            await conn.execute(
                """
                INSERT INTO messages(
                  source_chat_id,source_message_id,source_chat_username,source_chat_title,source_chat_type,source_kind,
                  sender_id,sender_username,sender_name,sender_kind,
                  sender_chat_id,sender_chat_username,sender_chat_title,
                  text,caption,normalized_text,message_type,media_json,media_count,
                  link_count,message_url,published_at,received_at,edited_at,is_forwarded,
                  forwarded_origin_chat_id,forwarded_origin_username,forwarded_origin_title,
                  forwarded_origin_url,forwarded_origin_message_id,forwarded_origin_sender_name,forwarded_origin_date,
                  raw_message_json,text_sha256,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET
                  source_chat_username=excluded.source_chat_username,
                  source_chat_title=excluded.source_chat_title,
                  source_chat_type=excluded.source_chat_type,
                  source_kind=excluded.source_kind,
                  sender_id=excluded.sender_id,sender_username=excluded.sender_username,sender_name=excluded.sender_name,
                  sender_kind=excluded.sender_kind,
                  sender_chat_id=excluded.sender_chat_id,
                  sender_chat_username=excluded.sender_chat_username,
                  sender_chat_title=excluded.sender_chat_title,
                  text=excluded.text,caption=excluded.caption,normalized_text=excluded.normalized_text,
                  message_type=excluded.message_type,media_json=excluded.media_json,media_count=excluded.media_count,
                  link_count=excluded.link_count,message_url=COALESCE(excluded.message_url,messages.message_url),
                  published_at=excluded.published_at,edited_at=excluded.edited_at,
                  is_forwarded=excluded.is_forwarded,
                  forwarded_origin_chat_id=excluded.forwarded_origin_chat_id,
                  forwarded_origin_username=excluded.forwarded_origin_username,
                  forwarded_origin_title=excluded.forwarded_origin_title,
                  forwarded_origin_url=excluded.forwarded_origin_url,
                  forwarded_origin_message_id=excluded.forwarded_origin_message_id,
                  forwarded_origin_sender_name=excluded.forwarded_origin_sender_name,
                  forwarded_origin_date=excluded.forwarded_origin_date,
                  raw_message_json=excluded.raw_message_json,text_sha256=excluded.text_sha256,updated_at=excluded.updated_at
                """,
                (
                    chat_id,
                    message_id,
                    chat.get("username"),
                    chat.get("title"),
                    chat.get("type"),
                    source_kind,
                    sender_identity["sender_id"],
                    sender_identity["sender_username"],
                    sender_identity["sender_name"],
                    sender_identity["kind"],
                    sender_identity["sender_chat_id"],
                    sender_identity["sender_chat_username"],
                    sender_identity["sender_chat_title"],
                    text,
                    caption,
                    normalized,
                    message_type,
                    media_json,
                    media_count,
                    len(urls),
                    message_url,
                    published,
                    stored_received_at,
                    edited,
                    int(is_forwarded),
                    origin_chat_id,
                    origin_username,
                    origin_title,
                    origin_url,
                    origin_message_id,
                    origin_name,
                    _epoch_iso(origin_date_raw),
                    dumps(message),
                    digest,
                    now,
                    now,
                ),
            )
            await conn.execute(
                """
                UPDATE monitored_chats SET last_received_at=?,last_success_at=?,last_error=NULL,updated_at=?
                WHERE chat_id=?
                """,
                (stored_received_at, now, now, chat_id),
            )
            await conn.commit()
            row = await (
                await conn.execute(
                    "SELECT id FROM messages WHERE source_chat_id=? AND source_message_id=?",
                    (chat_id, message_id),
                )
            ).fetchone()
            return int(row["id"])
        finally:
            await conn.close()

    async def get_message(self, message_id: int) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM messages WHERE id=?", (message_id,))

    async def get_message_by_source(
        self, source_chat_id: int, source_message_id: int
    ) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM messages WHERE source_chat_id=? AND source_message_id=?",
            (source_chat_id, source_message_id),
        )

    async def set_control_message(
        self, message_id: int, chat_id: int, control_message_id: int
    ) -> None:
        await self._execute(
            "UPDATE messages SET control_chat_id=?,control_message_id=?,updated_at=? WHERE id=?",
            (chat_id, control_message_id, utc_now(), message_id),
        )

    async def set_managed_message(
        self,
        message_id: int,
        chat_id: int,
        original_message_id: int,
        managed_message_id: int,
        mode: str,
    ) -> None:
        await self._execute(
            """
            UPDATE messages SET control_chat_id=?,control_message_id=?,
              managed_original_message_id=?,managed_message_id=?,managed_mode=?,updated_at=?
            WHERE id=?
            """,
            (
                chat_id,
                managed_message_id,
                original_message_id,
                managed_message_id,
                mode,
                utc_now(),
                message_id,
            ),
        )

    async def is_control_message(self, chat_id: int, message_id: int) -> bool:
        return bool(
            await self._fetchone(
                """
                SELECT 1 AS ok FROM messages
                WHERE (control_chat_id=? AND control_message_id=?)
                   OR (source_chat_id=? AND managed_message_id=?)
                LIMIT 1
                """,
                (chat_id, message_id, chat_id, message_id),
            )
        )

    async def set_delivery(
        self, message_id: int, target_chat_id: int, target_message_id: int | None
    ) -> None:
        await self._execute(
            """
            UPDATE messages SET target_chat_id=?,target_message_id=?,delivered_at=?,updated_at=?
            WHERE id=?
            """,
            (target_chat_id, target_message_id, utc_now(), utc_now(), message_id),
        )

    async def claim_status(
        self,
        message_id: int,
        actor_id: int,
        new_status: str,
        *,
        actor_name: str | None = None,
        actor_username: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        if new_status not in {"approved", "rejected"}:
            raise ValueError("وضعیت نامعتبر است.")
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            current = await (
                await conn.execute("SELECT * FROM messages WHERE id=?", (message_id,))
            ).fetchone()
            if current is None:
                await conn.rollback()
                return False, None
            if str(current["status"]) != "pending":
                await conn.rollback()
                return False, dict(current)
            now = utc_now()
            if new_status == "approved":
                await conn.execute(
                    """
                    UPDATE messages SET status='approved',approved_by=?,approved_by_name=?,
                    approved_by_username=?,reviewed_at=?,updated_at=? WHERE id=? AND status='pending'
                    """,
                    (actor_id, actor_name, actor_username, now, now, message_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE messages SET status='rejected',rejected_by=?,rejected_by_name=?,
                    rejected_by_username=?,reviewed_at=?,updated_at=? WHERE id=? AND status='pending'
                    """,
                    (actor_id, actor_name, actor_username, now, now, message_id),
                )
            await conn.execute(
                """
                INSERT INTO message_actions(message_id,actor_id,action_type,old_status,new_status,created_at)
                VALUES (?,?,'status_changed','pending',?,?)
                """,
                (message_id, actor_id, new_status, now),
            )
            await conn.commit()
            updated = await (
                await conn.execute("SELECT * FROM messages WHERE id=?", (message_id,))
            ).fetchone()
            return True, dict(updated)
        finally:
            await conn.close()

    async def add_action(
        self,
        message_id: int | None,
        actor_id: int | None,
        action_type: str,
        *,
        old_status: str | None = None,
        new_status: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO message_actions(message_id,actor_id,action_type,old_status,new_status,details_json,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                message_id,
                actor_id,
                action_type,
                old_status,
                new_status,
                dumps(details or {}),
                utc_now(),
            ),
        )

    async def add_source(
        self,
        message_id: int,
        url: str,
        actor_id: int,
        *,
        qr_path: str | None = None,
        qr_sha256: str | None = None,
    ) -> None:
        await self._execute(
            """
            UPDATE messages SET primary_source_url=?,primary_source_qr_path=?,primary_source_qr_sha256=?,
            updated_at=? WHERE id=?
            """,
            (url, qr_path, qr_sha256, utc_now(), message_id),
        )
        await self.add_action(message_id, actor_id, "source_saved", details={"url": url})

    async def add_oration(self, message_id: int, text: str, actor_id: int) -> None:
        await self._execute(
            """
            UPDATE messages SET oration_text=?,oration_by=?,oration_at=?,updated_at=? WHERE id=?
            """,
            (text, actor_id, utc_now(), utc_now(), message_id),
        )
        await self.add_action(message_id, actor_id, "oration_saved")

    async def set_message_rating(
        self,
        message_id: int,
        rating_half: int,
        *,
        actor_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Save a 1..5 editorial score in half-star increments.

        The integer representation avoids SQLite's floating point quirks while
        keeping the public API comfortably expressed in ordinary stars.
        """
        if rating_half < 2 or rating_half > 10:
            raise ValueError("امتیاز باید بین ۱ تا ۵ ستاره باشد.")
        now = utc_now()
        await self._execute(
            "UPDATE messages SET editorial_rating_half=?,updated_at=? WHERE id=?",
            (rating_half, now, message_id),
        )
        item = await self.get_message(message_id)
        if not item:
            return None
        await self.add_action(
            message_id,
            actor_id,
            "editorial_rating_saved",
            details={"rating": rating_half / 2},
        )
        item["editorial_rating"] = rating_half / 2
        return item

    async def update_message_qr(
        self, message_id: int, *, qr_path: str, qr_sha256: str
    ) -> None:
        await self._execute(
            """
            UPDATE messages SET primary_source_qr_path=?,primary_source_qr_sha256=?,updated_at=? WHERE id=?
            """,
            (qr_path, qr_sha256, utc_now(), message_id),
        )

    async def purge_rejected_content(self, message_id: int) -> None:
        await self._execute(
            """
            UPDATE messages SET text=NULL,caption=NULL,normalized_text='',media_json='[]',
            media_count=0,raw_message_json=NULL,updated_at=? WHERE id=? AND status='rejected'
            """,
            (utc_now(), message_id),
        )

    async def list_review_messages_for_reconciliation(self) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT * FROM messages
            WHERE control_message_id IS NOT NULL OR managed_message_id IS NOT NULL
            ORDER BY id DESC LIMIT 1000
            """
        )

    async def _attach_automatic_editorial_ratings(
        self, items: list[dict[str, Any]]
    ) -> None:
        """Decorate dashboard records with the derived, non-editable score."""
        message_ids = [int(item["id"]) for item in items if item.get("id") is not None]
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        finalized_rows = await self._fetchall(
            f"""
            SELECT DISTINCT i.message_id
            FROM editorial_draft_inputs i
            JOIN editorial_drafts d ON d.draft_id=i.draft_id
            WHERE d.status='finalized' AND i.message_id IN ({placeholders})
            """,
            message_ids,
        )
        finalized_ids = {int(row["message_id"]) for row in finalized_rows}
        for item in items:
            item["is_finalized"] = int(item.get("id") or 0) in finalized_ids
            score, components = _automatic_editorial_rating(item)
            item["editorial_rating"] = score
            item["editorial_rating_components"] = components

    async def list_messages_dashboard(
        self,
        *,
        status: str | None = None,
        source_chat_id: int | None = None,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        if status == "analyzed":
            # Analysis is a workflow state layered on top of the source-review
            # status, so it must not be compared to ``messages.status``.
            where.append(
                """(m.ai_enrichment_status='validated' OR EXISTS (
                    SELECT 1 FROM message_speaker_tags analyzed
                    WHERE analyzed.message_id=m.id
                ))"""
            )
        elif status:
            where.append("m.status=?")
            params.append(status)
        if source_chat_id is not None:
            where.append("m.source_chat_id=?")
            params.append(source_chat_id)
        if query:
            where.append(
                """(m.normalized_text LIKE ? OR m.sender_name LIKE ?
                OR m.sender_chat_title LIKE ? OR m.sender_chat_username LIKE ?
                OR m.forwarded_origin_title LIKE ? OR m.detected_person_name LIKE ?
                OR m.detected_topic_name LIKE ?
                OR EXISTS (
                    SELECT 1 FROM message_speaker_tags st
                    WHERE st.message_id=m.id AND (
                        st.speaker_name LIKE ? OR st.specific_topic LIKE ?
                        OR st.general_topic LIKE ?
                    )
                ))"""
            )
            token = f"%{normalize_persian(query)}%"
            params.extend([token] * 10)
        _append_message_window(where, params, date_from, date_to)
        clause = " AND ".join(where)
        total_row = await self._fetchone(
            f"SELECT COUNT(*) AS c FROM messages m WHERE {clause}", params
        )
        params.extend([max(1, min(int(limit), 5000)), max(0, offset)])
        items = await self._fetchall(
            f"""
            SELECT m.* FROM messages m
            WHERE {clause}
            ORDER BY COALESCE(m.published_at,m.received_at) DESC, m.id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        if items:
            message_ids = [int(item["id"]) for item in items]
            placeholders = ",".join("?" for _ in message_ids)
            tags = await self._fetchall(
                f"""
                SELECT * FROM message_speaker_tags
                WHERE message_id IN ({placeholders})
                ORDER BY message_id,sort_order,tag_id
                """,
                message_ids,
            )
            by_message: dict[int, list[dict[str, Any]]] = {}
            for tag in tags:
                by_message.setdefault(int(tag["message_id"]), []).append(tag)
            for item in items:
                item["speaker_tags"] = by_message.get(int(item["id"]), [])
        for item in items:
            item["is_analyzed"] = bool(item.get("speaker_tags")) or str(
                item.get("ai_enrichment_status") or ""
            ).lower() == "validated"
            item["workflow_status"] = "analyzed" if item["is_analyzed"] else item.get("status")
            _attach_flow_timestamp(item, "published_at", "received_at", "created_at")
        await self._attach_automatic_editorial_ratings(items)
        await self.attach_sender_profile_displays(items)
        return {"total": int((total_row or {}).get("c") or 0), "items": items}

    def _analyzed_message_sql(self) -> str:
        return """(m.ai_enrichment_status='validated' OR EXISTS (
                    SELECT 1 FROM message_speaker_tags analyzed
                    WHERE analyzed.message_id=m.id
                ))"""

    async def message_window_progress(
        self, *, date_from: str | None = None, date_to: str | None = None
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        _append_message_window(where, params, date_from, date_to)
        clause = " AND ".join(where)
        analyzed_sql = self._analyzed_message_sql()
        total_row = await self._fetchone(
            f"SELECT COUNT(*) AS c FROM messages m WHERE {clause}", params
        )
        analyzed_row = await self._fetchone(
            f"SELECT COUNT(*) AS c FROM messages m WHERE {clause} AND {analyzed_sql}",
            params,
        )
        total = int((total_row or {}).get("c") or 0)
        analyzed = int((analyzed_row or {}).get("c") or 0)
        remaining = max(0, total - analyzed)
        remaining_ids = await self._fetchall(
            f"""
            SELECT m.id FROM messages m
            WHERE {clause} AND NOT {analyzed_sql}
            ORDER BY COALESCE(m.published_at,m.received_at) DESC, m.id DESC
            LIMIT 5000
            """,
            params,
        )
        return {
            "total": total,
            "analyzed": analyzed,
            "remaining": remaining,
            "remaining_ids": [int(row["id"]) for row in remaining_ids],
        }

    async def message_detail(self, message_id: int) -> dict[str, Any] | None:
        item = await self.get_message(message_id)
        if not item:
            return None
        item["media"] = loads(item.get("media_json"), [])
        item["analysis"] = loads(item.get("ai_analysis_json"), {})
        item["speaker_tags"] = await self._fetchall(
            """
            SELECT * FROM message_speaker_tags
            WHERE message_id=? ORDER BY sort_order,tag_id
            """,
            (message_id,),
        )
        item["actions"] = await self._fetchall(
            "SELECT * FROM message_actions WHERE message_id=? ORDER BY id", (message_id,)
        )
        item["is_analyzed"] = bool(item.get("speaker_tags")) or str(
            item.get("ai_enrichment_status") or ""
        ).lower() == "validated"
        item["workflow_status"] = "analyzed" if item["is_analyzed"] else item.get("status")
        await self._attach_automatic_editorial_ratings([item])
        _attach_flow_timestamp(item, "published_at", "received_at", "created_at")
        await self.attach_sender_profile_displays([item])
        return item

    async def replace_message_speaker_tags(
        self,
        message_id: int,
        speakers: list[dict[str, Any]],
        *,
        provider: str | None,
        model: str | None,
        prompt_version: str,
        analyzed_by: str,
        content_type: str = "person_statement",
        main_subject: str | None = None,
        general_topic: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Persist model-one analysis.

        Statements are represented by one or more speaker tags.  Events are
        intentionally represented only on ``messages``: a tag would make an
        event appear as an unknown person and could make unrelated events
        merge in the editorial desk.
        """
        message = await self.get_message(message_id)
        if not message:
            raise ValueError("پیام پیدا نشد.")

        is_event = str(content_type or "").strip().casefold() in {
            "event",
            "رویداد",
            "رویداد مهم ایران و جهان",
        }
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in speakers:
            name = normalize_persian(str(raw.get("name") or "نامشخص")).strip() or "نامشخص"
            position = normalize_persian(str(raw.get("position") or "نامشخص")).strip() or "نامشخص"
            specific_topic = normalize_persian(
                str(raw.get("specific_topic") or "نامشخص")
            ).strip() or "نامشخص"
            speaker_general_topic = normalize_persian(
                str(raw.get("general_topic") or "نامشخص")
            ).strip() or "نامشخص"
            evidence = normalize_persian(str(raw.get("evidence") or "")).strip() or None
            expression_method_type = normalize_persian(
                str(raw.get("expression_method_type") or "نامشخص")
            ).strip() or "نامشخص"
            expression_method_context = normalize_persian(
                str(raw.get("expression_method_context") or "نامشخص")
            ).strip() or "نامشخص"
            expression_method_evidence = normalize_persian(
                str(raw.get("expression_method_evidence") or "")
            ).strip() or None
            canonical_speaker = canonical_key(name) or canonical_key("نامشخص")
            dedupe_key = (
                canonical_speaker,
                canonical_key(specific_topic),
                canonical_key(speaker_general_topic),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            preset_person_id = raw.get("person_id")
            person = None
            if preset_person_id not in (None, "", 0, "0"):
                person = await self.get_person(int(preset_person_id))
            if person is None and name != "نامشخص":
                person = await self.find_unique_person(name)
            normalized.append(
                {
                    "name": name[:240],
                    "canonical_speaker": canonical_speaker,
                    "position": position[:300],
                    "specific_topic": specific_topic[:500],
                    "general_topic": speaker_general_topic[:160],
                    "evidence": evidence[:700] if evidence else None,
                    "expression_method_type": expression_method_type[:160],
                    "expression_method_context": expression_method_context[:500],
                    "expression_method_evidence": expression_method_evidence[:700]
                    if expression_method_evidence
                    else None,
                    "person_id": int(person["person_id"]) if person else None,
                }
            )
        if not is_event and not normalized:
            normalized = [
                {
                    "name": "نامشخص",
                    "canonical_speaker": canonical_key("نامشخص"),
                    "position": "نامشخص",
                    "specific_topic": "نامشخص",
                    "general_topic": "نامشخص",
                    "evidence": None,
                    "expression_method_type": "نامشخص",
                    "expression_method_context": "نامشخص",
                    "expression_method_evidence": None,
                    "person_id": None,
                }
            ]

        normalized_main_subject = normalize_persian(str(main_subject or "")).strip()
        normalized_general_topic = normalize_persian(str(general_topic or "")).strip()
        raw_event = event if isinstance(event, dict) else {}
        raw_entities = raw_event.get("involved_entities")
        event_entities = (
            [
                normalize_persian(str(value)).strip()[:240]
                for value in raw_entities
                if normalize_persian(str(value)).strip()
            ]
            if isinstance(raw_entities, list)
            else []
        )
        normalized_event = {
            "title": normalize_persian(
                str(raw_event.get("title") or normalized_main_subject or "نامشخص")
            ).strip()
            or "نامشخص",
            "involved_entities": list(dict.fromkeys(event_entities)),
            "location": normalize_persian(
                str(raw_event.get("location") or "نامشخص")
            ).strip()
            or "نامشخص",
            "time": normalize_persian(str(raw_event.get("time") or "نامشخص")).strip()
            or "نامشخص",
        }
        primary = (
            next(
                (item for item in normalized if item["name"] != "نامشخص"),
                normalized[0],
            )
            if normalized
            else None
        )
        effective_general_topic = (
            normalized_general_topic
            or (primary or {}).get("general_topic")
            or "نامشخص"
        )
        effective_main_subject = (
            normalized_main_subject
            or (primary or {}).get("specific_topic")
            or normalized_event["title"]
        )
        topic = await self._fetchone(
            """
            SELECT topic_id,name FROM topics
            WHERE canonical_name=? AND active=1 LIMIT 1
            """,
            (canonical_key(effective_general_topic),),
        )
        location_type = (primary or {}).get("expression_method_type")
        location_context = (primary or {}).get("expression_method_context")
        location_label = (
            " · ".join(
                value
                for value in (location_type, location_context)
                if value and value != "نامشخص"
            )
            or None
        )
        analysis_payload = {
            "content_type": "رویداد مهم ایران و جهان" if is_event else "اظهارنظر شخص‌محور",
            "main_subject": effective_main_subject,
            "general_topic": effective_general_topic,
            "speakers": normalized,
            "event": normalized_event if is_event else None,
            "prompt_version": prompt_version,
        }
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "DELETE FROM message_speaker_tags WHERE message_id=?",
                (message_id,),
            )
            for order, item in enumerate(normalized):
                await conn.execute(
                    """
                    INSERT INTO message_speaker_tags(
                      message_id,speaker_name,canonical_speaker,position,
                      specific_topic,general_topic,evidence,expression_method_type,
                      expression_method_context,expression_method_evidence,person_id,sort_order,
                      provider,model,prompt_version,analyzed_by,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        message_id,
                        item["name"],
                        item["canonical_speaker"],
                        item["position"],
                        item["specific_topic"],
                        item["general_topic"],
                        item["evidence"],
                        item["expression_method_type"],
                        item["expression_method_context"],
                        item["expression_method_evidence"],
                        item["person_id"],
                        order,
                        provider,
                        model,
                        prompt_version,
                        analyzed_by,
                        now,
                    ),
                )
            await conn.execute(
                """
                UPDATE messages SET detected_person_name=?,detected_person_id=?,
                  person_candidate_id=NULL,person_match_method='selected_ai_analysis',
                  person_confidence=?,detected_topic_id=?,detected_topic_name=?,
                  topic_confidence=?,statement_type=?,statement_location_type=?,statement_location_label=?,
                  ai_enrichment_status='validated',ai_enrichment_confidence=1,
                  ai_analysis_json=?,analysis_content_type=?,analysis_main_subject=?,analysis_general_topic=?,
                  analysis_event_title=?,analysis_event_entities_json=?,analysis_event_location=?,analysis_event_time=?,
                  processing_error=NULL,updated_at=?
                WHERE id=?
                """,
                (
                    None if is_event else (primary or {}).get("name"),
                    None if is_event else (primary or {}).get("person_id"),
                    0.0 if is_event else (1.0 if (primary or {}).get("name") != "نامشخص" else 0.0),
                    int(topic["topic_id"]) if topic else None,
                    effective_general_topic,
                    1.0 if effective_general_topic != "نامشخص" else 0.0,
                    "رویداد" if is_event else "اظهارنظر",
                    None if is_event else location_type,
                    None if is_event else location_label,
                    dumps(analysis_payload),
                    "event" if is_event else "person_statement",
                    effective_main_subject,
                    effective_general_topic,
                    normalized_event["title"] if is_event else None,
                    dumps(normalized_event["involved_entities"]) if is_event else None,
                    normalized_event["location"] if is_event else None,
                    normalized_event["time"] if is_event else None,
                    now,
                    message_id,
                ),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return await self._fetchall(
            """
            SELECT * FROM message_speaker_tags
            WHERE message_id=? ORDER BY sort_order,tag_id
            """,
            (message_id,),
        )

    async def analysis_filter_catalog(
        self, *, date_from: str | None = None, date_to: str | None = None
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        _append_message_window(where, params, date_from, date_to)
        clause = " AND ".join(where)
        remaining_clause = f"""{clause} AND NOT EXISTS (
            SELECT 1 FROM editorial_draft_inputs edi
            JOIN editorial_drafts ed ON ed.draft_id=edi.draft_id
            WHERE edi.message_id=m.id AND ed.status='finalized'
        ) AND NOT EXISTS (
            SELECT 1 FROM editorial_message_decisions emd
            WHERE emd.message_id=m.id AND emd.decision='discarded'
        )"""
        speakers = await self._fetchall(
            f"""
            SELECT st.canonical_speaker,
              (
                SELECT recent.speaker_name
                FROM message_speaker_tags recent
                WHERE recent.canonical_speaker=st.canonical_speaker
                ORDER BY CASE WHEN recent.speaker_name='نامشخص' THEN 1 ELSE 0 END,
                         recent.created_at DESC,recent.tag_id DESC
                LIMIT 1
              ) AS speaker_name,
              MAX(NULLIF(position,'نامشخص')) AS position,
              COUNT(DISTINCT message_id) AS message_count,
              MAX(st.created_at) AS last_analyzed_at
            FROM message_speaker_tags st JOIN messages m ON m.id=st.message_id
            WHERE {remaining_clause}
            GROUP BY st.canonical_speaker
            ORDER BY CASE WHEN speaker_name='نامشخص' THEN 1 ELSE 0 END,
                     message_count DESC,speaker_name
            """,
            params,
        )
        general_topics = await self._fetchall(
            f"""
            SELECT general_topic,COUNT(DISTINCT message_id) AS message_count
            FROM message_speaker_tags st JOIN messages m ON m.id=st.message_id
            WHERE {remaining_clause}
            GROUP BY general_topic ORDER BY message_count DESC,general_topic
            """,
            params,
        )
        specific_topics = await self._fetchall(
            f"""
            SELECT specific_topic,COUNT(DISTINCT message_id) AS message_count
            FROM message_speaker_tags st JOIN messages m ON m.id=st.message_id
            WHERE {remaining_clause}
            GROUP BY specific_topic ORDER BY message_count DESC,specific_topic
            LIMIT 300
            """,
            params,
        )
        events = await self._fetchall(
            f"""
            SELECT m.id AS message_id,
              COALESCE(NULLIF(m.analysis_event_title,''),NULLIF(m.analysis_main_subject,''),'رویداد بدون عنوان') AS event_title,
              COALESCE(NULLIF(m.analysis_general_topic,''),NULLIF(m.detected_topic_name,''),'نامشخص') AS general_topic,
              m.analysis_event_location AS location,m.analysis_event_time AS event_time,
              m.published_at,m.received_at,m.created_at
            FROM messages m
            WHERE {remaining_clause} AND m.analysis_content_type='event'
            ORDER BY COALESCE(m.published_at,m.received_at) DESC,m.id DESC
            """,
            params,
        )
        for event in events:
            _attach_flow_timestamp(event, "published_at", "received_at", "created_at")
        total_people_row = await self._fetchone(
            f"""
            SELECT COUNT(*) AS c FROM (
              SELECT st.canonical_speaker
              FROM message_speaker_tags st JOIN messages m ON m.id=st.message_id
              WHERE {clause}
              GROUP BY st.canonical_speaker
            )
            """,
            params,
        ) or {}
        total_events_row = await self._fetchone(
            f"""
            SELECT COUNT(*) AS c FROM messages m
            WHERE {clause} AND m.analysis_content_type='event'
            """,
            params,
        ) or {}
        remaining_messages_row = await self._fetchone(
            f"""
            SELECT COUNT(DISTINCT m.id) AS c FROM messages m
            WHERE {remaining_clause} AND (
              m.analysis_content_type='event' OR EXISTS (
                SELECT 1 FROM message_speaker_tags st WHERE st.message_id=m.id
              )
            )
            """,
            params,
        ) or {}
        total_people = int(total_people_row.get("c") or 0)
        total_events = int(total_events_row.get("c") or 0)
        remaining_people = len(speakers)
        remaining_events = len(events)
        total_units = total_people + total_events
        remaining_units = remaining_people + remaining_events
        return {
            "speakers": speakers,
            "general_topics": general_topics,
            "specific_topics": specific_topics,
            "events": events,
            "progress": {
                "total_people": total_people,
                "remaining_people": remaining_people,
                "total_events": total_events,
                "remaining_events": remaining_events,
                "remaining_messages": int(remaining_messages_row.get("c") or 0),
                "completed_percent": round(
                    max(0, total_units - remaining_units) / total_units * 100
                ) if total_units else 100,
            },
        }

    async def list_analyzed_messages(
        self,
        *,
        speaker: str | None = None,
        general_topic: str | None = None,
        specific_topic: str | None = None,
        event_message_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        where = [
            "1=1",
            "NOT EXISTS (SELECT 1 FROM editorial_draft_inputs edi "
            "JOIN editorial_drafts ed ON ed.draft_id=edi.draft_id "
            "WHERE edi.message_id=m.id AND ed.status='finalized')",
            "NOT EXISTS (SELECT 1 FROM editorial_message_decisions emd "
            "WHERE emd.message_id=m.id AND emd.decision='discarded')",
        ]
        params: list[Any] = []
        if event_message_id is not None:
            where.append("m.analysis_content_type='event'")
            where.append("m.id=?")
            params.append(int(event_message_id))
            if general_topic:
                where.append("COALESCE(m.analysis_general_topic,m.detected_topic_name)=?")
                params.append(normalize_persian(general_topic))
            if date_from or date_to:
                _append_message_window(where, params, date_from, date_to)
            params.append(max(1, min(int(limit), 1)))
            rows = await self._fetchall(
                f"""
                SELECT m.*,NULL AS tag_id,NULL AS speaker_name,NULL AS speaker_position,
                  m.analysis_main_subject AS specific_topic,
                  COALESCE(m.analysis_general_topic,m.detected_topic_name) AS general_topic,
                  NULL AS speaker_evidence,NULL AS analyzed_person_id,NULL AS analyzed_by,
                  NULL AS analyzed_at
                FROM messages m
                WHERE {' AND '.join(where)}
                ORDER BY COALESCE(m.published_at,m.received_at) DESC,m.id DESC
                LIMIT ?
                """,
                params,
            )
            for row in rows:
                row["event"] = {
                    "title": row.get("analysis_event_title") or row.get("analysis_main_subject"),
                    "involved_entities": loads(row.get("analysis_event_entities_json"), []),
                    "location": row.get("analysis_event_location") or "نامشخص",
                    "time": row.get("analysis_event_time") or "نامشخص",
                }
                row["is_analyzed"] = True
                row["workflow_status"] = "analyzed"
                _attach_flow_timestamp(row, "published_at", "received_at", "created_at")
                row["analyzed_datetime"] = row.pop("flow_datetime")
                row["analyzed_date"] = row.pop("flow_date")
                row["analyzed_time"] = row.pop("flow_time")
                row["analyzed_timezone"] = row.pop("flow_timezone")
                row["analyzed_timestamp"] = row.pop("flow_timestamp")
                _attach_flow_timestamp(row, "published_at", "received_at", "created_at")
            return rows
        if speaker:
            where.append("st.canonical_speaker=?")
            params.append(canonical_key(speaker))
        if general_topic:
            where.append("st.general_topic=?")
            params.append(normalize_persian(general_topic))
        if specific_topic:
            where.append("st.specific_topic=?")
            params.append(normalize_persian(specific_topic))
        if date_from or date_to:
            _append_message_window(where, params, date_from, date_to)
        params.append(max(1, min(int(limit), 2000)))
        rows = await self._fetchall(
            f"""
            SELECT m.*,st.tag_id,st.speaker_name,st.position AS speaker_position,
              st.specific_topic,st.general_topic,st.evidence AS speaker_evidence,
              st.person_id AS analyzed_person_id,st.analyzed_by,st.created_at AS analyzed_at
            FROM message_speaker_tags st
            JOIN messages m ON m.id=st.message_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(m.published_at,m.received_at) DESC,m.id DESC,st.sort_order
            LIMIT ?
            """,
            params,
        )
        for row in rows:
            row["is_analyzed"] = True
            row["workflow_status"] = "analyzed"
            _attach_flow_timestamp(row, "published_at", "received_at", "created_at")
            _attach_flow_timestamp(row, "analyzed_at", "published_at", "received_at")
            row["analyzed_datetime"] = row.pop("flow_datetime")
            row["analyzed_date"] = row.pop("flow_date")
            row["analyzed_time"] = row.pop("flow_time")
            row["analyzed_timezone"] = row.pop("flow_timezone")
            row["analyzed_timestamp"] = row.pop("flow_timestamp")
            _attach_flow_timestamp(row, "published_at", "received_at", "created_at")
        return rows

    async def discard_analyzed_message(
        self, message_id: int, *, actor: str, reason: str | None = None
    ) -> dict[str, Any]:
        message = await self._fetchone(
            """
            SELECT id,analysis_content_type FROM messages
            WHERE id=? AND (
                analysis_content_type='event' OR EXISTS (
                    SELECT 1 FROM message_speaker_tags st WHERE st.message_id=messages.id
                )
            )
            """,
            (message_id,),
        )
        if not message:
            raise ValueError("پیام تحلیل‌شده برای کنار گذاشتن پیدا نشد.")
        finalized = await self._fetchone(
            """
            SELECT 1 AS present FROM editorial_draft_inputs edi
            JOIN editorial_drafts ed ON ed.draft_id=edi.draft_id
            WHERE edi.message_id=? AND ed.status='finalized' LIMIT 1
            """,
            (message_id,),
        )
        if finalized:
            raise ValueError("این پیام پیش‌تر نهایی شده است و از صف خارج است.")
        now = utc_now()
        clean_reason = str(reason or "").strip()[:1000] or None
        await self._execute(
            """
            INSERT INTO editorial_message_decisions(
              message_id,decision,reason,decided_by,decided_at
            ) VALUES (?,'discarded',?,?,?)
            ON CONFLICT(message_id) DO UPDATE SET
              decision='discarded',reason=excluded.reason,
              decided_by=excluded.decided_by,decided_at=excluded.decided_at
            """,
            (message_id, clean_reason, actor, now),
        )
        return {"message_id": message_id, "decision": "discarded", "decided_at": now}

    async def correct_analyzed_message_speaker(
        self,
        message_id: int,
        *,
        speaker_name: str,
        position: str | None,
        actor: str,
    ) -> dict[str, Any]:
        """Replace a single message's inferred speaker after editorial review.

        The operation deliberately replaces, rather than appends to, the tag
        set.  This makes the corrected message disappear from its old bucket
        immediately and ensures it is listed under exactly the chosen speaker.
        """
        name = normalize_persian(str(speaker_name or "")).strip()
        if not name:
            raise ValueError("نام گوینده برای اصلاح لازم است.")
        message = await self.get_message(message_id)
        if not message:
            raise ValueError("پیام پیدا نشد.")
        if str(message.get("analysis_content_type") or "") == "event":
            raise ValueError("رویداد گوینده ندارد و باید به‌صورت مستقل در میز تدوین بررسی شود.")
        old_tag = await self._fetchone(
            """
            SELECT * FROM message_speaker_tags
            WHERE message_id=? ORDER BY sort_order,tag_id LIMIT 1
            """,
            (message_id,),
        )
        if not old_tag:
            raise ValueError("این پیام هنوز تحلیل گوینده ندارد.")
        clean_position = normalize_persian(str(position or "")).strip() or str(
            old_tag.get("position") or "نامشخص"
        )
        person = await self.find_person(name) if name != "نامشخص" else None
        person_id = int(person["person_id"]) if person else None
        specific_topic = str(old_tag.get("specific_topic") or message.get("analysis_main_subject") or "نامشخص")
        general_topic = str(old_tag.get("general_topic") or message.get("analysis_general_topic") or "نامشخص")
        evidence = old_tag.get("evidence")
        expression_type = str(old_tag.get("expression_method_type") or "نامشخص")
        expression_context = str(old_tag.get("expression_method_context") or "نامشخص")
        expression_evidence = old_tag.get("expression_method_evidence")
        location_label = " · ".join(
            value
            for value in (expression_type, expression_context)
            if value and value != "نامشخص"
        ) or None
        topic = await self._fetchone(
            "SELECT topic_id,name FROM topics WHERE canonical_name=? AND active=1 LIMIT 1",
            (canonical_key(general_topic),),
        )
        normalized_tag = {
            "name": name[:240],
            "canonical_speaker": canonical_key(name) or canonical_key("نامشخص"),
            "position": clean_position[:300],
            "specific_topic": normalize_persian(specific_topic).strip()[:500] or "نامشخص",
            "general_topic": normalize_persian(general_topic).strip()[:160] or "نامشخص",
            "evidence": evidence,
            "expression_method_type": expression_type,
            "expression_method_context": expression_context,
            "expression_method_evidence": expression_evidence,
            "person_id": person_id,
        }
        now = utc_now()
        analysis_payload = loads(message.get("ai_analysis_json"), {})
        if not isinstance(analysis_payload, dict):
            analysis_payload = {}
        analysis_payload.update(
            {
                "content_type": "اظهارنظر شخص‌محور",
                "main_subject": normalized_tag["specific_topic"],
                "general_topic": normalized_tag["general_topic"],
                "speakers": [normalized_tag],
                "event": None,
                "prompt_version": "manual-speaker-correction-v1",
            }
        )
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute("DELETE FROM message_speaker_tags WHERE message_id=?", (message_id,))
            cursor = await conn.execute(
                """
                INSERT INTO message_speaker_tags(
                  message_id,speaker_name,canonical_speaker,position,specific_topic,general_topic,
                  evidence,expression_method_type,expression_method_context,expression_method_evidence,
                  person_id,sort_order,provider,model,prompt_version,analyzed_by,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    message_id,
                    normalized_tag["name"],
                    normalized_tag["canonical_speaker"],
                    normalized_tag["position"],
                    normalized_tag["specific_topic"],
                    normalized_tag["general_topic"],
                    normalized_tag["evidence"],
                    normalized_tag["expression_method_type"],
                    normalized_tag["expression_method_context"],
                    normalized_tag["expression_method_evidence"],
                    normalized_tag["person_id"],
                    0,
                    "manual",
                    None,
                    "manual-speaker-correction-v1",
                    actor,
                    now,
                ),
            )
            tag_id = int(cursor.lastrowid)
            await conn.execute(
                """
                UPDATE messages SET detected_person_name=?,detected_person_id=?,person_candidate_id=NULL,
                  person_match_method='editorial_speaker_correction',person_confidence=?,
                  detected_topic_id=?,detected_topic_name=?,topic_confidence=?,statement_type='اظهارنظر',
                  statement_location_type=?,statement_location_label=?,ai_enrichment_status='validated',
                  ai_enrichment_confidence=1,ai_analysis_json=?,analysis_content_type='person_statement',
                  analysis_main_subject=?,analysis_general_topic=?,analysis_event_title=NULL,
                  analysis_event_entities_json=NULL,analysis_event_location=NULL,analysis_event_time=NULL,
                  updated_at=? WHERE id=?
                """,
                (
                    normalized_tag["name"],
                    person_id,
                    1.0 if name != "نامشخص" else 0.0,
                    int(topic["topic_id"]) if topic else None,
                    normalized_tag["general_topic"],
                    1.0 if normalized_tag["general_topic"] != "نامشخص" else 0.0,
                    expression_type,
                    location_label,
                    dumps(analysis_payload),
                    normalized_tag["specific_topic"],
                    normalized_tag["general_topic"],
                    now,
                    message_id,
                ),
            )
            await conn.execute(
                """
                INSERT INTO message_actions(message_id,actor_id,action_type,details_json,created_at)
                VALUES (?,NULL,'analysis_speaker_corrected',?,?)
                """,
                (message_id, dumps({"actor": actor, "speaker_name": name}), now),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return {
            "ok": True,
            "message_id": message_id,
            "tag_id": tag_id,
            "speaker_name": normalized_tag["name"],
            "person_id": person_id,
        }

    async def promote_analyzed_speaker_to_registry(
        self,
        message_id: int,
        tag_id: int,
        *,
        actor: str,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Create or reuse a registry person from an AI speaker tag.

        The transaction deliberately resolves aliases before inserting a person,
        then links every existing tag with the same canonical speaker.  This
        makes the action safe to repeat and prevents duplicate registry rows
        when more than one analyzed message identifies the same person.
        """
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            tag = await (
                await conn.execute(
                    """
                    SELECT st.* FROM message_speaker_tags st
                    WHERE st.tag_id=? AND st.message_id=?
                    """,
                    (tag_id, message_id),
                )
            ).fetchone()
            if not tag:
                raise ValueError("برچسب گوینده برای این پیام پیدا نشد.")

            name = normalize_persian(str(tag["speaker_name"] or "")).strip()
            canonical = canonical_key(name)
            if not canonical or canonical == canonical_key("نامشخص"):
                raise ValueError("گوینده نامشخص را نمی‌توان به شناسنامه افزود.")
            raw_position = normalize_persian(str(tag["position"] or "")).strip()
            position = (
                raw_position
                if raw_position and canonical_key(raw_position) != canonical_key("نامشخص")
                else None
            )
            clean_category = normalize_persian(str(category or "")).strip() or None

            person = await (
                await conn.execute(
                    """
                    SELECT p.* FROM people p
                    LEFT JOIN person_aliases a ON a.person_id=p.person_id
                    WHERE p.merged_into IS NULL
                      AND (p.canonical_name=? OR a.canonical_alias=?)
                    ORDER BY CASE p.registry_status WHEN 'inside' THEN 0 ELSE 1 END,
                             p.active DESC,p.priority,p.person_id
                    LIMIT 1
                    """,
                    (canonical, canonical),
                )
            ).fetchone()
            created = person is None
            if person is None:
                cursor = await conn.execute(
                    """
                    INSERT INTO people(
                      full_name,canonical_name,category,position,registry_status,
                      active,priority,created_at,updated_at
                    ) VALUES (?,?,?,?, 'inside',1,100,?,?)
                    """,
                    (name, canonical, clean_category, position, now, now),
                )
                person_id = int(cursor.lastrowid)
            else:
                person_id = int(person["person_id"])
                await conn.execute(
                    """
                    UPDATE people
                    SET registry_status='inside',active=1,
                        position=CASE WHEN (position IS NULL OR TRIM(position)='' OR position='نامشخص')
                                      AND ? IS NOT NULL THEN ? ELSE position END,
                        category=CASE WHEN (category IS NULL OR TRIM(category)='')
                                      AND ? IS NOT NULL THEN ? ELSE category END,
                        updated_at=?
                    WHERE person_id=?
                    """,
                    (
                        position,
                        position,
                        clean_category,
                        clean_category,
                        now,
                        person_id,
                    ),
                )

            await conn.execute(
                """
                INSERT OR IGNORE INTO person_aliases(
                  person_id,alias_text,canonical_alias,created_at
                ) VALUES (?,?,?,?)
                """,
                (person_id, name, canonical, now),
            )
            linked = await conn.execute(
                """
                UPDATE message_speaker_tags SET person_id=?
                WHERE canonical_speaker=? AND person_id IS NULL
                """,
                (person_id, canonical),
            )
            await conn.execute(
                """
                UPDATE messages
                SET detected_person_id=?,
                    detected_person_name=CASE WHEN detected_person_name IS NULL
                                              OR TRIM(detected_person_name)='' THEN ?
                                              ELSE detected_person_name END,
                    updated_at=?
                WHERE id IN (
                  SELECT message_id FROM message_speaker_tags
                  WHERE canonical_speaker=?
                ) AND detected_person_id IS NULL
                """,
                (person_id, name, now, canonical),
            )
            await conn.execute(
                """
                INSERT INTO person_changes(person_id,action,old_json,new_json,actor,created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    person_id,
                    "promote_analyzed_speaker",
                    dumps({"tag_id": tag_id, "message_id": message_id}),
                    dumps(
                        {
                            "speaker_name": name,
                            "position": position,
                            "category": clean_category,
                            "created": created,
                        }
                    ),
                    actor,
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

        person_data = await self.get_person(person_id)
        return {
            "ok": True,
            "created": created,
            "person_id": person_id,
            "person": person_data,
            "linked_tag_count": int(linked.rowcount or 0),
            "message_id": message_id,
            "tag_id": tag_id,
        }

    async def finalized_topic_trends(
        self,
        *,
        days: int = 14,
        topic_limit: int = 6,
    ) -> dict[str, Any]:
        """Trend finalized editorial news by its approved topic in Tehran time."""
        safe_days = max(7, min(int(days), 31))
        safe_limit = max(1, min(int(topic_limit), 8))
        tehran = ZoneInfo("Asia/Tehran")
        today = datetime.now(tehran).date()
        day_values = [today - timedelta(days=offset) for offset in range(safe_days - 1, -1, -1)]
        day_keys = [value.isoformat() for value in day_values]
        first_day = day_values[0]

        rows = await self._fetchall(
            """
            SELECT draft_id,topic_name,finalized_at
            FROM editorial_drafts
            WHERE status='finalized'
              AND finalized_at IS NOT NULL
              AND topic_name IS NOT NULL
              AND TRIM(topic_name)<>''
            ORDER BY finalized_at DESC
            """
        )
        buckets: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        included = 0
        for row in rows:
            try:
                finalized = datetime.fromisoformat(
                    str(row["finalized_at"]).replace("Z", "+00:00")
                )
                if finalized.tzinfo is None:
                    finalized = finalized.replace(tzinfo=timezone.utc)
                local_day = finalized.astimezone(tehran).date()
            except (TypeError, ValueError):
                continue
            if local_day < first_day or local_day > today:
                continue
            topic = normalize_persian(str(row.get("topic_name") or "")).strip()
            if not topic:
                continue
            key = local_day.isoformat()
            topic_bucket = buckets.setdefault(topic, {})
            topic_bucket[key] = topic_bucket.get(key, 0) + 1
            totals[topic] = totals.get(topic, 0) + 1
            included += 1

        selected_topics = sorted(
            totals,
            key=lambda topic: (-totals[topic], topic),
        )[:safe_limit]
        series = [
            {
                "topic": topic,
                "total": totals[topic],
                "values": [buckets.get(topic, {}).get(day, 0) for day in day_keys],
            }
            for topic in selected_topics
        ]
        return {
            "days": day_keys,
            "series": series,
            "total_finalized": included,
            "window_days": safe_days,
            "timezone": "Asia/Tehran",
        }

    async def dashboard_stats(self) -> dict[str, Any]:
        counts = await self._fetchone(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN ai_enrichment_status='validated' OR EXISTS (
                SELECT 1 FROM message_speaker_tags st WHERE st.message_id=messages.id
              ) THEN 1 ELSE 0 END) AS analyzed,
              SUM(CASE WHEN editorial_rating_half IS NOT NULL THEN 1 ELSE 0 END) AS rated,
              SUM(CASE WHEN primary_source_url IS NULL OR primary_source_url='' THEN 1 ELSE 0 END) AS without_source,
              SUM(CASE WHEN oration_text IS NULL OR oration_text='' THEN 1 ELSE 0 END) AS without_oration,
              SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered
            FROM messages
            """
        ) or {}
        active = await self._fetchone(
            "SELECT COUNT(*) AS c FROM monitored_chats WHERE enabled=1"
        )
        counts["active_sources"] = int((active or {}).get("c") or 0)
        for key in list(counts):
            counts[key] = int(counts[key] or 0)
        by_source = await self._fetchall(
            """
            SELECT COALESCE(source_chat_title,source_chat_username,CAST(source_chat_id AS TEXT)) AS source_name,
              COUNT(*) AS total,
              SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN ai_enrichment_status='validated' OR EXISTS (
                SELECT 1 FROM message_speaker_tags st WHERE st.message_id=messages.id
              ) THEN 1 ELSE 0 END) AS analyzed,
              SUM(CASE WHEN editorial_rating_half IS NOT NULL THEN 1 ELSE 0 END) AS rated
            FROM messages GROUP BY source_chat_id ORDER BY total DESC
            """
        )
        return {
            "counts": counts,
            "by_source": by_source,
            "topic_trends": await self.finalized_topic_trends(),
        }

    async def monitoring_summary(self, *, days: int = 30) -> dict[str, Any]:
        """Aggregate sender activity and editorial scores in Tehran time.

        A sender is intentionally separate from the detected speaker: this
        section measures the people/chats that *submit* messages to the flow.
        """
        safe_days = max(7, min(int(days), 90))
        tehran = ZoneInfo("Asia/Tehran")
        today = datetime.now(tehran).date()
        date_values = [today - timedelta(days=offset) for offset in range(safe_days - 1, -1, -1)]
        day_keys = [value.isoformat() for value in date_values]
        first_day = date_values[0]
        rows = await self._fetchall(
            """
            SELECT id,sender_id,sender_username,sender_name,sender_kind,
              sender_chat_id,sender_chat_username,sender_chat_title,
              source_chat_id,source_chat_username,source_chat_title,
              published_at,received_at,created_at,
              (ai_enrichment_status='validated' OR EXISTS (
                SELECT 1 FROM message_speaker_tags st WHERE st.message_id=messages.id
              )) AS is_analyzed,
              EXISTS (
                SELECT 1 FROM editorial_draft_inputs edi
                JOIN editorial_drafts ed ON ed.draft_id=edi.draft_id
                WHERE edi.message_id=messages.id AND ed.status='finalized'
              ) AS is_finalized
            FROM messages
            ORDER BY id DESC
            """
        )
        buckets: dict[str, dict[str, Any]] = {}
        total_messages = 0
        total_rated = 0
        all_rating_halves: list[int] = []
        for row in rows:
            raw_timestamp = row.get("published_at") or row.get("received_at") or row.get("created_at")
            try:
                parsed = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                local_day = parsed.astimezone(tehran).date()
            except (TypeError, ValueError):
                continue
            if local_day < first_day or local_day > today:
                continue

            is_chat_sender = str(row.get("sender_kind") or "") == "chat" or bool(
                row.get("sender_chat_id") or row.get("sender_chat_title") or row.get("sender_chat_username")
            )
            if is_chat_sender:
                identity = row.get("sender_chat_id") or row.get("source_chat_id")
                key = f"chat:{identity or row.get('sender_chat_username') or row.get('source_chat_username') or 'unknown'}"
                display_name = (
                    row.get("sender_chat_title")
                    or row.get("sender_chat_username")
                    or row.get("source_chat_title")
                    or row.get("source_chat_username")
                    or "گفت‌وگوی ناشناس"
                )
                username = row.get("sender_chat_username") or row.get("source_chat_username")
                sender_type = "گفت‌وگو / کانال"
            else:
                identity = row.get("sender_id")
                key = f"person:{identity or row.get('sender_username') or row.get('sender_name') or 'unknown'}"
                display_name = row.get("sender_name") or row.get("sender_username") or "ارسال‌کننده نامشخص"
                username = row.get("sender_username")
                sender_type = "کارشناس"

            bucket = buckets.setdefault(
                key,
                {
                    "sender_key": key,
                    "sender_name": str(display_name),
                    "sender_username": str(username).lstrip("@") if username else None,
                    "sender_type": sender_type,
                    "total_messages": 0,
                    "rated_messages": 0,
                    "rating_halves": [],
                    "daily": {day: 0 for day in day_keys},
                },
            )
            day_key = local_day.isoformat()
            bucket["total_messages"] += 1
            bucket["daily"][day_key] += 1
            total_messages += 1
            rating, _ = _automatic_editorial_rating(row)
            rating_half = rating * 2
            bucket["rated_messages"] += 1
            bucket["rating_halves"].append(rating_half)
            total_rated += 1
            all_rating_halves.append(rating_half)

        people: list[dict[str, Any]] = []
        daily_totals = {day: 0 for day in day_keys}
        for bucket in buckets.values():
            for day, value in bucket["daily"].items():
                daily_totals[day] += int(value)
            values = bucket.pop("rating_halves")
            bucket["average_rating"] = round(sum(values) / len(values) / 2, 2) if values else None
            people.append(bucket)
        people.sort(
            key=lambda item: (-int(item["total_messages"]), str(item["sender_name"]))
        )
        return {
            "days": day_keys,
            "daily_totals": [{"date": day, "count": daily_totals[day]} for day in day_keys],
            "people": people,
            "total_messages": total_messages,
            "rated_messages": total_rated,
            "average_rating": round(sum(all_rating_halves) / len(all_rating_halves) / 2, 2)
            if all_rating_halves
            else None,
            "window_days": safe_days,
            "timezone": "Asia/Tehran",
        }

    async def monitoring_sender_summary(self, *, days: int = 30) -> dict[str, Any]:
        """Aggregate all known senders while limiting only the chart window.

        A monitoring table answers the historical question "who has sent how
        many messages?".  The former implementation discarded an entire
        sender when their latest activity preceded the selected 14/30/60/90
        day chart window.  This version keeps those sender rows and uses the
        window solely for daily activity and the accompanying window counters.
        """
        safe_days = max(7, min(int(days), 90))
        tehran = ZoneInfo("Asia/Tehran")
        today = datetime.now(tehran).date()
        date_values = [
            today - timedelta(days=offset)
            for offset in range(safe_days - 1, -1, -1)
        ]
        day_keys = [value.isoformat() for value in date_values]
        first_day = date_values[0]
        rows = await self._fetchall(
            """
            SELECT id,sender_id,sender_username,sender_name,sender_kind,
              sender_chat_id,sender_chat_username,sender_chat_title,
              source_chat_id,source_chat_username,source_chat_title,
              published_at,received_at,created_at,raw_message_json,
              (ai_enrichment_status='validated' OR EXISTS (
                SELECT 1 FROM message_speaker_tags st WHERE st.message_id=messages.id
              )) AS is_analyzed,
              EXISTS (
                SELECT 1 FROM editorial_draft_inputs edi
                JOIN editorial_drafts ed ON ed.draft_id=edi.draft_id
                WHERE edi.message_id=messages.id AND ed.status='finalized'
              ) AS is_finalized
            FROM messages
            ORDER BY id DESC
            """
        )
        buckets: dict[str, dict[str, Any]] = {}
        total_messages = total_rated = 0
        window_messages = window_rated = 0
        all_rating_halves: list[int] = []

        for row in rows:
            # Sender columns were added after some installations already had
            # messages. Recover a legacy identity from raw Bale data when the
            # columns are empty instead of omitting it from monitoring.
            raw_message = loads(row.get("raw_message_json"), {}) or {}
            if (
                isinstance(raw_message, dict)
                and not isinstance(raw_message.get("chat"), dict)
                and isinstance(raw_message.get("message"), dict)
            ):
                raw_message = raw_message["message"]
            fallback = (
                _sender_identity(raw_message)
                if isinstance(raw_message, dict)
                else {}
            )
            sender_kind = str(
                row.get("sender_kind") or fallback.get("kind") or ""
            ).lower()
            sender_chat_id = row.get("sender_chat_id") or fallback.get("sender_chat_id")
            sender_chat_username = row.get("sender_chat_username") or fallback.get("sender_chat_username")
            sender_chat_title = row.get("sender_chat_title") or fallback.get("sender_chat_title")
            sender_id = row.get("sender_id") or fallback.get("sender_id")
            sender_username = row.get("sender_username") or fallback.get("sender_username")
            sender_name = row.get("sender_name") or fallback.get("sender_name")

            is_chat_sender = sender_kind == "chat" or bool(
                sender_chat_id or sender_chat_username or sender_chat_title
            )
            if is_chat_sender:
                identity = sender_chat_id or row.get("source_chat_id")
                key = (
                    f"chat:{identity or sender_chat_username or row.get('source_chat_username') or 'unknown'}"
                )
                display_name = (
                    sender_chat_title
                    or sender_chat_username
                    or row.get("source_chat_title")
                    or row.get("source_chat_username")
                    or "گفت‌وگوی ناشناس"
                )
                username = sender_chat_username or row.get("source_chat_username")
                sender_type = "گفت‌وگو / کانال"
            else:
                identity = sender_id
                key = (
                    f"person:{identity or sender_username or sender_name or row.get('source_chat_id') or row.get('id')}"
                )
                display_name = sender_name or sender_username or "ارسال‌کننده نامشخص"
                username = sender_username
                sender_type = "کارشناس"

            bucket = buckets.setdefault(
                key,
                {
                    "sender_key": key,
                    "sender_name": str(display_name),
                    "sender_username": str(username).lstrip("@") if username else None,
                    "sender_type": sender_type,
                    "total_messages": 0,
                    "rated_messages": 0,
                    "window_messages": 0,
                    "window_rated_messages": 0,
                    "rating_halves": [],
                    "daily": {day: 0 for day in day_keys},
                },
            )
            bucket["total_messages"] += 1
            total_messages += 1

            raw_timestamp = (
                row.get("published_at")
                or row.get("received_at")
                or row.get("created_at")
            )
            local_day = None
            try:
                parsed = datetime.fromisoformat(
                    str(raw_timestamp).replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                local_day = parsed.astimezone(tehran).date()
            except (TypeError, ValueError):
                pass
            in_window = bool(
                local_day is not None and first_day <= local_day <= today
            )
            if in_window:
                day_key = local_day.isoformat()
                bucket["daily"][day_key] += 1
                bucket["window_messages"] += 1
                window_messages += 1

            rating, _ = _automatic_editorial_rating(row)
            rating_half = rating * 2
            bucket["rated_messages"] += 1
            bucket["rating_halves"].append(rating_half)
            total_rated += 1
            all_rating_halves.append(rating_half)
            if in_window:
                bucket["window_rated_messages"] += 1
                window_rated += 1

        people: list[dict[str, Any]] = []
        daily_totals = {day: 0 for day in day_keys}
        profiles = await self._fetchall(
            """
            SELECT user_id,full_name,username,role,active,sender_key
            FROM admin_users WHERE sender_key IS NOT NULL AND TRIM(sender_key)<>''
            """
        )
        profile_by_sender = {
            str(row["sender_key"]): row for row in profiles if row.get("sender_key")
        }
        for bucket in buckets.values():
            for day, count in bucket["daily"].items():
                daily_totals[day] += int(count)
            values = bucket.pop("rating_halves")
            bucket["average_rating"] = (
                round(sum(values) / len(values) / 2, 2) if values else None
            )
            profile = profile_by_sender.get(str(bucket.get("sender_key") or ""))
            if profile:
                bucket["profile_user_id"] = int(profile["user_id"])
                bucket["profile_full_name"] = str(profile["full_name"])
                bucket["profile_username"] = str(profile["username"])
                bucket["profile_role"] = str(profile["role"])
                bucket["profile_active"] = int(profile["active"] or 0)
            else:
                bucket["profile_user_id"] = None
                bucket["profile_full_name"] = None
                bucket["profile_username"] = None
                bucket["profile_role"] = None
                bucket["profile_active"] = None
            people.append(bucket)
        people.sort(
            key=lambda item: (-int(item["total_messages"]), str(item["sender_name"]))
        )
        return {
            "days": day_keys,
            "daily_totals": [
                {"date": day, "count": daily_totals[day]} for day in day_keys
            ],
            "people": people,
            "total_messages": total_messages,
            "rated_messages": total_rated,
            "window_messages": window_messages,
            "window_rated_messages": window_rated,
            "average_rating": (
                round(sum(all_rating_halves) / len(all_rating_halves) / 2, 2)
                if all_rating_halves
                else None
            ),
            "window_days": safe_days,
            "timezone": "Asia/Tehran",
        }

    async def garaye_insights(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Build Garaye from finalized editorial news only.

        Using ``editorial_drafts`` makes the word cloud, speaker trend and
        subject trend describe material that has passed the editorial desk,
        rather than every message that happened to be received in the period.
        All date boundaries and bucketing use Tehran time.
        """
        tehran = ZoneInfo("Asia/Tehran")
        today = datetime.now(tehran).date()
        safe_days = max(1, min(int(days or 30), 3660))

        def parse_day(value: str | None) -> Any:
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(tehran).date()
            except (TypeError, ValueError):
                return None

        start_day = parse_day(date_from) or (today - timedelta(days=safe_days - 1))
        end_day = parse_day(date_to) or today
        if end_day < start_day:
            start_day, end_day = end_day, start_day

        rows = await self._fetchall(
            """
            SELECT draft_id,person_name,topic_name,main_subject,base_text,
              summary_paragraph,summary_sentence,summary_title,detail,
              finalized_at
            FROM editorial_drafts
            WHERE status='finalized' AND finalized_at IS NOT NULL
            ORDER BY finalized_at DESC,draft_id DESC
            """
        )
        selected: dict[int, tuple[dict[str, Any], Any]] = {}
        daily_finalized: Counter[str] = Counter()
        for row in rows:
            local_day = parse_day(row.get("finalized_at"))
            if local_day is None or local_day < start_day or local_day > end_day:
                continue
            selected[int(row["draft_id"])] = (row, local_day)
            daily_finalized[local_day.isoformat()] += 1

        word_counts: Counter[str] = Counter()
        word_days: dict[str, Counter[str]] = {}
        topic_counts: Counter[str] = Counter()
        speaker_counts: Counter[str] = Counter()
        topic_days: dict[str, Counter[str]] = {}
        speaker_days: dict[str, Counter[str]] = {}

        def add_words(value: Any, day: str, weight: int) -> None:
            safe_weight = max(0, int(weight or 0))
            for word in content_words(value):
                word_counts[word] += safe_weight
                word_days.setdefault(word, Counter())[day] += safe_weight

        for _, (row, local_day) in selected.items():
            day_key = local_day.isoformat()
            add_words(row.get("base_text"), day_key, WORD_CLOUD_WEIGHTS["message"])
            add_words(row.get("summary_paragraph"), day_key, 3)
            add_words(row.get("summary_sentence"), day_key, 3)
            add_words(row.get("summary_title"), day_key, 4)
            add_words(row.get("detail"), day_key, 3)
            add_words(row.get("main_subject"), day_key, WORD_CLOUD_WEIGHTS["specific_topic"])
            add_words(row.get("topic_name"), day_key, WORD_CLOUD_WEIGHTS["general_topic"])
            add_words(row.get("person_name"), day_key, WORD_CLOUD_WEIGHTS["speaker"])

            topic = normalize_persian(str(row.get("topic_name") or "")).strip()
            speaker = normalize_persian(str(row.get("person_name") or "")).strip()
            if topic and topic != "نامشخص":
                topic_counts[topic] += 1
                topic_days.setdefault(topic, Counter())[day_key] += 1
            if speaker and speaker != "نامشخص":
                speaker_counts[speaker] += 1
                speaker_days.setdefault(speaker, Counter())[day_key] += 1

        active_days = sorted(daily_finalized)

        def ranked(
            totals: Counter[str], buckets: dict[str, Counter[str]], limit: int
        ) -> list[dict[str, Any]]:
            return [
                {
                    "name": name,
                    "count": int(totals[name]),
                    "series": [
                        {"date": day, "count": int(buckets.get(name, {}).get(day, 0))}
                        for day in active_days
                        if int(buckets.get(name, {}).get(day, 0))
                    ],
                }
                for name in sorted(totals, key=lambda value: (-totals[value], value))[:limit]
            ]

        attention_rows = await self._fetchall(
            """
            SELECT r.source_day,r.finalized_at,i.source_draft_ids_json
            FROM high_attention_items i
            JOIN high_attention_runs r ON r.high_attention_run_id=i.high_attention_run_id
            WHERE r.status='finalized'
            ORDER BY r.finalized_at DESC,i.sort_order
            """
        )
        all_final_drafts = await self._fetchall(
            """
            SELECT draft_id,main_subject,topic_name
            FROM editorial_drafts
            WHERE status='finalized'
            """
        )
        subject_by_draft = {
            int(row["draft_id"]): normalize_persian(
                str(row.get("main_subject") or row.get("topic_name") or "")
            ).strip()
            for row in all_final_drafts
        }
        attention_subject_counts: Counter[str] = Counter()
        attention_subject_days: dict[str, Counter[str]] = {}
        for row in attention_rows:
            local_day = parse_day(row.get("source_day")) or parse_day(row.get("finalized_at"))
            if local_day is None or local_day < start_day or local_day > end_day:
                continue
            day_key = local_day.isoformat()
            source_ids = {
                int(value)
                for value in (loads(row.get("source_draft_ids_json"), []) or [])
                if str(value).isdigit()
            }
            # Count each selected subject once per finalized high-attention item.
            for subject in {
                subject_by_draft.get(draft_id, "") for draft_id in source_ids
            }:
                if not subject or subject == "نامشخص":
                    continue
                attention_subject_counts[subject] += 1
                attention_subject_days.setdefault(subject, Counter())[day_key] += 1

        word_trends = ranked(word_counts, word_days, 24)

        return {
            "range": {
                "date_from": start_day.isoformat(),
                "date_to": end_day.isoformat(),
                "finalized_count": len(selected),
                "active_days": len(active_days),
                "timezone": "Asia/Tehran",
            },
            "daily_finalized": [
                {"date": day, "count": int(daily_finalized[day])}
                for day in active_days
            ],
            "word_cloud": ranked_word_cloud(word_counts, limit=42),
            "word_trends": word_trends,
            "topic_chart": {
                "days": active_days,
                "series": [
                    {
                        "topic": item["name"],
                        "total": item["count"],
                        "values": [
                            int(topic_days.get(item["name"], {}).get(day, 0))
                            for day in active_days
                        ],
                    }
                    for item in ranked(topic_counts, topic_days, 8)
                ],
                "total_finalized": len(selected),
                "window_days": (end_day - start_day).days + 1,
                "timezone": "Asia/Tehran",
            },
            "speaker_trends": ranked(speaker_counts, speaker_days, 10),
            "high_attention": {
                "subjects": ranked(
                    attention_subject_counts, attention_subject_days, 10
                ),
            },
        }

    async def store_update(self, update: dict[str, Any]) -> bool:
        if update.get("update_id") is None:
            return True
        update_id = int(update["update_id"])
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            current = await (
                await conn.execute(
                    "SELECT status,attempts FROM bot_updates WHERE update_id=?",
                    (update_id,),
                )
            ).fetchone()
            if current and str(current["status"]) == "processed":
                await conn.rollback()
                return False
            if current:
                await conn.execute(
                    """
                    UPDATE bot_updates SET payload_json=?,status='processing',
                    attempts=attempts+1,error_text=NULL WHERE update_id=?
                    """,
                    (dumps(update), update_id),
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO bot_updates(update_id,payload_json,status,attempts,received_at)
                    VALUES (?,?,'processing',1,?)
                    """,
                    (update_id, dumps(update), now),
                )
            await conn.commit()
            return True
        finally:
            await conn.close()

    async def mark_update_processed(self, update_id: int) -> None:
        await self._execute(
            """
            UPDATE bot_updates SET status='processed',processed_at=?,error_text=NULL
            WHERE update_id=?
            """,
            (utc_now(), update_id),
        )

    async def mark_update_failed(self, update_id: int, error_text: str) -> None:
        await self._execute(
            "UPDATE bot_updates SET status='failed',error_text=? WHERE update_id=?",
            (error_text[:4000], update_id),
        )

    async def get_update_attempts(self, update_id: int) -> int:
        row = await self._fetchone(
            "SELECT attempts FROM bot_updates WHERE update_id=?", (update_id,)
        )
        return int((row or {}).get("attempts") or 0)

    async def list_bot_updates_for_recovery(self, *, received_since: str) -> list[dict[str, Any]]:
        """Return the locally retained raw Bot API updates for a bounded replay.

        This is intentionally an archive of updates that the bot had already
        received; it is not a crawler and never attempts to discover channel
        history outside the Bot API queue.
        """
        rows = await self._fetchall(
            """
            SELECT update_id,payload_json,status,attempts,error_text,received_at,processed_at
            FROM bot_updates
            WHERE received_at >= ?
            ORDER BY update_id ASC
            """,
            (str(received_since),),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = loads(row.get("payload_json"), {})
            if not isinstance(payload, dict):
                continue
            row["payload"] = payload
            result.append(row)
        return result

    async def mark_crawler_recovery_running(self, run_id: int) -> None:
        now = utc_now()
        await self._execute(
            """
            UPDATE crawler_recovery_runs
            SET status='running',started_at=COALESCE(started_at,?),updated_at=?,error_text=NULL
            WHERE run_id=?
            """,
            (now, now, int(run_id)),
        )

    async def finish_crawler_recovery_run(
        self,
        run_id: int,
        *,
        status: str,
        scanned: int,
        existing: int,
        imported: int,
        failed: int,
        details: dict[str, Any] | None = None,
        error_text: str | None = None,
    ) -> None:
        """Persist one operator-visible bot-queue recovery result."""
        now = utc_now()
        await self._execute(
            """
            UPDATE crawler_recovery_runs
            SET status=?,scanned_count=?,existing_count=?,imported_count=?,failed_count=?,
                details_json=?,error_text=?,completed_at=?,updated_at=?
            WHERE run_id=?
            """,
            (
                str(status), int(scanned), int(existing), int(imported), int(failed),
                dumps(details or {}), error_text, now, now, int(run_id),
            ),
        )

    async def create_interaction_request(
        self,
        *,
        message_id: int,
        kind: str,
        requested_by: int,
        requested_by_name: str | None,
        source_chat_id: int,
        target_message_id: int,
        timeout_seconds: int,
    ) -> int:
        if kind not in {"source", "oration"}:
            raise ValueError("نوع تعامل نامعتبر است.")
        now = datetime.now(timezone.utc)
        return await self._execute(
            """
            INSERT INTO interaction_requests(
              message_id,kind,requested_by,requested_by_name,source_chat_id,target_message_id,
              expires_at,created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                kind,
                requested_by,
                requested_by_name,
                source_chat_id,
                target_message_id,
                (now + timedelta(seconds=max(60, timeout_seconds))).isoformat(),
                now.isoformat(),
            ),
        )

    async def attach_interaction_prompt(
        self, request_id: int, prompt_chat_id: int, prompt_message_id: int
    ) -> None:
        await self._execute(
            """
            UPDATE interaction_requests SET prompt_chat_id=?,prompt_message_id=?
            WHERE id=? AND status='pending'
            """,
            (prompt_chat_id, prompt_message_id, request_id),
        )

    async def get_interaction_request(self, request_id: int) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM interaction_requests WHERE id=?", (request_id,)
        )

    async def get_interaction_by_prompt(
        self, prompt_chat_id: int, prompt_message_id: int
    ) -> dict[str, Any] | None:
        return await self._fetchone(
            """
            SELECT * FROM interaction_requests
            WHERE prompt_chat_id=? AND prompt_message_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (prompt_chat_id, prompt_message_id),
        )

    async def is_interaction_prompt(self, chat_id: int, message_id: int) -> bool:
        return bool(await self.get_interaction_by_prompt(chat_id, message_id))

    async def cancel_interaction(self, request_id: int, reason: str) -> None:
        await self._execute(
            """
            UPDATE interaction_requests SET status='cancelled',result_text=?,completed_at=?
            WHERE id=? AND status='pending'
            """,
            (reason[:1000], utc_now(), request_id),
        )

    async def complete_source_interaction(
        self,
        request_id: int,
        *,
        url: str,
        qr_path: str | None,
        qr_sha256: str | None,
        submitted_by: int,
        submitted_by_name: str | None,
        response_chat_id: int,
        response_message_id: int,
    ) -> tuple[bool, dict[str, Any] | None]:
        request = await self.get_interaction_request(request_id)
        if not request or request.get("status") != "pending":
            return False, request
        await self.add_source(
            int(request["message_id"]),
            url,
            submitted_by,
            qr_path=qr_path,
            qr_sha256=qr_sha256,
        )
        await self._execute(
            """
            UPDATE interaction_requests SET status='completed',result_text=?,
            response_chat_id=?,response_message_id=?,completed_at=?
            WHERE id=? AND status='pending'
            """,
            (url, response_chat_id, response_message_id, utc_now(), request_id),
        )
        return True, await self.get_interaction_request(request_id)

    async def complete_oration_interaction(
        self,
        request_id: int,
        *,
        text: str,
        submitted_by: int,
        submitted_by_name: str | None,
        response_chat_id: int,
        response_message_id: int,
    ) -> tuple[bool, dict[str, Any] | None]:
        request = await self.get_interaction_request(request_id)
        if not request or request.get("status") != "pending":
            return False, request
        await self.add_oration(int(request["message_id"]), text, submitted_by)
        await self._execute(
            """
            UPDATE interaction_requests SET status='completed',result_text=?,
            response_chat_id=?,response_message_id=?,completed_at=?
            WHERE id=? AND status='pending'
            """,
            (text, response_chat_id, response_message_id, utc_now(), request_id),
        )
        return True, await self.get_interaction_request(request_id)

    async def collect_interaction_artifacts_for_cleanup(self) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT prompt_chat_id,prompt_message_id,response_chat_id,response_message_id
            FROM interaction_requests
            WHERE prompt_message_id IS NOT NULL OR response_message_id IS NOT NULL
            """
        )

    async def _set_pending(
        self, user_id: int, kind: str, message_id: int, timeout_seconds: int
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._execute(
            """
            INSERT INTO pending_inputs(user_id,kind,message_id,expires_at,created_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id,kind) DO UPDATE SET
              message_id=excluded.message_id,expires_at=excluded.expires_at,created_at=excluded.created_at
            """,
            (
                user_id,
                kind,
                message_id,
                (now + timedelta(seconds=max(60, timeout_seconds))).isoformat(),
                now.isoformat(),
            ),
        )

    async def set_pending_source(
        self, user_id: int, message_id: int, timeout_seconds: int
    ) -> None:
        await self._set_pending(user_id, "source", message_id, timeout_seconds)

    async def set_pending_oration(
        self, user_id: int, message_id: int, timeout_seconds: int
    ) -> None:
        await self._set_pending(user_id, "oration", message_id, timeout_seconds)

    async def _get_pending(self, user_id: int, kind: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM pending_inputs WHERE user_id=? AND kind=?",
            (user_id, kind),
        )
        if not row:
            return None
        try:
            expired = datetime.fromisoformat(str(row["expires_at"])).astimezone(timezone.utc)
        except ValueError:
            expired = datetime.min.replace(tzinfo=timezone.utc)
        if expired < datetime.now(timezone.utc):
            await self._execute(
                "DELETE FROM pending_inputs WHERE user_id=? AND kind=?",
                (user_id, kind),
            )
            return None
        return row

    async def get_pending_source(self, user_id: int) -> dict[str, Any] | None:
        return await self._get_pending(user_id, "source")

    async def get_pending_oration(self, user_id: int) -> dict[str, Any] | None:
        return await self._get_pending(user_id, "oration")

    async def clear_pending_source(self, user_id: int) -> None:
        await self._execute(
            "DELETE FROM pending_inputs WHERE user_id=? AND kind='source'", (user_id,)
        )

    async def clear_pending_oration(self, user_id: int) -> None:
        await self._execute(
            "DELETE FROM pending_inputs WHERE user_id=? AND kind='oration'", (user_id,)
        )

    async def clear_pending_inputs(self, user_id: int) -> None:
        await self._execute("DELETE FROM pending_inputs WHERE user_id=?", (user_id,))

    async def add_system_event(
        self,
        component: str,
        level: str,
        event_type: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO system_events(component,level,event_type,message,details_json,created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                component,
                level,
                event_type,
                message,
                dumps(details or {}),
                utc_now(),
            ),
        )

    async def list_system_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM system_events ORDER BY id DESC LIMIT ?", (max(1, limit),)
        )

    async def create_crawler_recovery_run(
        self, *, hours: int = 48, requested_by: str | None = None
    ) -> int:
        """Queue a bounded Bot API recovery and keep its operator-visible audit row."""
        safe_hours = min(168, max(1, int(hours)))
        now = utc_now()
        return await self._execute(
            """
            INSERT INTO crawler_recovery_runs(
              hours,status,requested_by,created_at,updated_at
            ) VALUES (?, 'queued', ?, ?, ?)
            """,
            (safe_hours, requested_by, now, now),
        )

    async def latest_crawler_recovery_run(self) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM crawler_recovery_runs ORDER BY run_id DESC LIMIT 1"
        )
        if row:
            row["details"] = loads(row.pop("details_json", None), {}) or {}
        return row

    async def add_dashboard_audit(
        self,
        actor: str,
        action: str,
        *,
        object_type: str | None = None,
        object_id: str | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO dashboard_audit(actor,action,object_type,object_id,ip_address,details_json,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                actor,
                action,
                object_type,
                object_id,
                ip_address,
                dumps(details or {}),
                utc_now(),
            ),
        )

    async def list_dashboard_audit(self, limit: int = 500) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM dashboard_audit ORDER BY id DESC LIMIT ?", (max(1, limit),)
        )

    async def health_summary(self) -> dict[str, Any]:
        required = {
            "settings",
            "monitored_chats",
            "messages",
            "people",
            "person_categories",
            "topics",
            "bulletin_runs",
            "bulletin_items",
            "editorial_drafts",
            "admin_users",
            "admin_sessions",
        }
        conn = await self._connect()
        try:
            tables = {
                str(row["name"])
                for row in await (
                    await conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ).fetchall()
            }
            quick = await (await conn.execute("PRAGMA quick_check")).fetchone()
            size = self.path.stat().st_size if self.path.exists() else 0
            return {
                "path": str(self.path),
                "quick_check": str(quick[0] if quick else "unknown"),
                "missing_tables": sorted(required - tables),
                "table_count": len(tables),
                "size_bytes": size,
                "wal_enabled": True,
            }
        finally:
            await conn.close()

    async def save_person(
        self,
        *,
        full_name: str,
        person_id: int | None = None,
        category: str | None = None,
        position: str | None = None,
        organization: str | None = None,
        registry_status: str = "inside",
        active: bool = True,
        aliases: list[str] | None = None,
        priority: int = 100,
        replace_aliases: bool = True,
    ) -> int:
        name = normalize_persian(full_name)
        clean_category = re.sub(r"\s+", " ", str(category or "")).strip() or None
        canonical = canonical_key(name)
        if not canonical:
            raise ValueError("نام شخص خالی است.")
        if registry_status not in {"inside", "outside"}:
            raise ValueError("وضعیت شناسنامه نامعتبر است.")
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            if person_id is None:
                existing = await (
                    await conn.execute(
                        "SELECT person_id FROM people WHERE canonical_name=? AND merged_into IS NULL",
                        (canonical,),
                    )
                ).fetchone()
                if existing:
                    person_id = int(existing["person_id"])
            if person_id is None:
                cursor = await conn.execute(
                    """
                    INSERT INTO people(full_name,canonical_name,category,position,organization,
                    registry_status,active,priority,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        name,
                        canonical,
                        clean_category,
                        position,
                        organization,
                        registry_status,
                        int(active),
                        priority,
                        now,
                        now,
                    ),
                )
                person_id = int(cursor.lastrowid)
            else:
                await conn.execute(
                    """
                    UPDATE people SET full_name=?,canonical_name=?,category=?,position=?,
                    organization=COALESCE(?,organization),registry_status=?,active=?,priority=?,updated_at=?
                    WHERE person_id=?
                    """,
                    (
                        name,
                        canonical,
                        clean_category,
                        position,
                        organization,
                        registry_status,
                        int(active),
                        priority,
                        now,
                        person_id,
                    ),
                )
            if replace_aliases:
                await conn.execute(
                    "DELETE FROM person_aliases WHERE person_id=?", (person_id,)
                )
            alias_values = [name, *(aliases or [])]
            for alias in alias_values:
                clean_alias = normalize_persian(alias)
                canonical_alias = canonical_key(clean_alias)
                if canonical_alias:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO person_aliases(person_id,alias_text,canonical_alias,created_at)
                        VALUES (?,?,?,?)
                        """,
                        (person_id, clean_alias, canonical_alias, now),
                    )
            if clean_category:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO person_categories(title,created_by,created_at,updated_at)
                    VALUES (?,?,?,?)
                    """,
                    (clean_category, "person_saved", now, now),
                )
            await conn.commit()
            return int(person_id)
        except aiosqlite.IntegrityError as exc:
            await conn.rollback()
            raise ValueError("نام یا نام مستعار با شخص دیگری تداخل دارد.") from exc
        finally:
            await conn.close()

    async def add_person_channel(
        self,
        person_id: int,
        *,
        chat_id: int | None = None,
        username: str | None = None,
    ) -> int:
        if chat_id is None and not username:
            raise ValueError("chat_id یا username لازم است.")
        clean = str(username or "").strip().lstrip("@") or None
        return await self._execute(
            """
            INSERT INTO person_channels(person_id,chat_id,username,username_norm,created_at)
            VALUES (?,?,?,?,?)
            """,
            (person_id, chat_id, clean, clean.lower() if clean else None, utc_now()),
        )

    async def save_person_change(
        self,
        person_id: int,
        *,
        action: str,
        old: dict[str, Any],
        new: dict[str, Any],
        actor: str,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO person_changes(person_id,action,old_json,new_json,actor,created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (person_id, action, dumps(old), dumps(new), actor, utc_now()),
        )

    async def list_people(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM person_channels pc WHERE pc.person_id=p.person_id) AS channel_count,
              (SELECT GROUP_CONCAT(pa.alias_text,' | ') FROM person_aliases pa
               WHERE pa.person_id=p.person_id AND pa.canonical_alias<>p.canonical_name) AS aliases,
              (SELECT COUNT(*) FROM messages m WHERE m.detected_person_id=p.person_id) AS news_count
            FROM people p WHERE p.merged_into IS NULL
            ORDER BY p.active DESC,p.priority,p.full_name
            """
        )
        return rows

    async def get_person(self, person_id: int) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM people WHERE person_id=?", (person_id,)
        )
        if not row:
            return None
        row["alias_rows"] = await self._fetchall(
            "SELECT * FROM person_aliases WHERE person_id=? ORDER BY alias_text",
            (person_id,),
        )
        row["channels"] = await self._fetchall(
            "SELECT * FROM person_channels WHERE person_id=? ORDER BY id", (person_id,)
        )
        row["news"] = await self._fetchall(
            """
            SELECT id,text,caption,published_at,status,detected_topic_name
            FROM messages WHERE detected_person_id=? ORDER BY published_at DESC LIMIT 100
            """,
            (person_id,),
        )
        return row

    async def find_people_by_name(self, name: str | None) -> list[dict[str, Any]]:
        """Return every active person whose canonical name or alias matches."""

        key = canonical_key(name)
        if not key:
            return []
        return await self._fetchall(
            """
            SELECT DISTINCT p.* FROM person_aliases a
            JOIN people p ON p.person_id=a.person_id
            WHERE a.canonical_alias=? AND p.active=1 AND p.merged_into IS NULL
            ORDER BY CASE p.registry_status WHEN 'inside' THEN 0 ELSE 1 END,p.priority,p.full_name
            """,
            (key,),
        )

    async def find_unique_person(self, name: str | None) -> dict[str, Any] | None:
        """Return a person only when the name/alias match is unique and certain."""

        matches = await self.find_people_by_name(name)
        if len(matches) != 1:
            return None
        return matches[0]

    async def find_person(self, name: str | None) -> dict[str, Any] | None:
        # Keep the historical helper name, but only auto-bind unique matches so
        # ambiguous aliases never invent or force a person identity.
        return await self.find_unique_person(name)

    async def list_person_categories(self) -> list[str]:
        rows = await self._fetchall(
            """
            SELECT title AS category FROM person_categories
            UNION
            SELECT DISTINCT TRIM(category) AS category
            FROM people
            WHERE merged_into IS NULL AND active=1
              AND category IS NOT NULL AND TRIM(category)<>''
            ORDER BY category COLLATE NOCASE
            """
        )
        return [str(row["category"]) for row in rows if row.get("category")]

    async def create_person_category(self, *, title: str, actor: str) -> dict[str, Any]:
        category = re.sub(r"\s+", " ", str(title or "")).strip()
        if not category:
            raise ValueError("عنوان دسته الزامی است.")
        if len(category) > 120:
            raise ValueError("عنوان دسته حداکثر ۱۲۰ نویسه است.")
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            existing = await (
                await conn.execute(
                    "SELECT category_id,title FROM person_categories WHERE title=? COLLATE NOCASE",
                    (category,),
                )
            ).fetchone()
            if existing:
                await conn.commit()
                return {"category_id": int(existing["category_id"]), "title": str(existing["title"]), "created": False}
            cursor = await conn.execute(
                """
                INSERT INTO person_categories(title,created_by,created_at,updated_at)
                VALUES (?,?,?,?)
                """,
                (category, actor, now, now),
            )
            category_id = int(cursor.lastrowid)
            await conn.execute(
                """
                INSERT INTO system_events(component,level,event_type,message,details_json,created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    "people", "info", "category_created", f"دسته «{category}» ایجاد شد.",
                    dumps({"actor": actor, "category_id": category_id, "title": category}), now,
                ),
            )
            await conn.commit()
            return {"category_id": category_id, "title": category, "created": True}
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def rename_person_category(
        self,
        *,
        old_category: str,
        new_category: str,
        actor: str,
    ) -> dict[str, Any]:
        old_name = re.sub(r"\s+", " ", str(old_category or "")).strip()
        new_name = re.sub(r"\s+", " ", str(new_category or "")).strip()
        if not old_name or not new_name:
            raise ValueError("نام قدیم و جدید دسته الزامی است.")
        if old_name == new_name:
            return {"updated_people": 0, "updated_drafts": 0, "old_category": old_name, "new_category": new_name}
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            old_category_row = await (
                await conn.execute(
                    "SELECT category_id FROM person_categories WHERE title=? COLLATE NOCASE",
                    (old_name,),
                )
            ).fetchone()
            new_category_row = await (
                await conn.execute(
                    "SELECT category_id FROM person_categories WHERE title=? COLLATE NOCASE",
                    (new_name,),
                )
            ).fetchone()
            if old_category_row and new_category_row:
                await conn.execute(
                    "DELETE FROM person_categories WHERE category_id=?",
                    (int(old_category_row["category_id"]),),
                )
            elif old_category_row:
                await conn.execute(
                    "UPDATE person_categories SET title=?,updated_at=? WHERE category_id=?",
                    (new_name, now, int(old_category_row["category_id"])),
                )
            elif not new_category_row:
                await conn.execute(
                    "INSERT INTO person_categories(title,created_by,created_at,updated_at) VALUES (?,?,?,?)",
                    (new_name, actor, now, now),
                )
            people_cursor = await conn.execute(
                """
                UPDATE people SET category=?, updated_at=?
                WHERE merged_into IS NULL AND TRIM(COALESCE(category,''))=?
                """,
                (new_name, now, old_name),
            )
            drafts_cursor = await conn.execute(
                """
                UPDATE editorial_drafts SET category_name=?, updated_at=?
                WHERE TRIM(COALESCE(category_name,''))=?
                """,
                (new_name, now, old_name),
            )
            await conn.execute(
                """
                INSERT INTO system_events(component,level,event_type,message,details_json,created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    "people",
                    "info",
                    "category_renamed",
                    f"دسته «{old_name}» به «{new_name}» تغییر کرد.",
                    dumps(
                        {
                            "actor": actor,
                            "old_category": old_name,
                            "new_category": new_name,
                            "updated_people": int(people_cursor.rowcount or 0),
                            "updated_drafts": int(drafts_cursor.rowcount or 0),
                        }
                    ),
                    now,
                ),
            )
            await conn.commit()
            return {
                "updated_people": int(people_cursor.rowcount or 0),
                "updated_drafts": int(drafts_cursor.rowcount or 0),
                "old_category": old_name,
                "new_category": new_name,
            }
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def delete_person(self, person_id: int, *, actor: str) -> None:
        person = await self.get_person(person_id)
        if not person:
            raise ValueError("شخص پیدا نشد.")
        if person.get("merged_into") is not None:
            raise ValueError("این شخص قبلاً ادغام شده است.")
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                """
                UPDATE people SET active=0, updated_at=? WHERE person_id=?
                """,
                (now, person_id),
            )
            await conn.execute(
                """
                UPDATE message_speaker_tags SET person_id=NULL
                WHERE person_id=?
                """,
                (person_id,),
            )
            await conn.execute(
                """
                UPDATE messages SET detected_person_id=NULL
                WHERE detected_person_id=?
                """,
                (person_id,),
            )
            await conn.execute(
                """
                INSERT INTO person_changes(person_id,action,old_json,new_json,actor,created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    person_id,
                    "delete",
                    dumps({"full_name": person.get("full_name"), "active": person.get("active")}),
                    dumps({"active": 0}),
                    actor,
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def merge_people(
        self, source_person_id: int, target_person_id: int, *, actor: str
    ) -> None:
        if source_person_id == target_person_id:
            raise ValueError("شخص مبدأ و مقصد یکسان هستند.")
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            source = await (
                await conn.execute(
                    "SELECT * FROM people WHERE person_id=?", (source_person_id,)
                )
            ).fetchone()
            target = await (
                await conn.execute(
                    "SELECT * FROM people WHERE person_id=?", (target_person_id,)
                )
            ).fetchone()
            if not source or not target:
                raise ValueError("شخص مبدأ یا مقصد پیدا نشد.")
            aliases = await (
                await conn.execute(
                    "SELECT alias_text,canonical_alias FROM person_aliases WHERE person_id=?",
                    (source_person_id,),
                )
            ).fetchall()
            for alias in aliases:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO person_aliases(person_id,alias_text,canonical_alias,created_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        target_person_id,
                        alias["alias_text"],
                        alias["canonical_alias"],
                        utc_now(),
                    ),
                )
            await conn.execute(
                "UPDATE messages SET detected_person_id=? WHERE detected_person_id=?",
                (target_person_id, source_person_id),
            )
            await conn.execute(
                """
                UPDATE message_speaker_tags
                SET person_id=?,speaker_name=?,canonical_speaker=?
                WHERE person_id=?
                """,
                (
                    target_person_id,
                    target["full_name"],
                    target["canonical_name"],
                    source_person_id,
                ),
            )
            await conn.execute(
                "UPDATE bulletin_items SET person_id=?,person_name=? WHERE person_id=?",
                (target_person_id, target["full_name"], source_person_id),
            )
            await conn.execute(
                """
                UPDATE people SET active=0,merged_into=?,updated_at=? WHERE person_id=?
                """,
                (target_person_id, utc_now(), source_person_id),
            )
            await conn.execute(
                """
                INSERT INTO person_changes(person_id,action,old_json,new_json,actor,created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    target_person_id,
                    "merge",
                    dumps(dict(source)),
                    dumps({"merged_from": source_person_id}),
                    actor,
                    utc_now(),
                ),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def create_person_candidate(
        self,
        name: str,
        *,
        confidence: float,
        message_id: int | None = None,
        tag_id: int | None = None,
        sample_text: str | None = None,
        sample_caption: str | None = None,
        category: str | None = None,
        candidate_kind: str = "speaker",
        detected_position: str | None = None,
        suggested_name: str | None = None,
        web_query: str | None = None,
        web_evidence: list[dict[str, Any]] | None = None,
    ) -> int:
        canonical = canonical_key(name)
        if not canonical:
            raise ValueError("نام نامزد خالی است.")
        existing = await self._fetchone(
            """
            SELECT candidate_id FROM person_candidates
            WHERE canonical_name=? AND status='pending' ORDER BY candidate_id DESC LIMIT 1
            """,
            (canonical,),
        )
        if existing:
            candidate_id = int(existing["candidate_id"])
            if message_id is not None:
                await self._execute(
                    """
                    INSERT OR IGNORE INTO person_candidate_links(candidate_id,message_id,tag_id,created_at)
                    VALUES (?,?,?,?)
                    """,
                    (candidate_id, message_id, tag_id, utc_now()),
                )
                await self._execute(
                    """
                    UPDATE messages SET person_candidate_id=?,person_match_method='human_control_pending',
                    updated_at=? WHERE id=?
                    """,
                    (candidate_id, utc_now(), message_id),
                )
            return candidate_id
        now = utc_now()
        candidate_id = await self._execute(
            """
            INSERT INTO person_candidates(
              detected_name,canonical_name,candidate_kind,detected_position,suggested_name,web_query,
              web_evidence_json,confidence,sample_message_id,sample_text,sample_caption,
              category,status,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)
            """,
            (
                normalize_persian(name),
                canonical,
                candidate_kind if candidate_kind in {"speaker", "position_only"} else "speaker",
                normalize_persian(detected_position or "").strip() or None,
                normalize_persian(suggested_name or "").strip() or None,
                str(web_query or "").strip() or None,
                dumps(web_evidence or []),
                confidence,
                message_id,
                sample_text,
                sample_caption,
                category,
                now,
                now,
            ),
        )
        if message_id is not None:
            await self._execute(
                """
                INSERT OR IGNORE INTO person_candidate_links(candidate_id,message_id,tag_id,created_at)
                VALUES (?,?,?,?)
                """,
                (candidate_id, message_id, tag_id, now),
            )
            await self._execute(
                """
                UPDATE messages SET person_candidate_id=?,person_match_method='human_control_pending',
                updated_at=? WHERE id=?
                """,
                (candidate_id, now, message_id),
            )
        return candidate_id

    async def list_person_candidates(
        self, status_value: str | None = None
    ) -> list[dict[str, Any]]:
        if status_value:
            rows = await self._fetchall(
                """
                SELECT pc.*,
                  (SELECT COUNT(*) FROM person_candidate_links pcl WHERE pcl.candidate_id=pc.candidate_id) AS linked_message_count
                FROM person_candidates pc WHERE status=?
                ORDER BY confidence DESC,candidate_id DESC
                """,
                (status_value,),
            )
        else:
            rows = await self._fetchall(
                """
                SELECT pc.*,
                  (SELECT COUNT(*) FROM person_candidate_links pcl WHERE pcl.candidate_id=pc.candidate_id) AS linked_message_count
                FROM person_candidates pc ORDER BY candidate_id DESC
                """
            )
        for row in rows:
            row["web_evidence"] = loads(row.get("web_evidence_json"), [])
        return rows

    async def review_person_candidate(
        self,
        candidate_id: int,
        *,
        action: str,
        actor: str,
        merge_person_id: int | None = None,
        category: str | None = None,
        full_name: str | None = None,
        aliases: list[str] | None = None,
        position: str | None = None,
        add_detected_alias: bool = True,
        add_position: bool = True,
    ) -> dict[str, Any]:
        candidate = await self._fetchone(
            "SELECT * FROM person_candidates WHERE candidate_id=?", (candidate_id,)
        )
        if not candidate:
            return {"ok": False}
        person_id: int | None = None
        resolved_position = normalize_persian(
            position or candidate.get("detected_position") or ""
        ).strip()
        suggested_name = normalize_persian(
            full_name or candidate.get("suggested_name") or candidate.get("detected_name") or ""
        ).strip()
        requested_aliases = [
            normalize_persian(value).strip()
            for value in (aliases or [])
            if normalize_persian(value).strip()
        ]
        if add_detected_alias and candidate.get("detected_name"):
            requested_aliases.append(str(candidate["detected_name"]))
        if action == "approve":
            person_id = await self.save_person(
                full_name=suggested_name,
                category=category or candidate.get("category"),
                position=resolved_position or None,
                registry_status="outside",
                aliases=requested_aliases,
            )
            status_value = "approved"
        elif action == "merge":
            target = await self.get_person(merge_person_id) if merge_person_id is not None else None
            if not target:
                raise ValueError("شخص مقصد معتبر نیست.")
            person_id = merge_person_id
            # A candidate can contribute an alternate spelling and a newly
            # observed position without overwriting the registry's category.
            if requested_aliases:
                now = utc_now()
                for alias in requested_aliases:
                    key = canonical_key(alias)
                    if key:
                        await self._execute(
                            """
                            INSERT OR IGNORE INTO person_aliases(person_id,alias_text,canonical_alias,created_at)
                            VALUES (?,?,?,?)
                            """,
                            (person_id, alias, key, now),
                        )
            if add_position and resolved_position:
                current_position = str(target.get("position") or "").strip()
                if canonical_key(resolved_position) not in {
                    canonical_key(part) for part in current_position.split("|") if part.strip()
                }:
                    combined_position = " | ".join(
                        part for part in (current_position, resolved_position) if part
                    )
                    await self._execute(
                        "UPDATE people SET position=?,updated_at=? WHERE person_id=?",
                        (combined_position[:600], utc_now(), person_id),
                    )
            status_value = "merged"
        elif action == "reject":
            status_value = "rejected"
        else:
            raise ValueError("عملیات نامعتبر است.")
        await self._execute(
            """
            UPDATE person_candidates SET status=?,merged_person_id=?,reviewed_by=?,reviewed_at=?,updated_at=?
            WHERE candidate_id=?
            """,
            (
                status_value,
                person_id,
                actor,
                utc_now(),
                utc_now(),
                candidate_id,
            ),
        )
        if person_id is not None:
            person = await self.get_person(person_id)
            links = await self._fetchall(
                "SELECT message_id,tag_id FROM person_candidate_links WHERE candidate_id=?",
                (candidate_id,),
            )
            for link in links:
                if link.get("tag_id") is not None:
                    await self._execute(
                        """
                        UPDATE message_speaker_tags SET person_id=?,speaker_name=?,canonical_speaker=?
                        WHERE tag_id=?
                        """,
                        (
                            person_id,
                            (person or {}).get("full_name"),
                            (person or {}).get("canonical_name"),
                            int(link["tag_id"]),
                        ),
                    )
                await self._execute(
                    """
                    UPDATE messages SET detected_person_id=?,detected_person_name=?,person_candidate_id=NULL,
                    person_match_method='candidate_review',person_confidence=1,updated_at=?
                    WHERE id=?
                    """,
                    (
                        person_id,
                        (person or {}).get("full_name"),
                        utc_now(),
                        int(link["message_id"]),
                    ),
                )
        elif action == "reject":
            await self._execute(
                """
                UPDATE messages SET person_candidate_id=NULL,person_match_method='human_control_rejected',updated_at=?
                WHERE person_candidate_id=?
                """,
                (utc_now(), candidate_id),
            )
        return {"ok": True, "status": status_value, "person_id": person_id}

    async def get_editorial_automation_state(self) -> dict[str, Any]:
        row = await self._fetchone(
            "SELECT * FROM editorial_automation_state WHERE singleton_id=1"
        )
        if row is None:
            await self._execute(
                "INSERT OR IGNORE INTO editorial_automation_state(singleton_id,updated_at) VALUES (1,?)",
                (utc_now(),),
            )
            row = await self._fetchone(
                "SELECT * FROM editorial_automation_state WHERE singleton_id=1"
            )
        result = dict(row or {})
        result["initial_analysis_enabled"] = bool(
            result.get("initial_analysis_enabled")
        )
        result["drafts_enabled"] = bool(result.get("drafts_enabled"))
        return result

    async def configure_editorial_automation_stage(
        self,
        stage: str,
        *,
        enabled: bool,
        start_at: str | None = None,
    ) -> dict[str, Any]:
        """Persist one independent automation switch and its time boundary.

        ``start_at`` is not cleared when an operator only stops a stage.  It
        remains visible in the dashboard and is used again when the same
        stage is resumed.  Passing a new start time deliberately resets the
        boundary for future cycles.
        """
        if stage not in {"analysis", "draft"}:
            raise ValueError("مرحلهٔ خودکار نامعتبر است.")
        now = utc_now()
        enabled_column = "initial_analysis_enabled" if stage == "analysis" else "drafts_enabled"
        start_column = "analysis_start_at" if stage == "analysis" else "draft_start_at"
        status_column = "analysis_status" if stage == "analysis" else "draft_status"
        await self._execute(
            f"""
            INSERT INTO editorial_automation_state(
              singleton_id,{enabled_column},{start_column},{status_column},updated_at
            ) VALUES (1,?,?,?,?)
            ON CONFLICT(singleton_id) DO UPDATE SET
              {enabled_column}=excluded.{enabled_column},
              {start_column}=COALESCE(excluded.{start_column},{start_column}),
              {status_column}=excluded.{status_column},updated_at=excluded.updated_at
            """,
            (int(enabled), start_at, "idle" if enabled else "stopped", now),
        )
        return await self.get_editorial_automation_state()

    async def set_editorial_automation_enabled(self, enabled: bool) -> dict[str, Any]:
        """Backward-compatible alias for older integrations (stage one)."""
        return await self.configure_editorial_automation_stage(
            "analysis", enabled=enabled
        )

    async def mark_editorial_automation_run(
        self,
        stage: str,
        *,
        status: str,
        processed: int | None = None,
        error: str | None = None,
        started: bool = False,
    ) -> dict[str, Any]:
        if stage not in {"analysis", "draft"}:
            raise ValueError("مرحلهٔ خودکار نامعتبر است.")
        now = utc_now()
        prefix = "analysis" if stage == "analysis" else "draft"
        assignments = [f"{prefix}_status=?", "updated_at=?"]
        params: list[Any] = [status, now]
        if started:
            assignments.append(f"{prefix}_last_started_at=?")
            params.append(now)
        if status in {"completed", "failed", "idle"}:
            assignments.append(f"{prefix}_last_completed_at=?")
            params.append(now)
        if processed is not None:
            assignments.append(f"{prefix}_last_processed=?")
            params.append(max(0, int(processed)))
        if error is not None or status in {"completed", "idle"}:
            assignments.append(f"{prefix}_last_error=?")
            params.append(str(error or "")[:1800] or None)
        await self._execute(
            "UPDATE editorial_automation_state SET " + ",".join(assignments) + " WHERE singleton_id=1",
            params,
        )
        return await self.get_editorial_automation_state()

    async def list_unanalyzed_message_ids(
        self, *, limit: int = 100, start_at: str | None = None
    ) -> list[int]:
        where = [
            "status <> 'rejected'",
            "(TRIM(COALESCE(text,''))<>'' OR TRIM(COALESCE(caption,''))<>'')",
            "(analysis_content_type IS NULL OR TRIM(analysis_content_type)='')",
        ]
        params: list[Any] = []
        if start_at:
            where.append("COALESCE(published_at,received_at,created_at)>=?")
            params.append(start_at)
        params.append(max(1, min(int(limit), 300)))
        rows = await self._fetchall(
            f"""
            SELECT id FROM messages
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(published_at,received_at,created_at),id
            LIMIT ?
            """,
            params,
        )
        return [int(row["id"]) for row in rows]

    async def list_unresolved_speaker_tags(
        self, message_ids: list[int]
    ) -> list[dict[str, Any]]:
        ids = [int(value) for value in message_ids if int(value) > 0]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return await self._fetchall(
            f"""
            SELECT st.*,m.text,m.caption,m.detected_person_name,m.person_candidate_id,
                   m.analysis_content_type,m.id AS message_id
            FROM message_speaker_tags st JOIN messages m ON m.id=st.message_id
            WHERE st.message_id IN ({placeholders}) AND st.person_id IS NULL
              AND m.analysis_content_type='person_statement'
              AND (
                (TRIM(COALESCE(st.speaker_name,''))<>'' AND st.speaker_name<>'نامشخص')
                OR (TRIM(COALESCE(st.position,''))<>'' AND st.position<>'نامشخص')
              )
            ORDER BY st.message_id,st.sort_order,st.tag_id
            """,
            ids,
        )

    async def bind_speaker_tag_to_person(
        self,
        tag_id: int,
        person_id: int,
        *,
        match_method: str,
    ) -> bool:
        person = await self.get_person(person_id)
        tag = await self._fetchone(
            "SELECT message_id FROM message_speaker_tags WHERE tag_id=?", (tag_id,)
        )
        if not person or not tag:
            return False
        now = utc_now()
        await self._execute(
            """
            UPDATE message_speaker_tags SET person_id=?,speaker_name=?,canonical_speaker=? WHERE tag_id=?
            """,
            (person_id, person["full_name"], person["canonical_name"], tag_id),
        )
        await self._execute(
            """
            UPDATE messages SET detected_person_id=?,detected_person_name=?,person_candidate_id=NULL,
              person_match_method=?,person_confidence=1,updated_at=? WHERE id=?
            """,
            (person_id, person["full_name"], match_method, now, int(tag["message_id"])),
        )
        return True

    async def find_person_in_evidence(self, evidence: str) -> dict[str, Any] | None:
        text = normalize_persian(evidence or "")
        if not text:
            return None
        people = await self.list_people()
        matches: list[tuple[int, dict[str, Any]]] = []
        for person in people:
            if int(person.get("active") or 0) == 0:
                continue
            names = [str(person.get("full_name") or "")]
            names.extend(
                str(alias).strip()
                for alias in str(person.get("aliases") or "").split("|")
            )
            for candidate in names:
                candidate = normalize_persian(candidate).strip()
                if len(candidate) >= 4 and candidate in text:
                    matches.append((len(candidate), person))
                    break
        if not matches:
            return None
        best_len = max(item[0] for item in matches)
        top = [person for length, person in matches if length == best_len]
        # Ambiguous evidence matches must not invent a unique identity.
        if len(top) != 1:
            return None
        return top[0]

    async def list_auto_editorial_candidates(
        self, *, limit: int = 240, start_at: str | None = None
    ) -> list[dict[str, Any]]:
        where = [
            "m.analysis_content_type IN ('person_statement','event')",
            "(m.analysis_content_type='event' OR m.detected_person_id IS NOT NULL)",
            "NOT EXISTS (SELECT 1 FROM editorial_draft_inputs edi WHERE edi.message_id=m.id)",
            "NOT EXISTS (SELECT 1 FROM editorial_message_decisions emd WHERE emd.message_id=m.id AND emd.decision='discarded')",
        ]
        params: list[Any] = []
        if start_at:
            # Person tags are the authoritative timestamp of stage-one
            # analysis; event messages have no speaker tag, so their updated
            # time is the moment model one saved the event result.
            where.append(
                "COALESCE((SELECT MAX(st2.created_at) FROM message_speaker_tags st2 WHERE st2.message_id=m.id),m.updated_at)>=?"
            )
            params.append(start_at)
        params.append(max(1, min(int(limit), 600)))
        rows = await self._fetchall(
            f"""
            SELECT m.*
            FROM messages m
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(m.published_at,m.received_at,m.created_at),m.id
            LIMIT ?
            """,
            params,
        )
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        tags = await self._fetchall(
            f"SELECT * FROM message_speaker_tags WHERE message_id IN ({placeholders}) ORDER BY message_id,sort_order,tag_id",
            ids,
        )
        by_message: dict[int, list[dict[str, Any]]] = {}
        for tag in tags:
            by_message.setdefault(int(tag["message_id"]), []).append(tag)
        for row in rows:
            row["speaker_tags"] = by_message.get(int(row["id"]), [])
        return rows

    async def save_topic(
        self, name: str, keywords: list[str], *, topic_id: int | None = None
    ) -> int:
        clean = normalize_persian(name)
        if not clean:
            raise ValueError("نام موضوع خالی است.")
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            if topic_id is None:
                current = await (
                    await conn.execute("SELECT topic_id FROM topics WHERE name=?", (clean,))
                ).fetchone()
                topic_id = int(current["topic_id"]) if current else None
            if topic_id is None:
                cursor = await conn.execute(
                    """
                    INSERT INTO topics(name,canonical_name,active,created_at,updated_at)
                    VALUES (?,?,1,?,?)
                    """,
                    (clean, canonical_key(clean), now, now),
                )
                topic_id = int(cursor.lastrowid)
            else:
                await conn.execute(
                    """
                    UPDATE topics SET name=?,canonical_name=?,active=1,updated_at=? WHERE topic_id=?
                    """,
                    (clean, canonical_key(clean), now, topic_id),
                )
                await conn.execute(
                    "DELETE FROM topic_keywords WHERE topic_id=?", (topic_id,)
                )
            for keyword in keywords:
                value = normalize_persian(keyword)
                if value:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO topic_keywords(topic_id,keyword,canonical_keyword)
                        VALUES (?,?,?)
                        """,
                        (topic_id, value, canonical_key(value)),
                    )
            await conn.commit()
            return int(topic_id)
        finally:
            await conn.close()

    async def list_topics_with_keywords(self) -> list[dict[str, Any]]:
        topics = await self._fetchall(
            "SELECT * FROM topics WHERE active=1 ORDER BY name"
        )
        for topic in topics:
            topic["keywords"] = await self._fetchall(
                "SELECT keyword,canonical_keyword FROM topic_keywords WHERE topic_id=? ORDER BY keyword",
                (topic["topic_id"],),
            )
        return topics

    async def save_bulletin_template(
        self,
        *,
        name: str,
        system_prompt: str,
        user_prompt_template: str,
        output_format: str = "json_schema",
        template_id: int | None = None,
    ) -> int:
        now = utc_now()
        if template_id is None:
            return await self._execute(
                """
                INSERT INTO bulletin_templates(
                  name,system_prompt,user_prompt_template,output_format,version,active,created_at,updated_at
                ) VALUES (?,?,?,?,1,1,?,?)
                """,
                (
                    name,
                    system_prompt,
                    user_prompt_template,
                    output_format,
                    now,
                    now,
                ),
            )
        await self._execute(
            """
            UPDATE bulletin_templates SET name=?,system_prompt=?,user_prompt_template=?,
            output_format=?,version=version+1,updated_at=? WHERE id=?
            """,
            (
                name,
                system_prompt,
                user_prompt_template,
                output_format,
                now,
                template_id,
            ),
        )
        return template_id

    async def list_bulletin_templates(self) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM bulletin_templates WHERE active=1 ORDER BY id"
        )

    async def get_bulletin_template(
        self, template_id: int
    ) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM bulletin_templates WHERE id=?", (template_id,)
        )

    async def create_bulletin_run(
        self,
        *,
        requested_by: str | None,
        template_id: int | None,
        date_from: str | None,
        date_to: str | None,
        source_chat_ids: list[int] | None,
        statuses: list[str],
        filters: dict[str, Any],
        provider: str,
        model: str | None,
    ) -> int:
        issue_number = filters.get("issue_number")
        report_mode = str(filters.get("report_mode") or "concise")
        if report_mode not in {"concise", "full"}:
            report_mode = "concise"
        now = utc_now()
        return await self._execute(
            """
            INSERT INTO bulletin_runs(
              requested_by,template_id,date_from,date_to,source_ids_json,statuses_json,filters_json,
              provider,model,status,current_stage,issue_number,report_mode,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,'queued','queued',?,?,?,?)
            """,
            (
                requested_by,
                template_id,
                date_from,
                date_to,
                dumps(source_chat_ids or []),
                dumps(statuses or ["approved"]),
                dumps(filters or {}),
                provider,
                model,
                issue_number,
                report_mode,
                now,
                now,
            ),
        )

    async def claim_bulletin_run(self, run_id: int) -> bool:
        conn = await self._connect()
        try:
            cursor = await conn.execute(
                """
                UPDATE bulletin_runs SET status='running',current_stage='loading',
                started_at=COALESCE(started_at,?),updated_at=?
                WHERE id=? AND status IN ('queued','recovered')
                """,
                (utc_now(), utc_now(), run_id),
            )
            await conn.commit()
            return cursor.rowcount == 1
        finally:
            await conn.close()

    async def recover_incomplete_bulletin_runs(self) -> list[int]:
        rows = await self._fetchall(
            "SELECT id FROM bulletin_runs WHERE status='running' ORDER BY id"
        )
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            await self._execute(
                f"""
                UPDATE bulletin_runs SET status='recovered',current_stage='recovered',updated_at=?
                WHERE id IN ({placeholders})
                """,
                [utc_now(), *ids],
            )
        return ids

    async def get_bulletin_run(self, run_id: int) -> dict[str, Any] | None:
        row = await self._fetchone(
            """
            SELECT r.*,t.name AS template_name,t.version AS template_version
            FROM bulletin_runs r LEFT JOIN bulletin_templates t ON t.id=r.template_id
            WHERE r.id=?
            """,
            (run_id,),
        )
        return row

    async def list_bulletin_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT r.*,t.name AS template_name,
              (SELECT COUNT(*) FROM bulletin_items i WHERE i.run_id=r.id) AS item_count,
              EXISTS(SELECT 1 FROM bulletin_exports e WHERE e.run_id=r.id AND e.registry_bucket='layout' AND e.format='html' AND e.file_path LIKE '%bulletin_page_layout.html') AS page_layout_html_ready,
              EXISTS(SELECT 1 FROM bulletin_exports e WHERE e.run_id=r.id AND e.registry_bucket='concise' AND e.format='docx' AND e.file_path LIKE '%bulletin_page_layout.docx') AS page_layout_word_ready,
              EXISTS(SELECT 1 FROM bulletin_exports e WHERE e.run_id=r.id AND e.registry_bucket='classic' AND e.format='pdf' AND e.file_path LIKE '%bulletin_page_layout.pdf') AS page_layout_pdf_ready
            FROM bulletin_runs r LEFT JOIN bulletin_templates t ON t.id=r.template_id
            ORDER BY r.id DESC LIMIT ?
            """,
            (max(1, limit),),
        )

    async def mark_bulletin_export_state(
        self,
        run_id: int,
        *,
        stage: str,
        error_text: str | None = None,
    ) -> None:
        """Expose asynchronous manual-export progress to the dashboard."""

        await self._execute(
            """
            UPDATE bulletin_runs SET current_stage=?,error_text=?,updated_at=? WHERE id=?
            """,
            (stage, error_text, utc_now(), run_id),
        )

    async def delete_bulletin_run(self, run_id: int) -> bool:
        """Delete one finished run; schema cascades remove its linked records."""

        if not await self.get_bulletin_run(run_id):
            return False
        await self._execute("DELETE FROM bulletin_runs WHERE id=?", (run_id,))
        return True

    async def complete_bulletin_run(
        self,
        run_id: int,
        *,
        output_text: str,
        output_html: str,
        raw_response: dict[str, Any] | None,
        usage: dict[str, Any] | None,
    ) -> None:
        item_count = await self._fetchone(
            "SELECT COUNT(*) AS c FROM bulletin_items WHERE run_id=?", (run_id,)
        )
        now = utc_now()
        await self._execute(
            """
            UPDATE bulletin_runs SET status='completed',current_stage='editorial_review',
            output_text=?,output_html=?,raw_response_json=?,usage_json=?,
            processed_message_count=?,completed_at=?,updated_at=? WHERE id=?
            """,
            (
                output_text,
                output_html,
                dumps(raw_response or {}),
                dumps(usage or {}),
                int((item_count or {}).get("c") or 0),
                now,
                now,
                run_id,
            ),
        )

    async def fail_bulletin_run(
        self,
        run_id: int,
        error_text: str,
        *,
        stage: str | None = None,
        error_type: str | None = None,
        error_traceback: str | None = None,
    ) -> None:
        await self._execute(
            """
            UPDATE bulletin_runs SET status='failed',current_stage='failed',failed_stage=?,
            error_text=?,error_type=?,error_traceback=?,completed_at=?,updated_at=? WHERE id=?
            """,
            (
                stage,
                error_text[:12000],
                error_type,
                error_traceback,
                utc_now(),
                utc_now(),
                run_id,
            ),
        )

    async def update_bulletin_run_report(
        self,
        run_id: int,
        *,
        report: dict[str, Any],
        input_count: int,
        processed_count: int,
        skipped_count: int,
        warning_count: int,
        stage: str,
    ) -> None:
        await self._execute(
            """
            UPDATE bulletin_runs SET processing_report_json=?,input_message_count=?,
            processed_message_count=?,skipped_message_count=?,warning_count=?,current_stage=?,updated_at=?
            WHERE id=?
            """,
            (
                dumps(report),
                input_count,
                processed_count,
                skipped_count,
                warning_count,
                stage,
                utc_now(),
                run_id,
            ),
        )

    async def select_messages_for_run(
        self, run: dict[str, Any], *, limit: int = 500
    ) -> list[dict[str, Any]]:
        source_ids = [int(x) for x in loads(run.get("source_ids_json"), []) or []]
        statuses = [str(x) for x in loads(run.get("statuses_json"), ["approved"]) or ["approved"]]
        where: list[str] = ["status IN (" + ",".join("?" for _ in statuses) + ")"]
        params: list[Any] = list(statuses)
        if run.get("date_from"):
            where.append("COALESCE(published_at,received_at)>=?")
            params.append(run["date_from"])
        if run.get("date_to"):
            where.append("COALESCE(published_at,received_at)<=?")
            params.append(run["date_to"])
        if source_ids:
            where.append(
                "source_chat_id IN (" + ",".join("?" for _ in source_ids) + ")"
            )
            params.extend(source_ids)
        params.append(max(1, limit))
        return await self._fetchall(
            f"""
            SELECT * FROM messages WHERE {' AND '.join(where)}
            ORDER BY COALESCE(published_at,received_at),id LIMIT ?
            """,
            params,
        )

    async def replace_bulletin_items(
        self, run_id: int, items: list[dict[str, Any]]
    ) -> list[int]:
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "DELETE FROM bulletin_items WHERE run_id=?", (run_id,)
            )
            ids: list[int] = []
            for order, item in enumerate(items, start=1):
                cursor = await conn.execute(
                    """
                    INSERT INTO bulletin_items(
                      run_id,person_id,person_candidate_id,person_name,position,category,registry_bucket,
                      topic_id,topic_name,main_subject,statement_type,statement_location_type,statement_location_label,
                      summary,summary_detailed,edited_summary,detail,editorial_category,editorial_source_url,editorial_qr_code_path,
                      summary_method,summary_version,status,
                      confidence,confidence_breakdown_json,consensus_method,selected_sentences_json,
                      pipeline_versions_json,importance_score,include_in_main,include_in_appendix,
                      editorial_order,review_reason,issue_tags_json,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        item.get("person_id"),
                        item.get("person_candidate_id"),
                        item.get("person_name") or "نامشخص",
                        item.get("position"),
                        item.get("category"),
                        item.get("registry_bucket") or "outside",
                        item.get("topic_id"),
                        item.get("topic_name") or "سایر",
                        item.get("main_subject"),
                        item.get("statement_type"),
                        item.get("statement_location_type"),
                        item.get("statement_location_label"),
                        item.get("summary") or "",
                        item.get("summary_detailed"),
                        item.get("edited_summary"),
                        item.get("detail"),
                        item.get("editorial_category"),
                        item.get("editorial_source_url"),
                        item.get("editorial_qr_code_path"),
                        item.get("summary_method") or "extractive",
                        item.get("summary_version") or "v11.0",
                        item.get("status") or "review_pending",
                        float(item.get("confidence") or 0),
                        dumps(item.get("confidence_breakdown") or {}),
                        item.get("consensus_method"),
                        dumps(item.get("selected_sentences") or []),
                        dumps(item.get("pipeline_versions") or {"pipeline": "11.0"}),
                        float(item.get("importance_score") or 0.5),
                        int(item.get("include_in_main", True)),
                        int(item.get("include_in_appendix", True)),
                        int(item.get("editorial_order") or order),
                        item.get("review_reason"),
                        dumps(item.get("issue_tags") or []),
                        now,
                        now,
                    ),
                )
                item_id = int(cursor.lastrowid)
                ids.append(item_id)
                for relation in item.get("messages") or []:
                    message_id = (
                        int(relation["message_id"])
                        if isinstance(relation, dict)
                        else int(relation)
                    )
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO bulletin_item_messages(
                          item_id,message_id,relation_type,sentence_index,score,created_at
                        ) VALUES (?,?,?,?,?,?)
                        """,
                        (
                            item_id,
                            message_id,
                            relation.get("relation_type", "evidence")
                            if isinstance(relation, dict)
                            else "evidence",
                            relation.get("sentence_index")
                            if isinstance(relation, dict)
                            else None,
                            relation.get("score") if isinstance(relation, dict) else None,
                            now,
                        ),
                    )
            await conn.commit()
            return ids
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def list_bulletin_items(self, run_id: int) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT i.*,
              (SELECT COUNT(*) FROM bulletin_item_messages im
               WHERE im.item_id=i.item_id AND im.relation_type<>'duplicate') AS evidence_count,
              (SELECT COUNT(*) FROM bulletin_item_messages im
               WHERE im.item_id=i.item_id AND im.relation_type='duplicate') AS duplicate_count
            FROM bulletin_items i WHERE i.run_id=?
            ORDER BY i.editorial_order,i.item_id
            """,
            (run_id,),
        )

    async def bulletin_item_detail(self, item_id: int) -> dict[str, Any] | None:
        item = await self._fetchone(
            "SELECT * FROM bulletin_items WHERE item_id=?", (item_id,)
        )
        if not item:
            return None
        item["messages"] = await self._fetchall(
            """
            SELECT m.*,im.relation_type,im.sentence_index,im.score,
              m.normalized_text AS message_text,
              m.statement_type AS processed_statement_type
            FROM bulletin_item_messages im JOIN messages m ON m.id=im.message_id
            WHERE im.item_id=? ORDER BY
              CASE im.relation_type WHEN 'representative' THEN 0 WHEN 'evidence' THEN 1 ELSE 2 END,
              m.published_at,m.id
            """,
            (item_id,),
        )
        return item

    async def moderate_bulletin_item(
        self,
        item_id: int,
        *,
        new_status: str,
        actor: str,
        edited_summary: str | None = None,
        reason: str | None = None,
        corrected_person_name: str | None = None,
        corrected_person_id: int | None = None,
        issue_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if new_status not in {"approved", "rejected", "review_pending"}:
            raise ValueError("وضعیت سردبیری نامعتبر است.")
        item = await self._fetchone(
            "SELECT * FROM bulletin_items WHERE item_id=?", (item_id,)
        )
        if not item:
            return {"ok": False}
        person_changed = False
        person_name = str(corrected_person_name or item["person_name"]).strip()
        person_id = corrected_person_id
        if corrected_person_id is not None:
            person = await self.get_person(corrected_person_id)
            if not person:
                raise ValueError("شخص اصلاح‌شده پیدا نشد.")
            person_name = str(person["full_name"])
        elif corrected_person_name:
            matched = await self.find_person(corrected_person_name)
            if matched:
                person_id = int(matched["person_id"])
                person_name = str(matched["full_name"])
        if person_name != item["person_name"] or person_id != item.get("person_id"):
            person_changed = True
        now = utc_now()
        await self._execute(
            """
            UPDATE bulletin_items SET status=?,edited_summary=?,
            person_name=?,person_id=?,review_reason=?,issue_tags_json=?,
            reviewed_by=?,reviewed_at=?,summary_method=
              CASE WHEN ?='approved' THEN 'editor_approved' ELSE summary_method END,
            updated_at=? WHERE item_id=?
            """,
            (
                new_status,
                edited_summary,
                person_name,
                person_id,
                reason,
                dumps(issue_tags or []),
                actor,
                now,
                new_status,
                now,
                item_id,
            ),
        )
        return {
            "ok": True,
            "status": new_status,
            "person_changed": person_changed,
            "person_id": person_id,
            "person_name": person_name,
        }

    async def update_bulletin_item_generated_summary(
        self, item_id: int, *, summary_data: dict[str, Any], actor: str
    ) -> None:
        await self._execute(
            """
            UPDATE bulletin_items SET summary=?,summary_detailed=?,edited_summary=NULL,
            summary_method=?,summary_version=?,selected_sentences_json=?,
            confidence=?,confidence_breakdown_json=?,status='review_pending',
            reviewed_by=NULL,reviewed_at=NULL,updated_at=? WHERE item_id=?
            """,
            (
                summary_data.get("summary") or "",
                summary_data.get("summary_detailed")
                or summary_data.get("summary")
                or "",
                summary_data.get("summary_method") or "ai_reprocessed",
                summary_data.get("summary_version") or "v11.0",
                dumps(summary_data.get("selected_sentences") or []),
                float(summary_data.get("confidence") or 0),
                dumps(summary_data.get("confidence_breakdown") or {}),
                utc_now(),
                item_id,
            ),
        )

    async def start_bulletin_stage(
        self,
        run_id: int,
        stage: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        message_id: int | None = None,
    ) -> int:
        await self._execute(
            "UPDATE bulletin_runs SET current_stage=?,updated_at=? WHERE id=?",
            (stage, utc_now(), run_id),
        )
        return await self.add_bulletin_run_log(
            run_id,
            stage,
            status="running",
            level="INFO",
            message=message,
            details=details,
            message_id=message_id,
        )

    async def finish_bulletin_stage(
        self,
        log_id: int,
        *,
        status: str = "completed",
        level: str = "INFO",
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        row = await self._fetchone(
            "SELECT run_id,created_at FROM bulletin_run_logs WHERE id=?", (log_id,)
        )
        duration: int | None = None
        if row:
            try:
                duration = int(
                    (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(str(row["created_at"]))
                    ).total_seconds()
                    * 1000
                )
            except ValueError:
                duration = None
        await self._execute(
            """
            UPDATE bulletin_run_logs SET status=?,level=?,message=COALESCE(?,message),
            details_json=COALESCE(?,details_json),duration_ms=?,completed_at=? WHERE id=?
            """,
            (
                status,
                level,
                message,
                dumps(details) if details is not None else None,
                duration,
                utc_now(),
                log_id,
            ),
        )

    async def add_bulletin_run_log(
        self,
        run_id: int,
        stage: str,
        *,
        status: str,
        level: str = "INFO",
        message: str | None = None,
        details: dict[str, Any] | None = None,
        message_id: int | None = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO bulletin_run_logs(
              run_id,message_id,stage,status,level,message,details_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                message_id,
                stage,
                status,
                level,
                message,
                dumps(details or {}),
                utc_now(),
            ),
        )

    async def list_bulletin_run_logs(
        self, run_id: int, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT * FROM bulletin_run_logs WHERE run_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (run_id, max(1, limit)),
        )

    async def add_processing_error(
        self,
        run_id: int,
        *,
        stage: str,
        error_type: str,
        error_message: str,
        message_id: int | None = None,
        error_traceback: str | None = None,
        payload_snapshot: Any = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO processing_errors(
              run_id,message_id,stage,error_type,error_message,error_traceback,payload_snapshot,created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                message_id,
                stage,
                error_type,
                error_message[:8000],
                error_traceback,
                dumps(payload_snapshot) if payload_snapshot is not None else None,
                utc_now(),
            ),
        )

    async def list_processing_errors(
        self, run_id: int, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT e.*,m.source_chat_title,m.source_chat_username,m.text,m.caption
            FROM processing_errors e LEFT JOIN messages m ON m.id=e.message_id
            WHERE e.run_id=? ORDER BY e.error_id DESC LIMIT ?
            """,
            (run_id, max(1, limit)),
        )

    async def save_bulletin_export(
        self,
        run_id: int,
        *,
        format_name: str,
        registry_bucket: str,
        file_path: str,
        sha256: str,
    ) -> None:
        await self._execute(
            """
            INSERT INTO bulletin_exports(run_id,format,registry_bucket,file_path,sha256,created_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(run_id,format,registry_bucket) DO UPDATE SET
              file_path=excluded.file_path,sha256=excluded.sha256,created_at=excluded.created_at
            """,
            (run_id, format_name, registry_bucket, file_path, sha256, utc_now()),
        )

    async def list_bulletin_exports(self, run_id: int) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM bulletin_exports WHERE run_id=? ORDER BY id", (run_id,)
        )

    async def replace_bulletin_controversies(
        self, run_id: int, items: list[dict[str, Any]]
    ) -> None:
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "DELETE FROM bulletin_controversies WHERE run_id=?", (run_id,)
            )
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO bulletin_controversies(run_id,payload_json,created_at)
                    VALUES (?,?,?)
                    """,
                    (run_id, dumps(item), utc_now()),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def replace_export_validation_issues(
        self, run_id: int, items: list[dict[str, Any]]
    ) -> None:
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "DELETE FROM export_validation_issues WHERE run_id=?", (run_id,)
            )
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO export_validation_issues(
                      run_id,code,severity,object_type,object_id,message,details_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        item.get("code"),
                        item.get("severity"),
                        item.get("object_type"),
                        item.get("object_id"),
                        item.get("message"),
                        dumps(item.get("details") or {}),
                        utc_now(),
                    ),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def bulletin_v10_snapshot(self, run_id: int) -> dict[str, Any]:
        run = await self.get_bulletin_run(run_id)
        if not run:
            raise ValueError("اجرای بولتن پیدا نشد.")
        selected_records = await self.select_messages_for_run(run, limit=10000)
        item_rows = await self._fetchall(
            """
            SELECT * FROM bulletin_items WHERE run_id=? AND status='approved'
            ORDER BY editorial_order,item_id
            """,
            (run_id,),
        )
        for item in item_rows:
            item["messages"] = await self._fetchall(
                """
                SELECT m.*,im.relation_type,im.sentence_index,im.score
                FROM bulletin_item_messages im JOIN messages m ON m.id=im.message_id
                WHERE im.item_id=?
                ORDER BY CASE im.relation_type WHEN 'representative' THEN 0 WHEN 'evidence' THEN 1 ELSE 2 END,
                         m.published_at,m.id
                """,
                (item["item_id"],),
            )
        people = await self._fetchall(
            """
            SELECT p.*,
              SUM(CASE WHEN bim.id IS NOT NULL THEN 1 ELSE 0 END) AS run_record_count,
              COUNT(DISTINCT CASE WHEN bi.status='approved' THEN bi.item_id END) AS final_statement_count
            FROM people p
            LEFT JOIN bulletin_items bi ON bi.person_id=p.person_id AND bi.run_id=?
            LEFT JOIN bulletin_item_messages bim ON bim.item_id=bi.item_id
            WHERE p.merged_into IS NULL
            GROUP BY p.person_id ORDER BY p.priority,p.full_name
            """,
            (run_id,),
        )
        source_ids = {int(row["source_chat_id"]) for row in selected_records}
        sources: list[dict[str, Any]] = []
        for source_id in source_ids:
            source = await self._fetchone(
                """
                SELECT id AS source_id,chat_id,username,title,source_kind,
                  (SELECT COUNT(*) FROM messages m WHERE m.source_chat_id=monitored_chats.chat_id) AS record_count
                FROM monitored_chats WHERE chat_id=?
                """,
                (source_id,),
            )
            sources.append(
                source
                or {
                    "source_id": source_id,
                    "chat_id": source_id,
                    "title": str(source_id),
                    "record_count": sum(
                        1 for row in selected_records if int(row["source_chat_id"]) == source_id
                    ),
                }
            )
        return {
            "run": run,
            "items": item_rows,
            "people": people,
            "selected_records": selected_records,
            "sources": sources,
        }

    async def save_bulletin_schedule(
        self,
        *,
        name: str,
        cron_expression: str,
        timezone_name: str,
        template_id: int | None,
        filters: dict[str, Any],
        created_by: str,
        schedule_id: int | None = None,
    ) -> int:
        now = utc_now()
        if schedule_id is None:
            return await self._execute(
                """
                INSERT INTO bulletin_schedules(
                  name,cron_expression,timezone,template_id,filters_json,enabled,
                  created_by,created_at,updated_at
                ) VALUES (?,?,?,?,?,1,?,?,?)
                """,
                (
                    name,
                    cron_expression,
                    timezone_name,
                    template_id,
                    dumps(filters),
                    created_by,
                    now,
                    now,
                ),
            )
        await self._execute(
            """
            UPDATE bulletin_schedules SET name=?,cron_expression=?,timezone=?,
            template_id=?,filters_json=?,updated_at=? WHERE id=?
            """,
            (
                name,
                cron_expression,
                timezone_name,
                template_id,
                dumps(filters),
                now,
                schedule_id,
            ),
        )
        return schedule_id

    async def list_bulletin_schedules(
        self, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        where = "WHERE s.enabled=1" if enabled_only else ""
        return await self._fetchall(
            f"""
            SELECT s.*,t.name AS template_name
            FROM bulletin_schedules s LEFT JOIN bulletin_templates t ON t.id=s.template_id
            {where} ORDER BY s.id
            """
        )

    async def set_bulletin_schedule_enabled(
        self, schedule_id: int, enabled: bool
    ) -> None:
        await self._execute(
            "UPDATE bulletin_schedules SET enabled=?,updated_at=? WHERE id=?",
            (int(enabled), utc_now(), schedule_id),
        )

    async def mark_schedule_run(self, schedule_id: int) -> None:
        await self._execute(
            "UPDATE bulletin_schedules SET last_run_at=?,updated_at=? WHERE id=?",
            (utc_now(), utc_now(), schedule_id),
        )

    async def list_pending_ai_messages(self, run_id: int) -> list[dict[str, Any]]:
        run = await self.get_bulletin_run(run_id)
        if not run:
            return []
        selected = await self.select_messages_for_run(run, limit=10000)
        result: list[dict[str, Any]] = []
        for message in selected:
            if str(message.get("ai_enrichment_status") or "") in {
                "pending",
                "needs_review",
            }:
                message["message_id"] = message["id"]
                message["message_text"] = message.get("normalized_text") or ""
                message["topic_name"] = message.get("detected_topic_name")
                result.append(message)
        return result

    async def reconcile_editorial_ai_state(
        self, run_id: int, *, actor: str, exclude_unlinked: bool
    ) -> dict[str, Any]:
        items = await self.list_bulletin_items(run_id)
        linked: set[int] = set()
        for item in items:
            if item.get("status") == "approved":
                rows = await self._fetchall(
                    "SELECT message_id FROM bulletin_item_messages WHERE item_id=?",
                    (item["item_id"],),
                )
                linked.update(int(row["message_id"]) for row in rows)
                if str(item.get("summary_method") or "") in {
                    "pending_ai_extractive_draft",
                    "ai_unvalidated_pending_review",
                }:
                    await self._execute(
                        """
                        UPDATE bulletin_items SET summary_method='editor_approved',
                        reviewed_by=COALESCE(reviewed_by,?),reviewed_at=COALESCE(reviewed_at,?),
                        updated_at=? WHERE item_id=?
                        """,
                        (actor, utc_now(), utc_now(), item["item_id"]),
                    )
        pending = await self.list_pending_ai_messages(run_id)
        resolved_messages = 0
        for message in pending:
            message_id = int(message["id"])
            if message_id in linked:
                await self._execute(
                    """
                    UPDATE messages SET ai_enrichment_status='covered_by_editor',
                    ai_enrichment_confidence=1,updated_at=? WHERE id=?
                    """,
                    (utc_now(), message_id),
                )
                resolved_messages += 1
            elif exclude_unlinked:
                await self._execute(
                    """
                    UPDATE messages SET ai_enrichment_status='excluded_by_editor',
                    relevance_status='excluded',updated_at=? WHERE id=?
                    """,
                    (utc_now(), message_id),
                )
                resolved_messages += 1
        remaining = await self.list_pending_ai_messages(run_id)
        return {
            "ok": True,
            "resolved_items": sum(
                1
                for item in items
                if item.get("status") == "approved"
                and str(item.get("summary_method") or "")
                in {"pending_ai_extractive_draft", "ai_unvalidated_pending_review"}
            ),
            "resolved_messages": resolved_messages,
            "remaining_messages": len(remaining),
        }

    async def resolve_pending_ai_message(
        self,
        run_id: int,
        message_id: int,
        *,
        decision: str,
        actor: str,
    ) -> dict[str, Any]:
        run = await self.get_bulletin_run(run_id)
        message = await self.get_message(message_id)
        if not run or not message:
            return {"ok": False}
        if decision not in {"covered", "exclude"}:
            raise ValueError("تصمیم باید covered یا exclude باشد.")
        if decision == "covered":
            linked = await self._fetchone(
                """
                SELECT 1 AS ok FROM bulletin_item_messages im
                JOIN bulletin_items i ON i.item_id=im.item_id
                WHERE i.run_id=? AND i.status='approved' AND im.message_id=? LIMIT 1
                """,
                (run_id, message_id),
            )
            if not linked:
                raise ValueError(
                    "پیام به هیچ آیتم تأییدشده‌ای متصل نیست و نمی‌تواند پوشش‌داده‌شده ثبت شود."
                )
            await self._execute(
                """
                UPDATE messages SET ai_enrichment_status='covered_by_editor',
                ai_enrichment_confidence=1,updated_at=? WHERE id=?
                """,
                (utc_now(), message_id),
            )
        else:
            await self._execute(
                """
                UPDATE messages SET ai_enrichment_status='excluded_by_editor',
                relevance_status='excluded',relevance_reason=?,updated_at=? WHERE id=?
                """,
                (f"editor:{actor}", utc_now(), message_id),
            )
        remaining = await self.list_pending_ai_messages(run_id)
        return {"ok": True, "decision": decision, "remaining_messages": len(remaining)}

    async def bulletin_quality_report(
        self, run_id: int | None
    ) -> dict[str, Any]:
        params: list[Any] = []
        item_where = ""
        if run_id is not None:
            item_where = "WHERE run_id=?"
            params.append(run_id)
        items = await self._fetchone(
            f"""
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
              SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected,
              SUM(CASE WHEN status='review_pending' THEN 1 ELSE 0 END) AS review_pending,
              AVG(confidence) AS average_confidence,
              SUM(CASE WHEN confidence<0.6 THEN 1 ELSE 0 END) AS low_confidence
            FROM bulletin_items {item_where}
            """,
            params,
        ) or {}
        issue_rows = await self._fetchall(
            (
                "SELECT issue_tags_json FROM bulletin_items "
                + item_where
                + " AND issue_tags_json IS NOT NULL"
                if item_where
                else "SELECT issue_tags_json FROM bulletin_items WHERE issue_tags_json IS NOT NULL"
            ),
            params,
        )
        counter: dict[str, int] = {}
        for row in issue_rows:
            for tag in loads(row.get("issue_tags_json"), []) or []:
                counter[str(tag)] = counter.get(str(tag), 0) + 1
        return {
            **{key: (float(value) if key == "average_confidence" and value is not None else int(value or 0)) for key, value in items.items()},
            "issue_tags": [
                {"tag": tag, "count": count}
                for tag, count in sorted(
                    counter.items(), key=lambda pair: pair[1], reverse=True
                )
            ],
        }

    async def create_reprocess_log(
        self,
        *,
        run_id: int,
        item_id: int | None,
        mode: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO reprocess_logs(run_id,item_id,mode,actor,status,details_json,created_at)
            VALUES (?,?,?,?,'running',?,?)
            """,
            (run_id, item_id, mode, actor, dumps(details or {}), utc_now()),
        )

    async def complete_reprocess_log(
        self, log_id: int, *, status: str, details: dict[str, Any]
    ) -> None:
        await self._execute(
            """
            UPDATE reprocess_logs SET status=?,details_json=?,completed_at=? WHERE id=?
            """,
            (status, dumps(details), utc_now(), log_id),
        )

    async def add_ai_request(
        self,
        *,
        run_id: int | None,
        item_id: int | None,
        input_hash: str,
        provider: str | None,
        model: str | None,
        prompt_version: str | None,
        raw_request: dict[str, Any] | None,
        raw_response: Any,
        parsed_response: Any,
        token_usage: Any,
        latency_ms: int | None,
        status: str,
        error_text: str | None,
        key_slot: int | None = None,
        attempt_number: int | None = None,
        endpoint: str | None = None,
        http_status: int | None = None,
        request_kind: str | None = None,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO ai_requests(
              run_id,item_id,input_hash,provider,model,prompt_version,request_kind,
              raw_request_json,raw_response_json,parsed_response_json,token_usage_json,
              latency_ms,status,error_text,key_slot,attempt_number,endpoint,http_status,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                item_id,
                input_hash,
                provider,
                model,
                prompt_version,
                request_kind,
                dumps(raw_request or {}),
                dumps(raw_response) if raw_response is not None else None,
                dumps(parsed_response) if parsed_response is not None else None,
                dumps(token_usage) if token_usage is not None else None,
                latency_ms,
                status,
                error_text,
                key_slot,
                attempt_number,
                endpoint,
                http_status,
                utc_now(),
            ),
        )

    async def get_cached_ai_response(
        self,
        *,
        input_hash: str,
        provider: str,
        model: str,
        prompt_version: str,
        request_kind: str | None = None,
    ) -> dict[str, Any] | None:
        where = [
            "input_hash=?",
            "provider=?",
            "model=?",
            "prompt_version=?",
            "status IN ('completed','validated')",
        ]
        params: list[Any] = [input_hash, provider, model, prompt_version]
        if request_kind is not None:
            where.append("request_kind=?")
            params.append(request_kind)
        row = await self._fetchone(
            f"""
            SELECT * FROM ai_requests WHERE {' AND '.join(where)}
            ORDER BY id DESC LIMIT 1
            """,
            params,
        )
        if row:
            row["parsed_response"] = loads(row.get("parsed_response_json"), None)
            row["raw_response"] = loads(row.get("raw_response_json"), None)
        return row

    async def upsert_short_link(self, draft_id: int, code: str, target_url: str) -> None:
        now = utc_now()
        await self._execute(
            """
            INSERT INTO short_links(code,target_url,draft_id,created_at,updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(draft_id) DO UPDATE SET target_url=excluded.target_url,updated_at=excluded.updated_at
            """,
            (code, target_url, draft_id, now, now),
        )

    async def short_link_for_draft(self, draft_id: int) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM short_links WHERE draft_id=?", (draft_id,))

    async def short_link_target(self, code: str) -> str | None:
        row = await self._fetchone("SELECT target_url FROM short_links WHERE code=?", (code,))
        return str(row["target_url"]) if row else None

    async def set_editorial_short_link(self, draft_id: int, short_url: str, qr_code_path: str) -> None:
        await self._execute(
            "UPDATE editorial_drafts SET short_url=?,qr_code_path=?,updated_at=? WHERE draft_id=?",
            (short_url, qr_code_path, utc_now(), draft_id),
        )

    async def set_editorial_source_url(self, draft_id: int, source_url: str) -> None:
        await self._execute(
            "UPDATE editorial_drafts SET source_url=?,updated_at=? WHERE draft_id=?",
            (source_url, utc_now(), draft_id),
        )

    async def ai_usage_summary(self, configured_models: Sequence[str] = ()) -> dict[str, Any]:
        prices = {
            "gpt-5.6": (5.0, 30.0),
            "gpt-5.6-sol": (5.0, 30.0),
            "gpt-5.6-luna": (1.0, 6.0),
            "deepseek-v4-pro": (1.74, 3.48),
        }
        display_names = {
            "gpt-5.6": "GPT-5.6",
            "gpt-5.6-sol": "gpt-5.6-sol",
            "gpt-5.6-luna": "gpt-5.6-luna",
            "deepseek-v4-pro": "deepseek-v4-pro",
        }
        rows = await self._fetchall(
            "SELECT model,token_usage_json FROM ai_requests WHERE token_usage_json IS NOT NULL AND TRIM(token_usage_json)<>''"
        )
        aggregated: dict[str, dict[str, Any]] = {}
        for model in configured_models:
            clean = str(model or "").strip()
            clean = display_names.get(clean.casefold(), clean)
            if clean:
                aggregated.setdefault(clean, {"model": clean, "input_tokens": 0, "output_tokens": 0})
        for row in rows:
            model = str(row.get("model") or "نامشخص").strip() or "نامشخص"
            model = display_names.get(model.casefold(), model)
            usage = loads(row.get("token_usage_json"), {})
            if not isinstance(usage, dict):
                continue
            input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
            try:
                input_value, output_value = int(input_tokens), int(output_tokens)
            except (TypeError, ValueError):
                continue
            bucket = aggregated.setdefault(model, {"model": model, "input_tokens": 0, "output_tokens": 0})
            bucket["input_tokens"] += max(0, input_value)
            bucket["output_tokens"] += max(0, output_value)
        models: list[dict[str, Any]] = []
        for item in aggregated.values():
            rates = prices.get(str(item["model"]).casefold())
            input_cost = (item["input_tokens"] / 1_000_000) * rates[0] if rates else None
            output_cost = (item["output_tokens"] / 1_000_000) * rates[1] if rates else None
            models.append({
                **item,
                "input_price_per_million_usd": rates[0] if rates else None,
                "output_price_per_million_usd": rates[1] if rates else None,
                "input_cost_usd": input_cost,
                "output_cost_usd": output_cost,
                "total_cost_usd": (input_cost or 0) + (output_cost or 0) if rates else None,
            })
        models.sort(key=lambda item: str(item["model"]).casefold())
        return {
            "currency": "USD",
            "models": models,
            "total_cost_usd": sum(item["total_cost_usd"] or 0 for item in models),
        }

    async def save_evaluation_case(
        self, item_id: int, *, actor: str, notes: str | None
    ) -> int:
        detail = await self.bulletin_item_detail(item_id)
        if not detail:
            raise ValueError("آیتم پیدا نشد.")
        message_ids = [int(row["id"]) for row in detail.get("messages") or []]
        now = utc_now()
        return await self._execute(
            """
            INSERT INTO evaluation_cases(
              run_id,item_id,message_ids_json,expected_person_name,expected_topic_name,
              expected_statement_type,expected_summary,expected_claims_json,duplicate_label,
              registry_bucket,notes,created_by,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                detail["run_id"],
                item_id,
                dumps(message_ids),
                detail.get("person_name"),
                detail.get("topic_name"),
                detail.get("statement_type"),
                detail.get("edited_summary") or detail.get("summary"),
                dumps(loads(detail.get("selected_sentences_json"), [])),
                "has_duplicates" if any(row.get("relation_type") == "duplicate" for row in detail.get("messages") or []) else "unique",
                detail.get("registry_bucket"),
                notes,
                actor,
                now,
                now,
            ),
        )

    async def list_evaluation_cases(
        self, run_id: int | None = None
    ) -> list[dict[str, Any]]:
        if run_id is None:
            return await self._fetchall(
                "SELECT * FROM evaluation_cases ORDER BY case_id DESC"
            )
        return await self._fetchall(
            """
            SELECT * FROM evaluation_cases WHERE run_id=? ORDER BY case_id DESC
            """,
            (run_id,),
        )

    async def create_editorial_draft(
        self,
        *,
        title: str | None,
        person_id: int | None,
        person_name: str | None,
        position: str | None,
        topic_id: int | None,
        topic_name: str | None,
        main_subject: str | None = None,
        message_inputs: list[dict[str, Any]],
        base_text: str,
        created_by: str,
        category_name: str | None = None,
        content_type: str = "person_statement",
        event_title: str | None = None,
        event_entities: list[str] | None = None,
        event_location: str | None = None,
        event_time: str | None = None,
        oration_location: str | None = None,
    ) -> int:
        now = utc_now()
        person = await self.get_person(person_id) if person_id else None
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cursor = await conn.execute(
                """
                INSERT INTO editorial_drafts(
                  content_type,title,person_id,person_name,position,topic_id,topic_name,main_subject,category_name,base_text,oration_location,
                  event_title,event_entities_json,event_location,event_time,
                  status,current_version,created_by,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',1,?,?,?)
                """,
                (
                    "event" if str(content_type or "").strip() == "event" else "person_statement",
                    title,
                    person_id,
                    (person or {}).get("full_name") or person_name,
                    (person or {}).get("position") or position,
                    topic_id,
                    topic_name,
                    main_subject,
                    (person or {}).get("category") or category_name,
                    base_text,
                    oration_location,
                    event_title,
                    dumps(event_entities or []),
                    event_location,
                    event_time,
                    created_by,
                    now,
                    now,
                ),
            )
            draft_id = int(cursor.lastrowid)
            for order, item in enumerate(message_inputs):
                await conn.execute(
                    """
                    INSERT INTO editorial_draft_inputs(
                      draft_id,message_id,selected_text,sort_order,created_at
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        draft_id,
                        int(item["message_id"]),
                        item.get("selected_text"),
                        int(item.get("sort_order", order)),
                        now,
                    ),
                )
            await conn.execute(
                """
                INSERT INTO editorial_draft_versions(
                  draft_id,version_no,base_text,main_subject,change_reason,actor,created_at
                ) VALUES (?,1,?,?,'ایجاد پیش‌نویس',?,?)
                """,
                (draft_id, base_text, main_subject, created_by, now),
            )
            await conn.commit()
            return draft_id
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def list_editorial_drafts(
        self,
        *,
        status_value: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status_value:
            where.append("d.status=?")
            params.append(status_value)
        if date_from:
            where.append("COALESCE(d.finalized_at,d.updated_at)>=?")
            params.append(date_from)
        if date_to:
            where.append("COALESCE(d.finalized_at,d.updated_at)<=?")
            params.append(date_to)
        clause = "WHERE " + " AND ".join(where) if where else ""
        rows = await self._fetchall(
            f"""
            SELECT d.*,
              (SELECT COUNT(*) FROM editorial_draft_inputs i WHERE i.draft_id=d.draft_id) AS input_count
            FROM editorial_drafts d {clause}
            ORDER BY d.updated_at DESC,d.draft_id DESC
            """,
            params,
        )
        for row in rows:
            _attach_flow_timestamp(row, "finalized_at", "updated_at", "created_at")
        return rows

    async def get_editorial_draft(
        self, draft_id: int
    ) -> dict[str, Any] | None:
        draft = await self._fetchone(
            "SELECT * FROM editorial_drafts WHERE draft_id=?", (draft_id,)
        )
        if not draft:
            return None
        draft["event"] = {
            "title": draft.get("event_title"),
            "involved_entities": loads(draft.get("event_entities_json"), []),
            "location": draft.get("event_location"),
            "time": draft.get("event_time"),
        }
        draft["inputs"] = await self._fetchall(
            """
            SELECT i.*,m.text,m.caption,m.normalized_text,m.message_url,m.primary_source_url,
              m.forwarded_origin_url,m.source_chat_title,m.source_chat_username,
              m.source_chat_id,m.published_at,m.received_at,m.created_at,m.media_json,
              m.detected_person_id,m.detected_person_name,m.detected_topic_id,m.detected_topic_name
            FROM editorial_draft_inputs i JOIN messages m ON m.id=i.message_id
            WHERE i.draft_id=? ORDER BY i.sort_order,i.id
            """,
            (draft_id,),
        )
        input_ids = [int(item["message_id"]) for item in draft["inputs"]]
        tags_by_message: dict[int, list[dict[str, Any]]] = {}
        if input_ids:
            placeholders = ",".join("?" for _ in input_ids)
            tag_rows = await self._fetchall(
                f"""
                SELECT * FROM message_speaker_tags
                WHERE message_id IN ({placeholders})
                ORDER BY message_id,sort_order,tag_id
                """,
                input_ids,
            )
            for tag in tag_rows:
                tags_by_message.setdefault(int(tag["message_id"]), []).append(tag)
        for item in draft["inputs"]:
            item["speaker_tags"] = tags_by_message.get(int(item["message_id"]), [])
            _attach_flow_timestamp(item, "published_at", "received_at", "created_at")
        draft["versions"] = await self._fetchall(
            """
            SELECT * FROM editorial_draft_versions WHERE draft_id=?
            ORDER BY version_no DESC
            """,
            (draft_id,),
        )
        person = await self.get_person(int(draft["person_id"])) if draft.get("person_id") else None
        matching_tags = [
            tag
            for item in draft["inputs"]
            for tag in item.get("speaker_tags", [])
            if not draft.get("person_id") or tag.get("person_id") == draft.get("person_id")
        ]
        first_tag = matching_tags[0] if matching_tags else next(
            (tag for item in draft["inputs"] for tag in item.get("speaker_tags", [])),
            {},
        )
        effective_position = (
            (person or {}).get("position")
            or draft.get("position")
            or first_tag.get("position")
        )
        analysis_topics: list[dict[str, Any]] = []
        seen_topics: set[tuple[str, str, str]] = set()
        for item in draft["inputs"]:
            for tag in item.get("speaker_tags", []):
                topic_key = (
                    canonical_key(tag.get("speaker_name")),
                    canonical_key(tag.get("general_topic")),
                    canonical_key(tag.get("specific_topic")),
                )
                if topic_key in seen_topics:
                    continue
                seen_topics.add(topic_key)
                analysis_topics.append(
                    {
                        "message_id": int(item["message_id"]),
                        "tag_id": tag.get("tag_id"),
                        "speaker_name": tag.get("speaker_name"),
                        "position": tag.get("position"),
                        "general_topic": tag.get("general_topic"),
                        "specific_topic": tag.get("specific_topic"),
                        "evidence": tag.get("evidence"),
                        "person_id": tag.get("person_id"),
                    }
                )
        source_messages = [
            {
                "message_id": int(item["message_id"]),
                "selected_text": item.get("selected_text"),
                "source_name": item.get("source_chat_title") or item.get("source_chat_username"),
                "source_url": item.get("primary_source_url") or item.get("forwarded_origin_url") or item.get("message_url"),
                "flow_timestamp": item.get("flow_timestamp"),
                "flow_datetime": item.get("flow_datetime"),
                "flow_date": item.get("flow_date"),
                "flow_time": item.get("flow_time"),
                "speaker_tags": item.get("speaker_tags", []),
            }
            for item in draft["inputs"]
        ]
        draft["bulletin_fields"] = {
            "draft_id": int(draft["draft_id"]),
            "status": draft.get("status"),
            "content_type": draft.get("content_type") or "person_statement",
            "title": draft.get("title"),
            "person": {
                "person_id": draft.get("person_id"),
                "name": (person or {}).get("full_name") or draft.get("person_name"),
                "position": effective_position,
                "category": (person or {}).get("category"),
                "registry_status": (person or {}).get("registry_status") or (
                    "inside" if draft.get("person_id") else "outside"
                ),
            },
            "topic": {
                "topic_id": draft.get("topic_id"),
                "name": draft.get("topic_name"),
            },
            "main_subject": draft.get("main_subject"),
            "base_text": draft.get("base_text"),
            "summary_paragraph": draft.get("summary_paragraph"),
            "summary_sentence": draft.get("summary_sentence"),
            "summary_title": draft.get("summary_title"),
            "detail": draft.get("detail") or draft.get("summary_title"),
            "category_name": draft.get("category_name") or (person or {}).get("category"),
            "oration_location": draft.get("oration_location"),
            "source_url": draft.get("source_url"),
            "finalized_at": draft.get("finalized_at"),
            "event": draft["event"],
            "analysis_topics": analysis_topics,
            "source_messages": source_messages,
        }
        _attach_flow_timestamp(draft, "finalized_at", "updated_at", "created_at")
        return draft

    async def save_editorial_draft_version(
        self,
        draft_id: int,
        *,
        title: str | None = None,
        base_text: str,
        summary_paragraph: str | None,
        summary_sentence: str | None,
        summary_title: str | None,
        detail: str | None = None,
        category_name: str | None = None,
        person_id: int | None = None,
        person_name: str | None = None,
        topic_id: int | None = None,
        topic_name: str | None = None,
        main_subject: str | None = None,
        oration_location: str | None = None,
        source_url: str | None = None,
        expected_version: int,
        change_reason: str,
        actor: str,
    ) -> int:
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            current = await (
                await conn.execute(
                    "SELECT current_version,status FROM editorial_drafts WHERE draft_id=?",
                    (draft_id,),
                )
            ).fetchone()
            if current is None:
                raise ValueError("پیش‌نویس پیدا نشد.")
            current_version = int(current["current_version"])
            if current_version != expected_version:
                raise EditorialDraftConflictError(
                    expected_version=expected_version,
                    current_version=current_version,
                )
            if str(current["status"] or "") != "draft":
                raise ValueError("فقط پیش‌نویس نهایی‌نشده قابل ویرایش است.")
            version_no = current_version + 1
            cursor = await conn.execute(
                """
                UPDATE editorial_drafts SET title=COALESCE(?,title),base_text=?,summary_paragraph=?,summary_sentence=?,
                summary_title=?,detail=?,category_name=COALESCE(?,category_name),person_id=COALESCE(?,person_id),person_name=COALESCE(?,person_name),
                topic_id=COALESCE(?,topic_id),topic_name=COALESCE(?,topic_name),main_subject=COALESCE(?,main_subject),
                oration_location=COALESCE(?,oration_location),source_url=COALESCE(?,source_url),
                current_version=?,status='draft',updated_at=? WHERE draft_id=? AND current_version=? AND status='draft'
                """,
                (
                    title,
                    base_text,
                    summary_paragraph,
                    summary_sentence,
                    summary_title,
                    detail or summary_title,
                    category_name,
                    person_id,
                    person_name,
                    topic_id,
                    topic_name,
                    main_subject,
                    oration_location,
                    source_url,
                    version_no,
                    now,
                    draft_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise EditorialDraftConflictError(
                    expected_version=expected_version,
                    current_version=current_version,
                )
            await conn.execute(
                """
                INSERT INTO editorial_draft_versions(
                  draft_id,version_no,base_text,summary_paragraph,summary_sentence,summary_title,category_name,topic_name,main_subject,detail,
                  change_reason,actor,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    draft_id,
                    version_no,
                    base_text,
                    summary_paragraph,
                    summary_sentence,
                    summary_title,
                    category_name,
                    topic_name,
                    main_subject,
                    detail or summary_title,
                    change_reason,
                    actor,
                    now,
                ),
            )
            await conn.commit()
            return version_no
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def restore_editorial_draft_version(
        self, draft_id: int, version_id: int, *, expected_version: int, actor: str
    ) -> int:
        version = await self._fetchone(
            """
            SELECT * FROM editorial_draft_versions WHERE version_id=? AND draft_id=?
            """,
            (version_id, draft_id),
        )
        if not version:
            raise ValueError("نسخه پیدا نشد.")
        return await self.save_editorial_draft_version(
            draft_id,
            title=None,
            base_text=str(version["base_text"]),
            summary_paragraph=version.get("summary_paragraph"),
            summary_sentence=version.get("summary_sentence"),
            summary_title=version.get("summary_title"),
            detail=version.get("detail"),
            category_name=version.get("category_name"),
            topic_name=version.get("topic_name"),
            main_subject=version.get("main_subject"),
            expected_version=expected_version,
            change_reason=f"بازگشت به نسخه {version['version_no']}",
            actor=actor,
        )

    async def delete_editorial_draft(self, draft_id: int) -> dict[str, Any]:
        draft = await self._fetchone(
            "SELECT * FROM editorial_drafts WHERE draft_id=?",
            (draft_id,),
        )
        if not draft:
            raise ValueError("پیش‌نویس پیدا نشد.")
        if str(draft.get("status") or "") != "draft":
            raise ValueError("فقط پیش‌نویس نهایی‌نشده قابل حذف است.")
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cursor = await conn.execute(
                "DELETE FROM editorial_drafts WHERE draft_id=? AND status='draft'",
                (draft_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "پیش‌نویس هم‌زمان تغییر وضعیت داد و حذف نشد؛ صفحه را تازه کنید."
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return draft

    async def finalize_editorial_draft(
        self,
        draft_id: int,
        *,
        actor: str,
        finalized_at: str | None = None,
    ) -> dict[str, Any]:
        draft = await self.get_editorial_draft(draft_id)
        if not draft:
            raise ValueError("پیش‌نویس پیدا نشد.")
        is_event = str(draft.get("content_type") or "") == "event"
        required = [
            ("عنوان رویداد", draft.get("event_title") or draft.get("title"))
            if is_event
            else ("شخص", draft.get("person_name")),
            ("موضوع", draft.get("topic_name")),
            ("سوژه اصلی", draft.get("main_subject")),
            ("متن پایه", draft.get("base_text")),
            ("خلاصه پاراگرافی", draft.get("summary_paragraph")),
            ("خلاصه یک‌جمله‌ای", draft.get("summary_sentence")),
            ("جزئیات موضع", draft.get("detail") or draft.get("summary_title")),
        ]
        missing = [label for label, value in required if not str(value or "").strip()]
        if missing:
            raise ValueError("فیلدهای لازم تکمیل نشده‌اند: " + "، ".join(missing))
        effective_finalized_at = finalized_at or utc_now()
        await self._execute(
            """
            UPDATE editorial_drafts SET status='finalized',finalized_at=?,updated_at=?
            WHERE draft_id=?
            """,
            (effective_finalized_at, utc_now(), draft_id),
        )
        return {
            "ok": True,
            "draft_id": draft_id,
            "status": "finalized",
            "finalized_at": effective_finalized_at,
            **_tehran_flow_fields(effective_finalized_at),
        }

    async def list_finalized_editorial_days(self) -> list[dict[str, Any]]:
        rows = await self.list_editorial_drafts(status_value="finalized")
        grouped: dict[str, int] = {}
        for row in rows:
            day = str(row.get("flow_date") or "").strip()
            if day:
                grouped[day] = grouped.get(day, 0) + 1
        return [
            {"day": day, "draft_count": count}
            for day, count in sorted(grouped.items(), reverse=True)
        ]

    async def list_finalized_editorial_drafts_for_day(
        self, source_day: str
    ) -> list[dict[str, Any]]:
        day = str(source_day or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("روز نهایی‌سازی نامعتبر است.")
        rows = await self.list_editorial_drafts(status_value="finalized")
        return [row for row in rows if str(row.get("flow_date") or "") == day]

    async def create_high_attention_run(
        self,
        *,
        source_day: str,
        draft_ids: list[int],
        items: list[dict[str, Any]],
        provider: str | None,
        model: str | None,
        prompt_version: str,
        api_key_slot: int | None,
        created_by: str,
    ) -> int:
        day = str(source_day or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("روز نهایی‌سازی نامعتبر است.")
        clean_ids = list(dict.fromkeys(int(value) for value in draft_ids))
        if not clean_ids:
            raise ValueError("حداقل یک خبر نهایی برای پربازتاب لازم است.")
        for draft_id in clean_ids:
            draft = await self.get_editorial_draft(draft_id)
            if (
                not draft
                or draft.get("status") != "finalized"
                or str(draft.get("flow_date") or "") != day
            ):
                raise ValueError("همه خبرهای پربازتاب باید نهایی‌شده و متعلق به همان روز باشند.")
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cursor = await conn.execute(
                """
                INSERT INTO high_attention_runs(
                  source_day,selected_draft_ids_json,status,provider,model,prompt_version,
                  api_key_slot,created_by,created_at,updated_at
                ) VALUES (?,?,'draft',?,?,?,?,?,?,?)
                """,
                (
                    day,
                    dumps(clean_ids),
                    provider,
                    model,
                    prompt_version,
                    api_key_slot,
                    created_by,
                    now,
                    now,
                ),
            )
            run_id = int(cursor.lastrowid)
            for order, raw in enumerate(items):
                title = normalize_persian(str(raw.get("title") or "")).strip()
                summary = normalize_persian(str(raw.get("summary") or "")).strip()
                if not title or not summary:
                    continue
                await conn.execute(
                    """
                    INSERT INTO high_attention_items(
                      high_attention_run_id,sort_order,title,summary,source_draft_ids_json,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (run_id, order, title[:220], summary[:1800], dumps(clean_ids), now, now),
                )
            await conn.commit()
            return run_id
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def list_high_attention_runs(
        self, *, source_day: str | None = None, status_value: str | None = None
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if source_day:
            where.append("r.source_day=?")
            params.append(str(source_day))
        if status_value:
            where.append("r.status=?")
            params.append(str(status_value))
        clause = "WHERE " + " AND ".join(where) if where else ""
        rows = await self._fetchall(
            f"""
            SELECT r.*,
              (SELECT COUNT(*) FROM high_attention_items i
               WHERE i.high_attention_run_id=r.high_attention_run_id) AS item_count
            FROM high_attention_runs r {clause}
            ORDER BY r.source_day DESC,r.updated_at DESC,r.high_attention_run_id DESC
            """,
            params,
        )
        for row in rows:
            row["selected_draft_ids"] = loads(row.get("selected_draft_ids_json"), [])
            _attach_flow_timestamp(row, "finalized_at", "updated_at", "created_at")
        return rows

    async def get_high_attention_run(
        self, high_attention_run_id: int
    ) -> dict[str, Any] | None:
        run = await self._fetchone(
            "SELECT * FROM high_attention_runs WHERE high_attention_run_id=?",
            (high_attention_run_id,),
        )
        if not run:
            return None
        run["selected_draft_ids"] = loads(run.get("selected_draft_ids_json"), [])
        run["items"] = await self._fetchall(
            """
            SELECT * FROM high_attention_items WHERE high_attention_run_id=?
            ORDER BY sort_order,high_attention_item_id
            """,
            (high_attention_run_id,),
        )
        for item in run["items"]:
            item["source_draft_ids"] = loads(item.get("source_draft_ids_json"), [])
        _attach_flow_timestamp(run, "finalized_at", "updated_at", "created_at")
        return run

    async def save_high_attention_items(
        self, high_attention_run_id: int, items: list[dict[str, Any]]
    ) -> None:
        run = await self.get_high_attention_run(high_attention_run_id)
        if not run:
            raise ValueError("تولید پربازتاب پیدا نشد.")
        if run.get("status") != "draft":
            raise ValueError("پربازتاب نهایی‌شده قابل ویرایش نیست.")
        existing_ids = {
            int(item["high_attention_item_id"]) for item in run.get("items") or []
        }
        if len(items) > 4:
            raise ValueError("حداکثر چهار محور پربازتاب مجاز است.")
        now = utc_now()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            for order, raw in enumerate(items):
                item_id = int(raw.get("high_attention_item_id") or 0)
                if item_id not in existing_ids:
                    raise ValueError("یکی از محورهای پربازتاب به این تولید تعلق ندارد.")
                title = normalize_persian(str(raw.get("title") or "")).strip()
                summary = normalize_persian(str(raw.get("summary") or "")).strip()
                if not title or not summary:
                    raise ValueError("تیتر و توضیح هر محور پربازتاب لازم است.")
                await conn.execute(
                    """
                    UPDATE high_attention_items SET sort_order=?,title=?,summary=?,updated_at=?
                    WHERE high_attention_item_id=? AND high_attention_run_id=?
                    """,
                    (order, title[:220], summary[:1800], now, item_id, high_attention_run_id),
                )
            await conn.execute(
                "UPDATE high_attention_runs SET updated_at=? WHERE high_attention_run_id=?",
                (now, high_attention_run_id),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def finalize_high_attention_run(
        self, high_attention_run_id: int, *, actor: str
    ) -> dict[str, Any]:
        run = await self.get_high_attention_run(high_attention_run_id)
        if not run:
            raise ValueError("تولید پربازتاب پیدا نشد.")
        if run.get("status") != "draft":
            raise ValueError("این تولید قبلاً نهایی شده است.")
        items = run.get("items") or []
        if not items:
            raise ValueError("مدل محوری پربازتاب پیدا نکرده است؛ تولیدی برای نهایی‌سازی وجود ندارد.")
        if any(
            not str(item.get("title") or "").strip()
            or not str(item.get("summary") or "").strip()
            for item in items
        ):
            raise ValueError("تیتر و توضیح همه محورهای پربازتاب باید تکمیل باشد.")
        now = utc_now()
        await self._execute(
            """
            UPDATE high_attention_runs SET status='finalized',finalized_at=?,updated_at=?,created_by=COALESCE(created_by,?)
            WHERE high_attention_run_id=?
            """,
            (now, now, actor, high_attention_run_id),
        )
        return {"ok": True, "high_attention_run_id": high_attention_run_id, "status": "finalized", "finalized_at": now}

    async def list_finalized_high_attention_items(
        self, source_day: str
    ) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            """
            SELECT i.*,r.source_day,r.finalized_at,r.model,r.high_attention_run_id
            FROM high_attention_items i
            JOIN high_attention_runs r ON r.high_attention_run_id=i.high_attention_run_id
            WHERE r.source_day=? AND r.status='finalized'
            ORDER BY r.finalized_at DESC,i.sort_order,i.high_attention_item_id
            """,
            (str(source_day),),
        )
        for row in rows:
            row["source_draft_ids"] = loads(row.get("source_draft_ids_json"), [])
        return rows

    async def get_finalized_high_attention_items(
        self, item_ids: list[int]
    ) -> list[dict[str, Any]]:
        clean_ids = list(dict.fromkeys(int(value) for value in item_ids))
        if not clean_ids:
            return []
        placeholders = ",".join("?" for _ in clean_ids)
        rows = await self._fetchall(
            f"""
            SELECT i.*,r.source_day,r.finalized_at,r.high_attention_run_id
            FROM high_attention_items i JOIN high_attention_runs r
              ON r.high_attention_run_id=i.high_attention_run_id
            WHERE r.status='finalized' AND i.high_attention_item_id IN ({placeholders})
            ORDER BY r.finalized_at DESC,i.sort_order,i.high_attention_item_id
            """,
            clean_ids,
        )
        if len(rows) != len(clean_ids):
            raise ValueError("یکی از محورهای پربازتاب انتخاب‌شده نهایی نشده یا پیدا نشد.")
        for row in rows:
            row["source_draft_ids"] = loads(row.get("source_draft_ids_json"), [])
        return rows

    async def create_manual_run_from_drafts(
        self,
        *,
        draft_ids: list[int],
        requested_by: str,
        issue_number: int | None,
        report_mode: str,
        title: str | None,
        introduction: str | None = None,
        high_attention_item_ids: list[int] | None = None,
    ) -> int:
        if not draft_ids:
            raise ValueError("حداقل یک خبر نهایی لازم است.")
        drafts: list[dict[str, Any]] = []
        for draft_id in draft_ids:
            draft = await self.get_editorial_draft(draft_id)
            if not draft or draft.get("status") != "finalized":
                raise ValueError(f"خبر {draft_id} نهایی نشده است.")
            drafts.append(draft)
        selected_high_attention_ids = list(
            dict.fromkeys(int(value) for value in (high_attention_item_ids or []))
        )
        if selected_high_attention_ids:
            await self.get_finalized_high_attention_items(selected_high_attention_ids)
        templates = await self.list_bulletin_templates()
        run_id = await self.create_bulletin_run(
            requested_by=requested_by,
            template_id=int(templates[0]["id"]) if templates else None,
            date_from=None,
            date_to=utc_now(),
            source_chat_ids=None,
            statuses=["approved"],
            filters={
                "issue_number": issue_number,
                "report_mode": report_mode,
                "title": title,
                "introduction": str(introduction or "").strip(),
                "manual_draft_ids": draft_ids,
                "high_attention_item_ids": selected_high_attention_ids,
            },
            provider="manual",
            model=None,
        )
        items: list[dict[str, Any]] = []
        for order, draft in enumerate(drafts, start=1):
            is_event = str(draft.get("content_type") or "") == "event"
            input_rows = draft.get("inputs") or []
            person = (
                await self.get_person(int(draft["person_id"]))
                if draft.get("person_id")
                else None
            )
            items.append(
                {
                    "person_id": draft.get("person_id"),
                    "content_type": "event" if is_event else "person_statement",
                    # The page-layout card still needs a header.  For an
                    # event, its editorial title is the header rather than a
                    # fabricated "unknown person".
                    "person_name": draft.get("person_name") or draft.get("event_title") or draft.get("title"),
                    "position": draft.get("position"),
                    # دستهٔ فهرست اشخاص فقط از شناسنامه می‌آید؛ رویدادها بخش
                    # پایانی مستقل «رویدادهای مهم ایران و جهان» هستند.
                    "category": (
                        "رویدادهای مهم ایران و جهان"
                        if is_event
                        else ((person or {}).get("category") or draft.get("category_name") or "سایر افراد")
                    ),
                    "editorial_category": draft.get("category_name") or (person or {}).get("category") or ("رویدادهای مهم ایران و جهان" if is_event else "سایر"),
                    "registry_bucket": "inside" if draft.get("person_id") else "outside",
                    "topic_id": draft.get("topic_id"),
                    "topic_name": draft.get("topic_name") or "سایر",
                    "main_subject": draft.get("main_subject") or draft.get("topic_name") or "سایر",
                    "statement_location_type": "editorial",
                    "statement_location_label": draft.get("oration_location"),
                    "summary": draft.get("summary_sentence") or draft.get("summary_paragraph") or "",
                    "summary_detailed": draft.get("summary_paragraph") or draft.get("base_text"),
                    "edited_summary": draft.get("summary_sentence") or draft.get("summary_paragraph"),
                    "detail": draft.get("detail") or draft.get("summary_title"),
                    # QR is published only when this editorial-desk link and
                    # its generated QR file travel together into the run.
                    "editorial_source_url": draft.get("source_url"),
                    "editorial_qr_code_path": draft.get("qr_code_path"),
                    "summary_method": "manual_finalized",
                    "status": "approved",
                    "confidence": 1.0,
                    "importance_score": 1.0,
                    "editorial_order": order,
                    "messages": [
                        {
                            "message_id": int(row["message_id"]),
                            "relation_type": "representative" if index == 0 else "evidence",
                        }
                        for index, row in enumerate(input_rows)
                    ],
                }
            )
        await self.replace_bulletin_items(run_id, items)
        await self._execute(
            """
            UPDATE bulletin_runs SET status='completed',current_stage='editorial_review',
            input_message_count=?,processed_message_count=?,processing_report_json=?,
            completed_at=?,updated_at=? WHERE id=?
            """,
            (
                sum(len(draft.get("inputs") or []) for draft in drafts),
                len(items),
                dumps({
                    "mode": "manual_finalization",
                    "draft_ids": draft_ids,
                    "high_attention_item_ids": selected_high_attention_ids,
                }),
                utc_now(),
                utc_now(),
                run_id,
            ),
        )
        return run_id
