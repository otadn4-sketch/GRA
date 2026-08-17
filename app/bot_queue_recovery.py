"""Bounded recovery of source messages retained by Bale's Bot API.

This service deliberately has no Selenium, Firefox profile, or crawler-channel
dependency.  It can only recover source messages that are still available in
the Bot API update queue or whose raw update envelope was retained locally in
``bot_updates``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .bale import BaleClient
from .db import Database

logger = logging.getLogger("prasad.bot_queue_recovery")

RegularUpdateProcessor = Callable[[dict[str, Any]], Awaitable[None]]
SourceMatcher = Callable[[dict[str, Any]], Awaitable[bool]]
SourceProcessor = Callable[[dict[str, Any], str, int], Awaitable[int | None]]


@dataclass(frozen=True)
class SourceCandidate:
    update_id: int | None
    message: dict[str, Any]
    published_at: str
    edited: bool


def _message_from_update(update: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    for key in ("message", "channel_post"):
        value = update.get(key)
        if isinstance(value, dict):
            return value, False
    for key in ("edited_message", "edited_channel_post"):
        value = update.get(key)
        if isinstance(value, dict):
            return value, True
    return None, False


def _source_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        numeric = float(value)
        if numeric > 100_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class BotQueueRecoveryService:
    """Replay only missing monitored-source messages from Bot API evidence."""

    def __init__(
        self,
        db: Database,
        bale: BaleClient,
        *,
        process_regular_update: RegularUpdateProcessor,
        source_matches: SourceMatcher,
        process_source_message: SourceProcessor,
        ingestion_lock: asyncio.Lock,
    ) -> None:
        self.db = db
        self.bale = bale
        self.process_regular_update = process_regular_update
        self.source_matches = source_matches
        self.process_source_message = process_source_message
        self.ingestion_lock = ingestion_lock

    async def _candidate(
        self, update: dict[str, Any], cutoff: datetime
    ) -> SourceCandidate | None:
        message, edited = _message_from_update(update)
        if not message:
            return None
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("id") is None or message.get("message_id") is None:
            return None
        source_time = _source_time(message.get("date"))
        if source_time is None or source_time < cutoff:
            return None
        if not await self.source_matches(chat):
            return None
        raw_update_id = update.get("update_id")
        try:
            update_id = int(raw_update_id) if raw_update_id is not None else None
        except (TypeError, ValueError):
            update_id = None
        return SourceCandidate(
            update_id=update_id,
            message=message,
            published_at=source_time.isoformat(),
            edited=edited,
        )

    @staticmethod
    def _detail(candidate: SourceCandidate) -> dict[str, Any]:
        chat = candidate.message.get("chat") or {}
        username = str(chat.get("username") or "").strip().lstrip("@")
        channel = (
            str(chat.get("title") or "").strip()
            or (f"@{username}" if username else "منبع پایش")
        )
        text = str(candidate.message.get("text") or candidate.message.get("caption") or "").strip()
        return {
            "channel": channel,
            "published_at": candidate.published_at,
            "text": text[:260],
            "update_id": candidate.update_id,
        }

    async def _recover_candidate(
        self, candidate: SourceCandidate, run_id: int
    ) -> tuple[str, dict[str, Any] | None]:
        chat = candidate.message["chat"]
        existing = await self.db.get_message_by_source(
            int(chat["id"]), int(candidate.message["message_id"])
        )
        if existing:
            return "existing", None

        if candidate.edited:
            message_id = await self.db.upsert_message(
                candidate.message, received_at=candidate.published_at
            )
        else:
            message_id = await self.process_source_message(
                candidate.message, candidate.published_at, run_id
            )
        if message_id is None:
            # System/control messages in a monitored group are intentionally
            # ignored by the normal source handler as well.
            return "ignored", None
        await self.db.add_action(
            int(message_id),
            None,
            "bot_queue_recovered_48h",
            details={
                "run_id": int(run_id),
                "update_id": candidate.update_id,
                "published_at": candidate.published_at,
                "from": "bot_api_queue_or_archive",
            },
        )
        return "imported", self._detail(candidate)

    async def _process_queue_update(
        self,
        update: dict[str, Any],
        *,
        cutoff: datetime,
        run_id: int,
    ) -> tuple[str | None, dict[str, Any] | None]:
        candidate = await self._candidate(update, cutoff)
        if candidate:
            # Capture the exact Bot API envelope before acknowledging the
            # update.  This makes a later rerun possible if control-card
            # attachment itself fails.
            if candidate.update_id is None:
                raise ValueError("آپدیت بله شناسهٔ معتبر ندارد.")
            await self.db.store_update(update)
            outcome, detail = await self._recover_candidate(candidate, run_id)
            await self.db.mark_update_processed(candidate.update_id)
            return outcome, detail

        # Non-source updates must retain normal bot behavior; otherwise
        # consuming the queue here could silently drop callbacks or commands.
        await self.process_regular_update(update)
        return None, None

    async def _drain_bot_queue(
        self,
        *,
        cutoff: datetime,
        run_id: int,
        seen_source_updates: set[int],
        totals: dict[str, int],
        imported_items: list[dict[str, Any]],
    ) -> int:
        offset: int | None = None
        received = 0
        while True:
            updates = await self.bale.get_updates(offset=offset, timeout=0)
            if not updates:
                return received
            for update in updates:
                if not isinstance(update, dict) or update.get("update_id") is None:
                    raise ValueError("پاسخ getUpdates شامل آپدیت نامعتبر است.")
                update_id = int(update["update_id"])
                candidate = await self._candidate(update, cutoff)
                if candidate:
                    seen_source_updates.add(update_id)
                    totals["scanned"] += 1
                try:
                    outcome, detail = await self._process_queue_update(
                        update, cutoff=cutoff, run_id=run_id
                    )
                except Exception:
                    if candidate:
                        totals["failed"] += 1
                    raise
                if outcome == "existing":
                    totals["existing"] += 1
                elif outcome == "imported":
                    totals["imported"] += 1
                    if detail:
                        imported_items.append(detail)

                # Passing this offset on the next call acknowledges only the
                # update that was safely stored/processed above.
                offset = update_id + 1
                await self.db.set_setting("polling_offset", str(offset))
                received += 1

    async def run(self, *, run_id: int, hours: int = 48) -> dict[str, int]:
        safe_hours = max(1, min(168, int(hours)))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=safe_hours)
        totals = {"scanned": 0, "existing": 0, "imported": 0, "failed": 0}
        imported_items: list[dict[str, Any]] = []
        queue_updates = 0
        archive_considered = 0
        seen_source_updates: set[int] = set()

        await self.db.mark_crawler_recovery_running(run_id)
        try:
            # The archive query is made before acquiring the receive lock; the
            # actual source check is still performed inside the lock so it
            # cannot race with the polling loop's writes.
            archived = await self.db.list_bot_updates_for_recovery(
                received_since=cutoff.isoformat()
            )
            async with self.ingestion_lock:
                if not self.bale.enabled:
                    raise RuntimeError("توکن ربات بله تنظیم نشده است؛ صف API قابل دریافت نیست.")
                queue_updates = await self._drain_bot_queue(
                    cutoff=cutoff,
                    run_id=run_id,
                    seen_source_updates=seen_source_updates,
                    totals=totals,
                    imported_items=imported_items,
                )

                for archived_update in archived:
                    payload = archived_update.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    raw_update_id = payload.get("update_id")
                    try:
                        update_id = int(raw_update_id) if raw_update_id is not None else None
                    except (TypeError, ValueError):
                        update_id = None
                    if update_id is not None and update_id in seen_source_updates:
                        continue
                    candidate = await self._candidate(payload, cutoff)
                    if not candidate:
                        continue
                    if candidate.update_id is not None:
                        seen_source_updates.add(candidate.update_id)
                    archive_considered += 1
                    totals["scanned"] += 1
                    try:
                        outcome, detail = await self._recover_candidate(candidate, run_id)
                        if candidate.update_id is not None:
                            await self.db.mark_update_processed(candidate.update_id)
                    except Exception as exc:
                        totals["failed"] += 1
                        if candidate.update_id is not None:
                            await self.db.mark_update_failed(candidate.update_id, str(exc))
                        logger.exception("Bot-queue archive recovery failed for update %s", candidate.update_id)
                        continue
                    if outcome == "existing":
                        totals["existing"] += 1
                    elif outcome == "imported":
                        totals["imported"] += 1
                        if detail:
                            imported_items.append(detail)

            details = {
                "mode": "bot_api_queue_and_local_update_archive",
                "hours": safe_hours,
                "cutoff_utc": cutoff.isoformat(),
                "queue_updates_received": queue_updates,
                "archive_source_updates_checked": archive_considered,
                "imported_messages": imported_items[:100],
                "imported_messages_truncated": len(imported_items) > 100,
            }
            await self.db.finish_crawler_recovery_run(
                run_id,
                status="completed",
                **totals,
                details=details,
            )
            await self.db.add_system_event(
                "bot",
                "INFO",
                "bot_queue_recovery_completed",
                "Recent Bot API queue recovery completed.",
                {"run_id": run_id, **totals, "queue_updates": queue_updates},
            )
            return totals
        except Exception as exc:
            await self.db.finish_crawler_recovery_run(
                run_id,
                status="failed",
                **totals,
                details={
                    "mode": "bot_api_queue_and_local_update_archive",
                    "hours": safe_hours,
                    "cutoff_utc": cutoff.isoformat(),
                    "queue_updates_received": queue_updates,
                    "archive_source_updates_checked": archive_considered,
                    "imported_messages": imported_items[:100],
                },
                error_text=f"{type(exc).__name__}: {exc}",
            )
            await self.db.add_system_event(
                "bot",
                "ERROR",
                "bot_queue_recovery_failed",
                "Recent Bot API queue recovery failed.",
                {"run_id": run_id, **totals, "error": f"{type(exc).__name__}: {exc}"},
            )
            raise
