from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx

from .ai_key_pool import AIKeyPool, NoAvailableAPIKey
from .config import Settings
from .db import Database, dumps
from .persian_text import canonical_key, keywords, normalize_persian, split_sentences


PIPELINE_VERSION = "11.0"
PROMPT_VERSION = "garaye-analysis-v11.0"


@dataclass(frozen=True)
class PersonMatch:
    person_id: int | None
    candidate_id: int | None
    name: str
    registry_bucket: str
    method: str
    confidence: float
    position: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class TopicMatch:
    topic_id: int
    name: str
    score: float
    is_primary: bool = True


def _clean_json_output(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start_candidates = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if start_candidates:
        text = text[min(start_candidates) :]
    end = max(text.rfind("}"), text.rfind("]"))
    if end >= 0:
        text = text[: end + 1]
    return text.strip()


def _extract_output_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or item.get("content") or "")
                for item in content
                if isinstance(item, dict)
            )
    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"])
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for block in output:
            if not isinstance(block, dict):
                continue
            content = block.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or item.get("output_text") or ""))
        return "".join(parts)
    return str(payload.get("text") or "")


def _jaccard(a: str, b: str) -> float:
    left, right = keywords(a), keywords(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _trim_words(value: str, limit: int) -> str:
    words = normalize_persian(value).split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip("،؛:") + "…"


class HybridBulletinPipeline:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        client: httpx.AsyncClient,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self.ai_key_pool = AIKeyPool(
            settings.ai_api_keys,
            max_concurrency=settings.ai_max_concurrency,
            concurrency_per_key=settings.ai_concurrency_per_key,
            circuit_breaker_seconds=settings.ai_circuit_breaker_seconds,
        )

    async def execute(
        self, run: dict[str, Any], template: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        run_id = int(run["id"])
        messages = await self.db.select_messages_for_run(
            run, limit=self.settings.bulletin_max_messages
        )
        await self.db.update_bulletin_run_report(
            run_id,
            report={"stage": "analysis", "input": len(messages)},
            input_count=len(messages),
            processed_count=0,
            skipped_count=0,
            warning_count=0,
            stage="analysis",
        )
        topics = await self.db.list_topics_with_keywords()
        people = await self.db.list_people()
        alias_index = self._alias_index(people)
        topic_index = self._topic_index(topics)
        analyzed: list[dict[str, Any]] = []
        skipped = 0
        warnings: list[str] = []

        for message in messages:
            text = normalize_persian(
                message.get("text") or message.get("caption") or ""
            )
            if not text:
                skipped += 1
                await self._save_message_analysis(
                    message,
                    person=None,
                    topic=None,
                    relevance_status="excluded",
                    relevance_reason="empty_message",
                    ai_status="not_applicable",
                )
                continue
            person = await self._match_person(message, text, alias_index)
            topic = self._match_topic(text, topic_index)
            source_quality = self._source_quality(message)
            importance = self._importance(text, source_quality)
            ai_payload: dict[str, Any] | None = None
            ai_status = "disabled"
            if self._ai_ready and self.settings.ai_enrichment_enabled:
                try:
                    ai_payload = await self._analyze_with_ai(
                        run_id, message, text, people, topics
                    )
                    ai_status = "validated"
                    person = await self._apply_ai_person(
                        message, ai_payload, person, alias_index
                    )
                    topic = self._apply_ai_topic(ai_payload, topic, topic_index)
                except Exception as exc:
                    warnings.append(f"پیام {message['id']}: {type(exc).__name__}")
                    ai_status = "needs_review"
            location = str(
                message.get("oration_text")
                or (
                    "در کانال شخصی"
                    if message.get("source_kind") == "monitored_channel"
                    else "در گروه پشتیبان گرایه"
                )
            )
            await self._save_message_analysis(
                message,
                person=person,
                topic=topic,
                relevance_status="usable",
                relevance_reason="approved_source",
                ai_status=ai_status,
                ai_payload=ai_payload,
                source_quality=source_quality,
                importance=importance,
                location=location,
            )
            analyzed.append(
                {
                    **message,
                    "normalized_text": text,
                    "person": person,
                    "topic": topic,
                    "source_quality": source_quality,
                    "importance_score": importance,
                    "statement_location_label": location,
                    "ai_status": ai_status,
                }
            )

        clusters: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for message in analyzed:
            person: PersonMatch = message["person"]
            topic: TopicMatch = message["topic"]
            key = (
                f"id:{person.person_id}"
                if person.person_id is not None
                else f"name:{canonical_key(person.name)}",
                topic.topic_id,
            )
            clusters[key].append(message)

        items: list[dict[str, Any]] = []
        for order, cluster_messages in enumerate(clusters.values(), start=1):
            cluster_messages.sort(
                key=lambda item: (
                    -float(item.get("importance_score") or 0),
                    str(item.get("published_at") or ""),
                )
            )
            representative, relations = self._deduplicate(cluster_messages)
            person = representative["person"]
            topic = representative["topic"]
            summary_data, degraded, _ = await self._summarize_cluster(
                run_id=run_id,
                template=template,
                person=person,
                topic=topic,
                topic_keywords=[],
                messages=cluster_messages,
            )
            if degraded:
                warnings.append(
                    f"خلاصه خوشه {person.name}/{topic.name} به‌صورت استخراجی تولید شد."
                )
            items.append(
                {
                    "person_id": person.person_id,
                    "person_candidate_id": person.candidate_id,
                    "person_name": person.name,
                    "position": person.position,
                    "category": person.category,
                    "registry_bucket": person.registry_bucket,
                    "topic_id": topic.topic_id,
                    "topic_name": topic.name,
                    "statement_type": str(
                        (summary_data.get("statement_type") or "اظهارنظر")
                    ),
                    "statement_location_type": "reported",
                    "statement_location_label": representative.get(
                        "statement_location_label"
                    ),
                    "summary": summary_data["summary"],
                    "summary_detailed": summary_data.get("summary_detailed")
                    or summary_data["summary"],
                    "summary_method": summary_data["summary_method"],
                    "summary_version": summary_data["summary_version"],
                    "status": "review_pending",
                    "confidence": summary_data["confidence"],
                    "confidence_breakdown": summary_data.get(
                        "confidence_breakdown", {}
                    ),
                    "consensus_method": summary_data.get("consensus_method"),
                    "selected_sentences": summary_data.get(
                        "selected_sentences", []
                    ),
                    "pipeline_versions": {
                        "pipeline": PIPELINE_VERSION,
                        "analysis": PROMPT_VERSION,
                        "summary": summary_data["summary_version"],
                    },
                    "importance_score": max(
                        float(message.get("importance_score") or 0)
                        for message in cluster_messages
                    ),
                    "editorial_order": order,
                    "review_reason": (
                        "خروجی هوش نیازمند تأیید کاربر است."
                        if any(
                            message.get("ai_status") == "needs_review"
                            for message in cluster_messages
                        )
                        else None
                    ),
                    "messages": relations,
                }
            )
        item_ids = await self.db.replace_bulletin_items(run_id, items)
        report = {
            "pipeline_version": PIPELINE_VERSION,
            "input_messages": len(messages),
            "processed_messages": len(analyzed),
            "skipped_messages": skipped,
            "items": len(item_ids),
            "warning_count": len(warnings),
            "warnings": warnings[:100],
            "ai_enabled": self._ai_ready,
            "ai_key_count": self.ai_key_pool.key_count,
        }
        await self.db.update_bulletin_run_report(
            run_id,
            report=report,
            input_count=len(messages),
            processed_count=len(analyzed),
            skipped_count=skipped,
            warning_count=len(warnings),
            stage="editorial_review",
        )
        output = json.dumps(
            {
                "run_id": run_id,
                "status": "ready_for_editorial_review",
                "items": [
                    {
                        "item_id": item_id,
                        "person": item["person_name"],
                        "topic": item["topic_name"],
                        "summary": item["summary"],
                    }
                    for item_id, item in zip(item_ids, items)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        return output, report

    @property
    def _ai_ready(self) -> bool:
        return bool(
            self.settings.ai_provider != "disabled"
            and self.settings.ai_model
            and self.ai_key_pool.configured
        )

    def _alias_index(
        self, people: list[dict[str, Any]]
    ) -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        for person in people:
            aliases = [person.get("full_name")]
            aliases.extend(
                part.strip()
                for part in str(person.get("aliases") or "").split("|")
                if part.strip()
            )
            for alias in aliases:
                key = canonical_key(alias)
                if key:
                    result.append((key, person))
        result.sort(key=lambda item: len(item[0]), reverse=True)
        return result

    def _topic_index(
        self, topics: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], list[str]]]:
        result: list[tuple[dict[str, Any], list[str]]] = []
        for topic in topics:
            words = [
                canonical_key(
                    item.get("keyword") if isinstance(item, dict) else str(item)
                )
                for item in topic.get("keywords") or []
            ]
            words.append(canonical_key(topic.get("name")))
            result.append((topic, [word for word in words if word]))
        return result

    async def _match_person(
        self,
        message: dict[str, Any],
        text: str,
        aliases: list[tuple[str, dict[str, Any]]],
    ) -> PersonMatch:
        haystacks = [
            canonical_key(text),
            canonical_key(message.get("forwarded_origin_title")),
            canonical_key(message.get("forwarded_origin_sender_name")),
            canonical_key(message.get("source_chat_title")),
        ]
        if message.get("source_kind") == "monitored_channel":
            haystacks.append(canonical_key(message.get("sender_name")))
        for alias, person in aliases:
            if any(
                alias
                and (
                    alias == haystack
                    or re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", haystack)
                )
                for haystack in haystacks
                if haystack
            ):
                return PersonMatch(
                    person_id=int(person["person_id"]),
                    candidate_id=None,
                    name=str(person["full_name"]),
                    registry_bucket=str(person.get("registry_status") or "inside"),
                    method="registry_alias",
                    confidence=0.95,
                    position=person.get("position"),
                    category=person.get("category"),
                )
        candidate_name = normalize_persian(
            message.get("forwarded_origin_title")
            or (
                message.get("source_chat_title")
                if message.get("source_kind") == "monitored_channel"
                else ""
            )
            or "شخص نامشخص"
        )
        if canonical_key(candidate_name) in {
            "گروه پشتیبان گرایه",
            "گرایه",
            "شخص نامشخص",
        }:
            candidate_name = "شخص نامشخص"
        candidate_id = None
        if candidate_name != "شخص نامشخص":
            candidate_id = await self.db.create_person_candidate(
                candidate_name,
                confidence=0.45,
                message_id=int(message["id"]),
                sample_text=message.get("text"),
                sample_caption=message.get("caption"),
            )
        return PersonMatch(
            person_id=None,
            candidate_id=candidate_id,
            name=candidate_name,
            registry_bucket="outside",
            method="source_heuristic",
            confidence=0.45 if candidate_id else 0.2,
            category="سایر افراد",
        )

    def _match_topic(
        self,
        text: str,
        topic_index: list[tuple[dict[str, Any], list[str]]],
    ) -> TopicMatch:
        canonical = canonical_key(text)
        best: tuple[float, dict[str, Any]] | None = None
        for topic, words in topic_index:
            matches = sum(
                1
                for word in words
                if word
                and re.search(
                    rf"(?:^|\s){re.escape(word)}(?:$|\s)", canonical
                )
            )
            score = min(1.0, matches / max(1, min(3, len(words))))
            if best is None or score > best[0]:
                best = (score, topic)
        if best is None:
            raise RuntimeError("هیچ موضوع فعالی در سامانه تعریف نشده است.")
        score, topic = best
        return TopicMatch(
            topic_id=int(topic["topic_id"]),
            name=str(topic["name"]),
            score=max(0.25, score),
        )

    def _source_quality(self, message: dict[str, Any]) -> float:
        value = 0.55
        if message.get("source_kind") == "monitored_channel":
            value += 0.15
        if message.get("primary_source_url") or message.get(
            "forwarded_origin_url"
        ):
            value += 0.15
        if message.get("is_forwarded") and not message.get(
            "forwarded_origin_title"
        ):
            value -= 0.1
        return max(0.1, min(1.0, value))

    def _importance(self, text: str, source_quality: float) -> float:
        length = min(len(text) / 900, 1.0)
        number_bonus = 0.1 if re.search(r"[۰-۹0-9]", text) else 0.0
        return round(
            max(0.1, min(1.0, 0.35 + 0.35 * length + 0.2 * source_quality + number_bonus)),
            4,
        )

    def _deduplicate(
        self, messages: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        representative = messages[0]
        accepted: list[dict[str, Any]] = []
        for message in messages:
            relation = "evidence"
            if message is representative:
                relation = "representative"
            else:
                text = str(message.get("normalized_text") or "")
                for earlier in messages[: messages.index(message)]:
                    if (
                        message.get("text_sha256")
                        and message.get("text_sha256") == earlier.get("text_sha256")
                    ) or _jaccard(
                        text, str(earlier.get("normalized_text") or "")
                    ) >= self.settings.bulletin_near_duplicate_threshold:
                        relation = "duplicate"
                        break
            accepted.append(
                {
                    "message_id": int(message["id"]),
                    "relation_type": relation,
                    "score": float(message.get("importance_score") or 0),
                }
            )
        return representative, accepted

    async def _save_message_analysis(
        self,
        message: dict[str, Any],
        *,
        person: PersonMatch | None,
        topic: TopicMatch | None,
        relevance_status: str,
        relevance_reason: str,
        ai_status: str,
        ai_payload: dict[str, Any] | None = None,
        source_quality: float | None = None,
        importance: float | None = None,
        location: str | None = None,
    ) -> None:
        await self.db._execute(
            """
            UPDATE messages SET detected_person_name=?,detected_person_id=?,person_candidate_id=?,
            person_match_method=?,person_confidence=?,detected_topic_id=?,detected_topic_name=?,
            topic_confidence=?,statement_type=?,statement_location_type=?,statement_location_label=?,
            relevance_status=?,relevance_reason=?,source_quality=?,importance_score=?,
            ai_enrichment_status=?,ai_enrichment_confidence=?,ai_analysis_json=?,updated_at=?
            WHERE id=?
            """,
            (
                person.name if person else None,
                person.person_id if person else None,
                person.candidate_id if person else None,
                person.method if person else None,
                person.confidence if person else None,
                topic.topic_id if topic else None,
                topic.name if topic else None,
                topic.score if topic else None,
                (ai_payload or {}).get("statement_type") or "اظهارنظر",
                "reported" if location else None,
                location,
                relevance_status,
                relevance_reason,
                source_quality,
                importance,
                ai_status,
                float((ai_payload or {}).get("confidence") or 0)
                if ai_payload
                else None,
                dumps(ai_payload or {}),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                int(message["id"]),
            ),
        )

    async def _apply_ai_person(
        self,
        message: dict[str, Any],
        payload: dict[str, Any],
        fallback: PersonMatch,
        alias_index: list[tuple[str, dict[str, Any]]],
    ) -> PersonMatch:
        name = normalize_persian(payload.get("person_name"))
        if not name:
            return fallback
        key = canonical_key(name)
        for alias, person in alias_index:
            if alias == key:
                return PersonMatch(
                    person_id=int(person["person_id"]),
                    candidate_id=None,
                    name=str(person["full_name"]),
                    registry_bucket=str(person.get("registry_status") or "inside"),
                    method="ai_registry_alias",
                    confidence=float(payload.get("confidence") or 0.8),
                    position=person.get("position")
                    or payload.get("person_position"),
                    category=person.get("category"),
                )
        candidate_id = await self.db.create_person_candidate(
            name,
            confidence=float(payload.get("confidence") or 0.65),
            message_id=int(message["id"]),
            sample_text=message.get("text"),
            sample_caption=message.get("caption"),
        )
        return PersonMatch(
            person_id=None,
            candidate_id=candidate_id,
            name=name,
            registry_bucket="outside",
            method="ai_candidate",
            confidence=float(payload.get("confidence") or 0.65),
            position=payload.get("person_position"),
            category="سایر افراد",
        )

    def _apply_ai_topic(
        self,
        payload: dict[str, Any],
        fallback: TopicMatch,
        topic_index: list[tuple[dict[str, Any], list[str]]],
    ) -> TopicMatch:
        key = canonical_key(payload.get("topic"))
        if not key:
            return fallback
        for topic, _ in topic_index:
            if canonical_key(topic.get("name")) == key:
                return TopicMatch(
                    topic_id=int(topic["topic_id"]),
                    name=str(topic["name"]),
                    score=float(payload.get("confidence") or 0.8),
                )
        return fallback

    async def _analyze_with_ai(
        self,
        run_id: int,
        message: dict[str, Any],
        text: str,
        people: list[dict[str, Any]],
        topics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        schema_hint = {
            "person_name": "نام گوینده",
            "person_position": "سمت یا null",
            "topic": "یکی از موضوعات مجاز",
            "subtopics": ["موضوع فرعی"],
            "tags": ["برچسب"],
            "entities": ["شخص/سازمان/مکان"],
            "statement_type": "اظهارنظر/خبر/گزارش",
            "confidence": 0.0,
        }
        prompt = (
            "پیام زیر را فقط بر اساس متن و متادیتا تحلیل کن. ادعای تازه نساز. "
            "خروجی فقط JSON باشد.\n"
            f"اشخاص شناسنامه: {[p['full_name'] for p in people[:300]]}\n"
            f"موضوعات مجاز: {[t['name'] for t in topics]}\n"
            f"متادیتا: {json.dumps({'source': message.get('source_chat_title'), 'forwarded_origin': message.get('forwarded_origin_title'), 'oration': message.get('oration_text')}, ensure_ascii=False)}\n"
            f"متن: {text[: self.settings.ai_enrichment_max_chars_per_message]}\n"
            f"ساختار: {json.dumps(schema_hint, ensure_ascii=False)}"
        )
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached = await self.db.get_cached_ai_response(
            input_hash=input_hash,
            provider=self.settings.ai_provider,
            model=self.settings.ai_model,
            prompt_version=PROMPT_VERSION,
            request_kind="message_analysis",
        )
        if cached and isinstance(cached.get("parsed_response"), dict):
            return dict(cached["parsed_response"])
        return await self._call_json(
            run_id=run_id,
            item_id=None,
            input_hash=input_hash,
            system_prompt="شما تحلیل‌گر دقیق خبر فارسی هستید و فقط JSON معتبر برمی‌گردانید.",
            user_prompt=prompt,
            request_kind="message_analysis",
        )

    async def _summarize_cluster(
        self,
        *,
        run_id: int,
        template: dict[str, Any],
        person: PersonMatch,
        topic: TopicMatch,
        topic_keywords: list[str],
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        usable = [
            message
            for message in messages
            if str(message.get("normalized_text") or message.get("text") or message.get("caption") or "").strip()
            and message.get("relation_type") != "duplicate"
        ]
        if not usable:
            usable = messages
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for message in usable:
            text = normalize_persian(
                message.get("normalized_text")
                or message.get("text")
                or message.get("caption")
                or ""
            )
            for index, sentence in enumerate(split_sentences(text)):
                key = canonical_key(sentence)
                if not key or key in seen:
                    continue
                seen.add(key)
                score = min(
                    1.0,
                    0.45
                    + 0.3 * float(message.get("importance_score") or 0.5)
                    + (0.1 if re.search(r"[۰-۹0-9]", sentence) else 0),
                )
                selected.append(
                    {
                        "message_id": int(message["id"]),
                        "sentence_index": index,
                        "source_sentence": sentence,
                        "score": round(score, 4),
                        "reasons": ["اهمیت", "پوشش موضوع"],
                    }
                )
        selected.sort(key=lambda row: float(row["score"]), reverse=True)
        selected = selected[: max(1, self.settings.bulletin_summary_max_sentences)]
        evidence = " ".join(str(row["source_sentence"]) for row in selected)
        extractive = _trim_words(
            evidence or normalize_persian(usable[0].get("normalized_text") or ""),
            max(30, self.settings.bulletin_max_summary_chars // 5),
        )
        base = {
            "summary": extractive,
            "summary_detailed": _trim_words(
                " ".join(
                    normalize_persian(
                        message.get("normalized_text")
                        or message.get("text")
                        or message.get("caption")
                        or ""
                    )
                    for message in usable
                ),
                190,
            ),
            "summary_method": "extractive_offline",
            "summary_version": "extractive-v11.0",
            "selected_sentences": selected,
            "confidence": round(
                min(
                    0.92,
                    0.5
                    + 0.12 * len(selected)
                    + 0.15 * person.confidence
                    + 0.1 * topic.score,
                ),
                4,
            ),
            "confidence_breakdown": {
                "person": person.confidence,
                "topic": topic.score,
                "evidence_sentences": len(selected),
            },
            "consensus_method": "deterministic_evidence_selection",
            "statement_type": "اظهارنظر",
        }
        if not self._ai_ready:
            return base, True, {"mode": "offline"}
        prompt = (
            "فقط با اتکا به شواهد زیر، از زبان گوینده یک خلاصه دقیق بساز. "
            "عدد، نام یا ادعای بدون شاهد اضافه نکن. خروجی فقط JSON با کلیدهای "
            "summary، summary_detailed، statement_type، confidence باشد.\n"
            f"گوینده: {person.name}\nموضوع: {topic.name}\n"
            f"شواهد: {json.dumps(selected, ensure_ascii=False)}"
        )
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        try:
            parsed = await self._call_json(
                run_id=run_id,
                item_id=None,
                input_hash=input_hash,
                system_prompt=str(
                    template.get("system_prompt")
                    or "خلاصه‌ساز دقیق خبر فارسی هستید."
                ),
                user_prompt=prompt,
                request_kind="cluster_summary",
            )
            summary = normalize_persian(parsed.get("summary"))
            detailed = normalize_persian(
                parsed.get("summary_detailed") or summary
            )
            if not summary:
                return base, True, {"mode": "ai_empty"}
            base.update(
                {
                    "summary": _trim_words(summary, 80),
                    "summary_detailed": _trim_words(detailed, 190),
                    "summary_method": "ai_grounded",
                    "summary_version": PROMPT_VERSION,
                    "confidence": max(
                        0.0, min(1.0, float(parsed.get("confidence") or 0.75))
                    ),
                    "consensus_method": "ai_with_selected_evidence",
                    "statement_type": parsed.get("statement_type")
                    or "اظهارنظر",
                }
            )
            return base, False, {"mode": "ai"}
        except Exception as exc:
            return base, True, {"mode": "fallback", "error": str(exc)}

    async def _call_json(
        self,
        *,
        run_id: int | None,
        item_id: int | None,
        input_hash: str,
        system_prompt: str,
        user_prompt: str,
        request_kind: str,
        prompt_version: str = PROMPT_VERSION,
        key_pool: AIKeyPool | None = None,
        key_slot_offset: int = 0,
    ) -> dict[str, Any]:
        cached = await self.db.get_cached_ai_response(
            input_hash=input_hash,
            provider=self.settings.ai_provider,
            model=self.settings.ai_model,
            prompt_version=prompt_version,
            request_kind=request_kind,
        )
        if cached and isinstance(cached.get("parsed_response"), dict):
            return dict(cached["parsed_response"])
        endpoint = (
            f"{self.settings.ai_base_url}/responses"
            if self.settings.ai_provider == "openai"
            else f"{self.settings.ai_base_url}/chat/completions"
        )
        active_key_pool = key_pool or self.ai_key_pool
        last_error = ""
        for attempt in range(1, self.settings.bulletin_ai_max_retries + 1):
            lease = None
            started = time.perf_counter()
            try:
                lease = await active_key_pool.acquire()
                if self.settings.ai_provider == "openai":
                    payload = {
                        "model": self.settings.ai_model,
                        "input": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "text": {"format": {"type": "json_object"}},
                        "temperature": self.settings.ai_temperature,
                    }
                else:
                    payload = {
                        "model": self.settings.ai_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": self.settings.ai_temperature,
                        "response_format": {"type": "json_object"},
                    }
                response = await self.client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {lease.key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.settings.ai_timeout_seconds,
                )
                raw: Any
                try:
                    raw = response.json()
                except ValueError:
                    raw = {"text": response.text}
                latency = int((time.perf_counter() - started) * 1000)
                if response.status_code in {429, 500, 502, 503, 504}:
                    await active_key_pool.release_failure(
                        lease,
                        kind="transient",
                        reason=f"HTTP {response.status_code}",
                    )
                    last_error = f"HTTP {response.status_code}"
                    await self.db.add_ai_request(
                        run_id=run_id,
                        item_id=item_id,
                        input_hash=input_hash,
                        provider=self.settings.ai_provider,
                        model=self.settings.ai_model,
                        prompt_version=prompt_version,
                        request_kind=request_kind,
                        raw_request={"kind": request_kind},
                        raw_response=raw,
                        parsed_response=None,
                        token_usage=None,
                        latency_ms=latency,
                        status="retry",
                        error_text=last_error,
                        key_slot=lease.slot + key_slot_offset,
                        attempt_number=attempt,
                        endpoint=endpoint,
                        http_status=response.status_code,
                    )
                    continue
                response.raise_for_status()
                text = _clean_json_output(_extract_output_text(raw))
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("خروجی مدل باید شیء JSON باشد.")
                await active_key_pool.release_success(lease)
                await self.db.add_ai_request(
                    run_id=run_id,
                    item_id=item_id,
                    input_hash=input_hash,
                    provider=self.settings.ai_provider,
                    model=self.settings.ai_model,
                    prompt_version=prompt_version,
                    request_kind=request_kind,
                    raw_request={"kind": request_kind},
                    raw_response=raw,
                    parsed_response=parsed,
                    token_usage=raw.get("usage")
                    if isinstance(raw, dict)
                    else None,
                    latency_ms=latency,
                    status="validated",
                    error_text=None,
                    key_slot=lease.slot + key_slot_offset,
                    attempt_number=attempt,
                    endpoint=endpoint,
                    http_status=response.status_code,
                )
                return parsed
            except NoAvailableAPIKey:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if lease is not None:
                    await active_key_pool.release_failure(
                        lease, kind="transient", reason=last_error
                    )
        raise RuntimeError(
            f"هوش پس از {self.settings.bulletin_ai_max_retries} تلاش پاسخ معتبر نداد: {last_error}"
        )
