"""Block the Selenium crawler from posting near-duplicate news to the destination.

The worker itself lives outside git as ``crawler/bale_crawler_api_sender.py``.
This module monkey-patches Bot API HTTP clients in that process so copy/send
calls are skipped when the same origin or an 80% similar text was already sent.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .config import PROJECT_ROOT, load_settings
from .near_duplicate import (
    ANALYSIS_NEAR_DUPLICATE_THRESHOLD,
    is_near_duplicate,
    message_body,
    text_sha256,
)

logger = logging.getLogger("garaye.crawler_send_guard")

SEND_METHODS = {
    "sendmessage",
    "sendphoto",
    "sendvideo",
    "sendanimation",
    "senddocument",
    "sendaudio",
    "sendvoice",
    "sendmediagroup",
    "copymessage",
    "forwardmessage",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def bale_api_method(url: str | None) -> str | None:
    path = urlparse(str(url or "")).path
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    method = parts[-1].split("?")[0].strip().lower()
    return method or None


def payload_from_request_kwargs(kwargs: dict[str, Any] | None) -> dict[str, Any]:
    data = kwargs or {}
    body = data.get("json")
    if isinstance(body, dict):
        return body
    form = data.get("data")
    if isinstance(form, dict):
        return {str(key): form[key] for key in form}
    if isinstance(form, (bytes, str)):
        try:
            parsed = json.loads(form)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    content = data.get("content")
    if isinstance(content, (bytes, str)):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    params = data.get("params")
    if isinstance(params, dict):
        return params
    return {}


def origin_key(payload: dict[str, Any]) -> str | None:
    from_chat = payload.get("from_chat_id") or payload.get("fromChatId")
    message_id = payload.get("message_id") or payload.get("messageId")
    if from_chat not in (None, "") and message_id not in (None, ""):
        return f"origin:{from_chat}:{message_id}"
    url = str(payload.get("url") or payload.get("message_url") or "").strip()
    if not url:
        text = str(payload.get("text") or payload.get("caption") or "")
        match = _first_public_url(text)
        url = match or ""
    if url:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "bale" in host or host.endswith("ble.ir"):
            return f"url:{parsed.netloc}{parsed.path}".rstrip("/")
    return None


def _first_public_url(text: str) -> str | None:
    for token in text.split():
        if "ble.ir/" in token or "bale.ai/" in token or "bale.ir/" in token:
            return token.strip("()[]<>.,;")
    return None


def extract_send_text(method: str, payload: dict[str, Any]) -> str:
    if method in {"copymessage", "forwardmessage"}:
        return str(payload.get("caption") or payload.get("text") or "").strip()
    if method == "sendmediagroup":
        media = payload.get("media")
        if isinstance(media, str):
            try:
                media = json.loads(media)
            except json.JSONDecodeError:
                media = []
        if isinstance(media, list):
            captions = [
                str(item.get("caption") or "")
                for item in media
                if isinstance(item, dict)
            ]
            return "\n".join(part for part in captions if part).strip()
    return str(payload.get("text") or payload.get("caption") or "").strip()


class CrawlerSendLedger:
    """SQLite-backed memory of crawler sends plus recent ingested news."""

    def __init__(self, database_path: str, *, threshold: float = ANALYSIS_NEAR_DUPLICATE_THRESHOLD) -> None:
        self.database_path = database_path
        self.threshold = threshold
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _ensure_table(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawler_send_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    origin_key TEXT,
                    text_sha256 TEXT,
                    text TEXT,
                    method TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_crawler_send_log_origin "
                "ON crawler_send_log(origin_key, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_crawler_send_log_hash "
                "ON crawler_send_log(text_sha256, created_at DESC)"
            )
            conn.commit()
        finally:
            conn.close()

    def should_skip(self, *, method: str, payload: dict[str, Any]) -> tuple[bool, str]:
        text = extract_send_text(method, payload)
        key = origin_key(payload)
        digest = text_sha256(text)
        conn = self._connect()
        try:
            if key:
                row = conn.execute(
                    "SELECT id FROM crawler_send_log WHERE origin_key=? LIMIT 1",
                    (key,),
                ).fetchone()
                if row:
                    return True, "origin_already_sent"
            if digest:
                hashed = conn.execute(
                    "SELECT id FROM crawler_send_log WHERE text_sha256=? LIMIT 1",
                    (digest,),
                ).fetchone()
                if hashed:
                    return True, "exact_text_already_sent"
            if text:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                recent_sends = conn.execute(
                    """
                    SELECT text FROM crawler_send_log
                    WHERE created_at>=? AND text IS NOT NULL AND TRIM(text)<>''
                    ORDER BY id DESC LIMIT 800
                    """,
                    (cutoff,),
                ).fetchall()
                for row in recent_sends:
                    if is_near_duplicate(text, row["text"], threshold=self.threshold):
                        return True, "near_duplicate_already_sent"
            try:
                return self._skip_against_messages(conn, key=key, digest=digest, text=text)
            except sqlite3.OperationalError:
                return False, "new"
        finally:
            conn.close()

    def _skip_against_messages(
        self,
        conn: sqlite3.Connection,
        *,
        key: str | None,
        digest: str | None,
        text: str,
    ) -> tuple[bool, str]:
        if key and key.startswith("url:"):
            url = key[4:]
            ingested = conn.execute(
                """
                SELECT id FROM messages
                WHERE forwarded_origin_url=? OR message_url=?
                LIMIT 1
                """,
                (url, url),
            ).fetchone()
            if ingested:
                return True, "origin_already_ingested"
        elif key and key.startswith("origin:"):
            _prefix, from_chat, message_id = (key.split(":", 2) + ["", ""])[:3]
            if from_chat and message_id:
                ingested = conn.execute(
                    """
                    SELECT id FROM messages
                    WHERE CAST(forwarded_origin_chat_id AS TEXT)=?
                      AND CAST(forwarded_origin_message_id AS TEXT)=?
                    LIMIT 1
                    """,
                    (from_chat, message_id),
                ).fetchone()
                if ingested:
                    return True, "origin_already_ingested"
        if digest:
            hashed_messages = conn.execute(
                "SELECT id FROM messages WHERE text_sha256=? LIMIT 1",
                (digest,),
            ).fetchone()
            if hashed_messages:
                return True, "exact_text_already_ingested"
        if text:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent_messages = conn.execute(
                """
                SELECT text, caption FROM messages
                WHERE COALESCE(published_at, received_at, created_at)>=?
                  AND (
                    (text IS NOT NULL AND TRIM(text)<>'')
                    OR (caption IS NOT NULL AND TRIM(caption)<>'')
                  )
                ORDER BY id DESC LIMIT 800
                """,
                (cutoff,),
            ).fetchall()
            for row in recent_messages:
                if is_near_duplicate(text, message_body(dict(row)), threshold=self.threshold):
                    return True, "near_duplicate_already_ingested"
        return False, "new"

    def record(self, *, method: str, payload: dict[str, Any]) -> None:
        text = extract_send_text(method, payload)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO crawler_send_log(origin_key, text_sha256, text, method, created_at)
                VALUES (?,?,?,?,?)
                """,
                (origin_key(payload), text_sha256(text), text or None, method, _utc_now()),
            )
            conn.commit()
        finally:
            conn.close()


def skipped_api_result(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    chat_id = payload.get("chat_id") or payload.get("chatId")
    return {
        "ok": True,
        "result": {
            "message_id": 0,
            "chat": {"id": chat_id} if chat_id not in (None, "") else {},
            "garaye_skipped": True,
            "method": method,
        },
    }


class _RequestsLikeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self.ok = True
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def _inspect_send(
    ledger: CrawlerSendLedger, url: str, kwargs: dict[str, Any]
) -> tuple[str | None, dict[str, Any], dict[str, Any] | None]:
    api_method = bale_api_method(url)
    payload = payload_from_request_kwargs(kwargs)
    if api_method not in SEND_METHODS:
        return api_method, payload, None
    skip, reason = ledger.should_skip(method=api_method, payload=payload)
    if skip:
        logger.info("Crawler skipped %s (%s)", api_method, reason)
        return api_method, payload, skipped_api_result(api_method, payload)
    return api_method, payload, None


def install(ledger: CrawlerSendLedger | None = None) -> CrawlerSendLedger:
    """Patch requests/httpx so duplicate Bot API sends never leave this process."""

    settings = load_settings()
    active = ledger or CrawlerSendLedger(str(settings.database_path), threshold=settings.analysis_near_duplicate_threshold)
    try:
        import requests

        original = requests.sessions.Session.request

        def wrapped(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            api_method, payload, skipped = _inspect_send(active, str(url or ""), kwargs)
            if skipped is not None:
                return _RequestsLikeResponse(skipped)
            response = original(self, method, url, *args, **kwargs)
            if api_method in SEND_METHODS and getattr(response, "ok", False):
                active.record(method=api_method or "", payload=payload)
            return response

        requests.sessions.Session.request = wrapped  # type: ignore[method-assign]
    except Exception:
        logger.exception("Could not wrap requests for crawler send guard")

    try:
        import httpx

        original_sync = httpx.Client.request

        def wrapped_sync(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            api_method, payload, skipped = _inspect_send(active, str(url or ""), kwargs)
            if skipped is not None:
                return httpx.Response(200, json=skipped, request=httpx.Request(method, url))
            response = original_sync(self, method, url, *args, **kwargs)
            if api_method in SEND_METHODS and int(getattr(response, "status_code", 0) or 0) < 400:
                active.record(method=api_method or "", payload=payload)
            return response

        httpx.Client.request = wrapped_sync  # type: ignore[method-assign]
    except Exception:
        logger.exception("Could not wrap httpx for crawler send guard")

    logger.info("Crawler send guard installed (root=%s)", PROJECT_ROOT)
    return active
