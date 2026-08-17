from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings

logger = logging.getLogger("garaye.scheduler")


class BulletinScheduler:
    def __init__(self, db: Any, service: Any, settings: Settings) -> None:
        self.db = db
        self.service = service
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self._started = False

    async def start(self) -> None:
        if not self.settings.scheduler_enabled or self._started:
            return
        self.scheduler.start()
        self._started = True
        await self.reload()

    async def reload(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        if not self._started:
            return
        self.scheduler.remove_all_jobs()
        for schedule in await self.db.list_bulletin_schedules(enabled_only=True):
            try:
                trigger = CronTrigger.from_crontab(
                    str(schedule["cron_expression"]),
                    timezone=str(schedule.get("timezone") or "Asia/Tehran"),
                )
                self.scheduler.add_job(
                    self.service.execute_schedule,
                    trigger=trigger,
                    args=[schedule],
                    id=f"bulletin-{schedule['id']}",
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                    misfire_grace_time=900,
                )
            except Exception:
                logger.exception("Could not load schedule %s", schedule.get("id"))

    async def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False
