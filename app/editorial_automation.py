"""Background workflow for Garaye's two-stage editorial process.

The first stage is intentionally opt-in and persists its switch in SQLite: an
operator starts it once, then it works every ten minutes across restarts.  The
second stage only consumes analysis that has a registry-backed speaker (or an
independent event), so an unresolved name never turns into a premature draft.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import httpx

from .bulletins import BulletinService
from .db import Database, canonical_key, loads, normalize_persian

logger = logging.getLogger("garaye.editorial_automation")

UNKNOWN = "نامشخص"


@dataclass(frozen=True)
class IdentityResearch:
    query: str
    suggested_name: str | None
    evidence: list[dict[str, str]]


class WebIdentityResolver:
    """Small, dependency-free web lookup used only to prepare human review.

    Search output is never accepted as a new registry person automatically.
    It can only auto-link a person when the returned evidence contains an
    already existing registry name.  All other results stay auditable in the
    human-control queue.
    """

    _anchor_pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    _snippet_pattern = re.compile(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|'
        r'<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "GarayeNewsroom/11.20 (+identity-verification; human-review)"
            },
        )

    @staticmethod
    def _plain(value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", value or "")
        return " ".join(html_lib.unescape(value).split())

    @staticmethod
    def _name_from_title(title: str) -> str | None:
        # Titles usually begin with a full name followed by a dash/role.  This
        # is a suggestion for the reviewer, never an automatic fact.
        head = re.split(r"\s*[|–—،,:]\s*", title, maxsplit=1)[0].strip()
        head = normalize_persian(head)
        words = head.split()
        if 2 <= len(words) <= 7 and len(head) <= 100 and not any(
            token in head for token in ("وزیر", "رئیس", "استاندار", "نماینده", "سخنگو")
        ):
            return head
        return None

    async def research(self, query: str) -> IdentityResearch:
        clean_query = " ".join(normalize_persian(query).split())[:300]
        if not clean_query:
            return IdentityResearch(query="", suggested_name=None, evidence=[])
        try:
            response = await self._client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": clean_query, "kl": "ir-fa"},
            )
            response.raise_for_status()
        except Exception as exc:
            logger.info("Identity web lookup failed for %r: %s", clean_query, exc)
            return IdentityResearch(query=clean_query, suggested_name=None, evidence=[])

        anchors = self._anchor_pattern.findall(response.text)
        snippets = [self._plain(left or right) for left, right in self._snippet_pattern.findall(response.text)]
        evidence: list[dict[str, str]] = []
        for index, (url, raw_title) in enumerate(anchors[:5]):
            title = self._plain(raw_title)
            if not title:
                continue
            evidence.append(
                {"title": title[:300], "url": html_lib.unescape(url)[:1200], "snippet": (snippets[index] if index < len(snippets) else "")[:700]}
            )
        suggested = next((self._name_from_title(item["title"]) for item in evidence if self._name_from_title(item["title"])), None)
        return IdentityResearch(query=clean_query, suggested_name=suggested, evidence=evidence)

    async def close(self) -> None:
        await self._client.aclose()


class EditorialAutomationService:
    ANALYSIS_INTERVAL_SECONDS = 10 * 60
    DRAFT_INTERVAL_SECONDS = 3 * 60 * 60

    def __init__(self, db: Database, bulletins: BulletinService) -> None:
        self.db = db
        self.bulletins = bulletins
        self.identity_resolver = WebIdentityResolver()
        self._stop = asyncio.Event()
        self._analysis_wakeup = asyncio.Event()
        self._draft_wakeup = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._analysis_lock = asyncio.Lock()
        self._draft_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._tasks:
            return
        await self.db.get_editorial_automation_state()
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._analysis_loop(), name="editorial-analysis-loop"),
            asyncio.create_task(self._draft_loop(), name="editorial-draft-loop"),
        ]

    async def shutdown(self) -> None:
        self._stop.set()
        self._analysis_wakeup.set()
        self._draft_wakeup.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        await self.identity_resolver.close()

    async def status(self) -> dict[str, Any]:
        state = await self.db.get_editorial_automation_state()
        state.update(
            {
                "analysis_interval_minutes": self.ANALYSIS_INTERVAL_SECONDS // 60,
                "draft_interval_hours": self.DRAFT_INTERVAL_SECONDS // 3600,
                "service_running": bool(self._tasks),
                "analysis_running": self._analysis_lock.locked(),
                "draft_running": self._draft_lock.locked(),
                "analysis_enabled": bool(state.get("initial_analysis_enabled")),
                "drafts_enabled": bool(state.get("drafts_enabled")),
            }
        )
        return state

    async def enable_analysis(self, *, start_at: str | None = None) -> dict[str, Any]:
        await self.db.configure_editorial_automation_stage(
            "analysis", enabled=True, start_at=start_at
        )
        self._analysis_wakeup.set()
        return await self.status()

    async def disable_analysis(self) -> dict[str, Any]:
        await self.db.configure_editorial_automation_stage("analysis", enabled=False)
        return await self.status()

    async def enable_drafts(self, *, start_at: str | None = None) -> dict[str, Any]:
        await self.db.configure_editorial_automation_stage(
            "draft", enabled=True, start_at=start_at
        )
        self._draft_wakeup.set()
        return await self.status()

    async def disable_drafts(self) -> dict[str, Any]:
        await self.db.configure_editorial_automation_stage("draft", enabled=False)
        return await self.status()

    async def run_analysis_now(self) -> dict[str, Any]:
        state = await self.db.get_editorial_automation_state()
        if not state.get("initial_analysis_enabled"):
            raise RuntimeError("ابتدا روز، ساعت و دکمهٔ شروع تحلیل خودکار را تعیین کنید.")
        return await self._run_analysis_cycle()

    async def run_drafts_now(self) -> dict[str, Any]:
        state = await self.db.get_editorial_automation_state()
        if not state.get("drafts_enabled"):
            raise RuntimeError("ابتدا روز، ساعت و دکمهٔ شروع تولید پیش‌نویس را تعیین کنید.")
        return await self._run_draft_cycle()

    async def reconcile_speakers(self, message_ids: list[int]) -> int:
        """Apply the same registry/web/human-control policy to manual analysis."""
        return await self._research_unresolved_speakers(message_ids)

    async def _wait_or_stop(self, seconds: float, *, wakeup: asyncio.Event | None = None) -> bool:
        if self._stop.is_set():
            return True
        stop_waiter = asyncio.create_task(self._stop.wait())
        tasks = {stop_waiter}
        wake_waiter: asyncio.Task[Any] | None = None
        if wakeup is not None:
            wake_waiter = asyncio.create_task(wakeup.wait())
            tasks.add(wake_waiter)
        try:
            done, pending = await asyncio.wait(tasks, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if wake_waiter and wake_waiter in done:
                wakeup.clear()
            return stop_waiter in done
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def _analysis_loop(self) -> None:
        while not self._stop.is_set():
            state = await self.db.get_editorial_automation_state()
            if state.get("initial_analysis_enabled"):
                try:
                    await self._run_analysis_cycle()
                except Exception:
                    logger.exception("Automated first analysis cycle failed")
                stopped = await self._wait_or_stop(self.ANALYSIS_INTERVAL_SECONDS, wakeup=self._analysis_wakeup)
            else:
                stopped = await self._wait_or_stop(30, wakeup=self._analysis_wakeup)
            if stopped:
                break

    async def _draft_loop(self) -> None:
        # Stage two has its own switch and its own start boundary.  It may be
        # activated only after a human operator has reviewed stage-one output.
        while not self._stop.is_set():
            state = await self.db.get_editorial_automation_state()
            if state.get("drafts_enabled"):
                try:
                    await self._run_draft_cycle()
                except Exception:
                    logger.exception("Automated editorial-draft cycle failed")
                stopped = await self._wait_or_stop(self.DRAFT_INTERVAL_SECONDS, wakeup=self._draft_wakeup)
            else:
                stopped = await self._wait_or_stop(30, wakeup=self._draft_wakeup)
            if stopped:
                break

    async def _run_analysis_cycle(self) -> dict[str, Any]:
        if self._analysis_lock.locked():
            return {"ok": True, "skipped": "already_running", "processed": 0}
        async with self._analysis_lock:
            await self.db.mark_editorial_automation_run("analysis", status="running", started=True)
            try:
                state = await self.db.get_editorial_automation_state()
                ids = await self.db.list_unanalyzed_message_ids(
                    limit=100, start_at=state.get("analysis_start_at")
                )
                if not ids:
                    await self.db.mark_editorial_automation_run("analysis", status="completed", processed=0)
                    return {"ok": True, "processed": 0, "candidates": 0}
                result = await self.bulletins.analyze_selected_messages(ids, actor="automation_stage_one")
                succeeded_ids = [
                    int(item["message_id"])
                    for item in result.get("results", [])
                    if item.get("ok") and item.get("message_id") is not None
                ]
                candidates = await self._research_unresolved_speakers(succeeded_ids)
                await self.db.add_system_event(
                    "editorial_automation", "INFO", "analysis_cycle_completed",
                    "Automated first-stage analysis completed.",
                    {"processed": len(succeeded_ids), "failed": result.get("failed", 0), "candidates": candidates},
                )
                await self.db.mark_editorial_automation_run(
                    "analysis", status="completed", processed=len(succeeded_ids)
                )
                return {"ok": True, "processed": len(succeeded_ids), "candidates": candidates, **result}
            except Exception as exc:
                await self.db.mark_editorial_automation_run(
                    "analysis", status="failed", error=str(exc)
                )
                raise

    async def _research_unresolved_speakers(self, message_ids: list[int]) -> int:
        tags = await self.db.list_unresolved_speaker_tags(message_ids)
        if not tags:
            return 0
        semaphore = asyncio.Semaphore(5)

        async def resolve_tag(tag: dict[str, Any]) -> bool:
            name = normalize_persian(str(tag.get("speaker_name") or "")).strip()
            position = normalize_persian(str(tag.get("position") or "")).strip()
            # Only bind when the registry match is unique; ambiguous aliases stay
            # for human control instead of inventing an identity.
            direct = (
                await self.db.find_unique_person(name)
                if name and name != UNKNOWN
                else None
            )
            if direct:
                return await self.db.bind_speaker_tag_to_person(
                    int(tag["tag_id"]), int(direct["person_id"]), match_method="registry_exact"
                )
            query = name if name and name != UNKNOWN else position
            if not query or query == UNKNOWN:
                return False
            async with semaphore:
                research = await self.identity_resolver.research(query)
            evidence_text = " ".join(
                " ".join(item.get(key, "") for key in ("title", "snippet"))
                for item in research.evidence
            )
            matched = await self.db.find_person_in_evidence(evidence_text)
            if matched:
                return await self.db.bind_speaker_tag_to_person(
                    int(tag["tag_id"]), int(matched["person_id"]), match_method="web_registry_match"
                )
            candidate_name = name if name and name != UNKNOWN else (research.suggested_name or position)
            await self.db.create_person_candidate(
                candidate_name,
                confidence=0.62 if research.evidence else 0.3,
                message_id=int(tag["message_id"]),
                tag_id=int(tag["tag_id"]),
                sample_text=str(tag.get("text") or "")[:1800] or None,
                sample_caption=str(tag.get("caption") or "")[:1800] or None,
                candidate_kind="position_only" if (not name or name == UNKNOWN) else "speaker",
                detected_position=position if position != UNKNOWN else None,
                suggested_name=research.suggested_name,
                web_query=research.query,
                web_evidence=research.evidence,
            )
            return True

        results = await asyncio.gather(*(resolve_tag(tag) for tag in tags), return_exceptions=True)
        return sum(1 for item in results if item is True)

    @staticmethod
    def _text_of(row: dict[str, Any]) -> str:
        return " ".join(str(row.get("text") or row.get("caption") or "").split())

    async def _run_draft_cycle(self) -> dict[str, Any]:
        if self._draft_lock.locked():
            return {"ok": True, "skipped": "already_running", "processed": 0}
        async with self._draft_lock:
            await self.db.mark_editorial_automation_run("draft", status="running", started=True)
            try:
                state = await self.db.get_editorial_automation_state()
                rows = await self.db.list_auto_editorial_candidates(
                    limit=240, start_at=state.get("draft_start_at")
                )
                if not rows:
                    await self.db.mark_editorial_automation_run("draft", status="completed", processed=0)
                    return {"ok": True, "processed": 0}
                created = 0
                person_groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
                event_rows: list[dict[str, Any]] = []
                for row in rows:
                    if str(row.get("analysis_content_type")) == "event":
                        event_rows.append(row)
                    elif row.get("detected_person_id"):
                        person_groups[(int(row["detected_person_id"]), canonical_key(row.get("analysis_general_topic")))].append(row)

                for (person_id, _), items in person_groups.items():
                    # Keep independently reviewable batches bounded even if a
                    # prolific speaker has many messages before the next run.
                    for start in range(0, len(items), 30):
                        batch = items[start : start + 30]
                        created += await self._create_and_generate_person_draft(person_id, batch)
                for row in event_rows:
                    created += await self._create_and_generate_event_draft(row)
                await self.db.add_system_event(
                    "editorial_automation", "INFO", "draft_cycle_completed",
                    "Automated second-stage draft cycle completed.",
                    {"drafts_created": created},
                )
                await self.db.mark_editorial_automation_run("draft", status="completed", processed=created)
                return {"ok": True, "processed": created}
            except Exception as exc:
                await self.db.mark_editorial_automation_run("draft", status="failed", error=str(exc))
                raise

    async def _create_and_generate_person_draft(self, person_id: int, rows: list[dict[str, Any]]) -> int:
        person = await self.db.get_person(person_id)
        if not person or not rows:
            return 0
        topic_name = next((str(row.get("analysis_general_topic") or "").strip() for row in rows if row.get("analysis_general_topic")), UNKNOWN)
        topic_id = next((row.get("detected_topic_id") for row in rows if row.get("detected_topic_id")), None)
        subjects = [str(row.get("analysis_main_subject") or "").strip() for row in rows if row.get("analysis_main_subject")]
        main_subject = subjects[0] if subjects else topic_name
        matching_tag = next(
            (
                tag
                for row in rows
                for tag in row.get("speaker_tags", [])
                if int(tag.get("person_id") or 0) == person_id
            ),
            {},
        )
        location = " · ".join(
            value for value in (
                matching_tag.get("expression_method_type"), matching_tag.get("expression_method_context")
            ) if value and value != UNKNOWN
        ) or None
        base_text = "\n\n".join(dict.fromkeys(filter(None, (self._text_of(row) for row in rows))))
        draft_id = await self.db.create_editorial_draft(
            title=f"{person['full_name']} — {topic_name}",
            person_id=person_id,
            person_name=str(person["full_name"]),
            position=str(person.get("position") or matching_tag.get("position") or "") or None,
            topic_id=int(topic_id) if topic_id else None,
            topic_name=topic_name,
            main_subject=main_subject,
            message_inputs=[{"message_id": int(row["id"]), "sort_order": index} for index, row in enumerate(rows)],
            base_text=base_text,
            created_by="automation_stage_two",
            category_name=str(person.get("category") or "") or None,
            oration_location=location,
        )
        await self._generate_and_persist(draft_id)
        return 1

    async def _create_and_generate_event_draft(self, row: dict[str, Any]) -> int:
        entities = loads(row.get("analysis_event_entities_json"), [])
        if not isinstance(entities, list):
            entities = []
        title = str(row.get("analysis_event_title") or row.get("analysis_main_subject") or "رویداد مهم").strip()
        draft_id = await self.db.create_editorial_draft(
            title=title,
            person_id=None,
            person_name=None,
            position=None,
            topic_id=int(row["detected_topic_id"]) if row.get("detected_topic_id") else None,
            topic_name=str(row.get("analysis_general_topic") or row.get("detected_topic_name") or UNKNOWN),
            main_subject=str(row.get("analysis_main_subject") or title),
            message_inputs=[{"message_id": int(row["id"]), "sort_order": 0}],
            base_text=self._text_of(row),
            created_by="automation_stage_two",
            category_name="وقایع و رویدادهای مهم ایران و جهان",
            content_type="event",
            event_title=title,
            event_entities=[str(value) for value in entities],
            event_location=str(row.get("analysis_event_location") or "") or None,
            event_time=str(row.get("analysis_event_time") or "") or None,
        )
        await self._generate_and_persist(draft_id)
        return 1

    async def _generate_and_persist(self, draft_id: int) -> None:
        result = await self.bulletins.generate_editorial_content(draft_id, kind="all")
        draft = await self.db.get_editorial_draft(draft_id)
        if not draft:
            return
        await self.db.save_editorial_draft_version(
            draft_id,
            title=draft.get("title"),
            base_text=str(result.get("base_text") or draft.get("base_text") or ""),
            summary_paragraph=result.get("summary_paragraph"),
            summary_sentence=result.get("summary_sentence"),
            summary_title=result.get("summary_title"),
            detail=result.get("detail") or result.get("summary_title"),
            category_name=draft.get("category_name"),
            person_id=draft.get("person_id"),
            person_name=draft.get("person_name"),
            topic_id=draft.get("topic_id"),
            topic_name=draft.get("topic_name"),
            main_subject=draft.get("main_subject"),
            oration_location=draft.get("oration_location"),
            source_url=draft.get("source_url"),
            expected_version=int(draft["current_version"]),
            change_reason="تولید خودکار مرحلهٔ دوم",
            actor="automation_stage_two",
        )
