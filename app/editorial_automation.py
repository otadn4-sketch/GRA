"""Editorial automation loops have been removed.

The class remains so the dashboard and app lifespan can still import a stable
name.  Start only turns leftover analysis/draft switches off.
"""

from __future__ import annotations

from typing import Any

from .bulletins import BulletinService
from .db import Database


class EditorialAutomationService:
    def __init__(self, db: Database, bulletins: BulletinService) -> None:
        self.db = db
        self.bulletins = bulletins

    async def start(self) -> None:
        await self.db.configure_editorial_automation_stage("analysis", enabled=False)
        await self.db.configure_editorial_automation_stage("draft", enabled=False)

    async def shutdown(self) -> None:
        return None

    async def status(self) -> dict[str, Any]:
        return {
            "analysis_enabled": False,
            "drafts_enabled": False,
            "initial_analysis_enabled": False,
            "service_running": False,
            "removed": True,
        }
