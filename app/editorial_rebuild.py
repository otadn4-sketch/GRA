from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from .ai_key_pool import AIKeyPool, NoAvailableAPIKey
from .bulletin_builder import BulletinDataBuilder
from .bulletin_models import (
    BulletinControversy,
    BulletinData,
    BulletinPerson,
    BulletinStatement,
    ControversySide,
)
from .bulletin_pipeline import _clean_json_output, _extract_output_text
from .bulletin_validation import is_generic_url, valid_public_url
from .config import Settings
from .db import Database, dumps
from .persian_text import normalize_persian

EDITORIAL_PACKAGE_VERSION = "human_editorial_v10.4"
EDITORIAL_PROMPT_VERSION = 104

_FORBIDDEN_PUBLIC_PHRASES = (
    "محل بیان نامشخص",
    "در پیام ارائه شده هیچ",
    "در پیام ارائه‌شده هیچ",
    "در منبع هیچ اشاره",
    "هیچ اظهار نظر مستقیمی",
    "هیچ اظهارنظر مستقیمی",
    "متن صرفاً به",
    "متن صرفا به",
    "در پیام منبع هیچ",
)
_GENERIC_CONTROVERSY_PREFIXES = (
    "اختلاف نظر درباره",
    "اختلاف‌نظر درباره",
    "موضوع سیاست",
    "موضوع اقتصاد",
    "موضوع انرژی",
)
_TRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class EditorialEntry(BaseModel):
    statement_ids: list[str] = Field(min_length=1)
    topic: str = Field(min_length=1, max_length=160)
    context_label: str = Field(min_length=1, max_length=180)
    headline: str = Field(min_length=3, max_length=150)
    lead: str = Field(min_length=10, max_length=650)
    body: str = Field(min_length=20, max_length=2400)


class EditorialPersonOutput(BaseModel):
    person_id: int | None = None
    canonical_name: str = Field(min_length=2, max_length=180)
    position: str | None = Field(default=None, max_length=220)
    category: str = Field(min_length=1, max_length=180)
    entries: list[EditorialEntry] = Field(min_length=1)


class EditorialControversyOutput(BaseModel):
    title: str = Field(min_length=5, max_length=180)
    summary: str = Field(min_length=20, max_length=1200)
    statement_ids: list[str] = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list, max_length=6)
    importance_score: float = Field(default=0.75, ge=0, le=1)
    confidence: float = Field(default=0.75, ge=0, le=1)


class EditorialControversiesOutput(BaseModel):
    controversies: list[EditorialControversyOutput] = Field(default_factory=list, max_length=5)


class EditorialPackage(BaseModel):
    version: str = EDITORIAL_PACKAGE_VERSION
    run_id: int
    generated_at: str
    source_digest: str
    provider: str
    model: str
    people: list[EditorialPersonOutput]
    controversies: list[EditorialControversyOutput] = Field(default_factory=list)


def _api_endpoint(base_url: str, endpoint_kind: str) -> str:
    base = str(base_url or "").rstrip("/")
    suffix = "/responses" if endpoint_kind == "responses" else "/chat/completions"
    return base if base.endswith(suffix) else base + suffix


def _numbers(text: str) -> set[str]:
    clean = normalize_persian(text).translate(_DIGITS)
    return set(re.findall(r"(?<![\w])\d+(?:[.,/]\d+)*(?![\w])", clean))


def _clean_public_text(text: str) -> str:
    clean = normalize_persian(str(text or ""), remove_noise=True).strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean


def editorial_source_digest(data: BulletinData) -> str:
    payload: list[dict[str, Any]] = []
    for category in data.categories:
        for person in category.people:
            payload.append({
                "person_id": person.person_id,
                "name": person.name_canonical,
                "position": person.position,
                "category": category.title,
                "statements": [
                    {
                        "id": statement.statement_id,
                        "topic": statement.topic,
                        "context": statement.statement_mode,
                        "summary": statement.summary_short,
                        "detailed": statement.summary_detailed,
                        "include": statement.include_in_main,
                        "sources": statement.source_ids,
                    }
                    for statement in person.statements
                    if statement.include_in_main
                ],
            })
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def editorial_package_path(settings: Settings, run_id: int) -> Path:
    return settings.bulletin_output_dir / f"run_{run_id}" / "editorial_package.json"


def load_editorial_package(settings: Settings, run_id: int) -> EditorialPackage | None:
    path = editorial_package_path(settings, run_id)
    if not path.exists():
        return None
    try:
        return EditorialPackage.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError, json.JSONDecodeError):
        return None


def _merge_statement_sources(target: BulletinStatement, statements: list[BulletinStatement]) -> None:
    source_records = []
    source_ids: list[int] = []
    source_names: list[str] = []
    source_links: list[str] = []
    best_qr = target.qr_code_path if target.show_qr_in_bulletin else None
    best_editorial_url = target.editorial_source_url if target.show_qr_in_bulletin else None
    best_url = target.source_url
    for statement in statements:
        for source in statement.source_records:
            if source.message_id not in source_ids:
                source_records.append(source)
                source_ids.append(source.message_id)
        for name in statement.source_names:
            if name not in source_names:
                source_names.append(name)
        for link in statement.source_links:
            if link not in source_links:
                source_links.append(link)
        if (
            not best_qr
            and statement.show_qr_in_bulletin
            and statement.qr_code_path
            and valid_public_url(statement.editorial_source_url)
            and not is_generic_url(statement.editorial_source_url)
        ):
            best_qr = statement.qr_code_path
            best_editorial_url = statement.editorial_source_url
        if not best_url and statement.source_url:
            best_url = statement.source_url
    target.source_records = source_records
    target.source_ids = source_ids
    target.source_names = source_names
    target.source_links = source_links
    target.source_url = best_url
    target.has_source_url = bool(best_url)
    target.editorial_source_url = best_editorial_url
    target.qr_code_path = best_qr
    target.show_qr_in_bulletin = bool(
        best_qr
        and valid_public_url(best_editorial_url)
        and not is_generic_url(best_editorial_url)
    )


def apply_editorial_package(data: BulletinData, package: EditorialPackage) -> BulletinData:
    """Overlay a validated editorial package on deterministic bulletin data.

    The approved source items remain untouched in the database. When the model merges
    multiple approved statements into one public entry, the first statement carries
    the merged editorial text and the remaining statements stay available for audit/
    appendix while being hidden from the public concise and magazine outputs.
    """
    if package.source_digest != editorial_source_digest(data):
        raise RuntimeError(
            "بسته تدوین سردبیری با تصمیم‌های فعلی همخوان نیست. فقط بازتدوین سردبیری را دوباره اجرا کنید؛ "
            "نیازی به تحلیل کامل پیام‌ها نیست."
        )

    people_by_id: dict[int, BulletinPerson] = {}
    people_by_name: dict[str, BulletinPerson] = {}
    for category in data.categories:
        for person in category.people:
            if person.person_id is not None:
                people_by_id[int(person.person_id)] = person
            people_by_name[normalize_persian(person.name_canonical).strip().lower()] = person

    for person_output in package.people:
        person = people_by_id.get(int(person_output.person_id)) if person_output.person_id is not None else None
        if person is None:
            person = people_by_name.get(normalize_persian(person_output.canonical_name).strip().lower())
        if person is None:
            raise RuntimeError(f"شخص بسته سردبیری در داده فعلی پیدا نشد: {person_output.canonical_name}")

        statement_map = {statement.statement_id: statement for statement in person.statements}
        public_order: list[BulletinStatement] = []
        covered: set[str] = set()
        for entry_index, entry in enumerate(person_output.entries):
            linked = [statement_map[sid] for sid in entry.statement_ids if sid in statement_map]
            if not linked:
                raise RuntimeError(f"ورودی سردبیری بدون گزاره معتبر برای {person.name_canonical}")
            target = linked[0]
            target.topic = _clean_public_text(entry.topic)
            target.statement_mode = _clean_public_text(entry.context_label)
            target.editorial_context_label = target.statement_mode
            target.headline = _clean_public_text(entry.headline)
            target.summary_lead = _clean_public_text(entry.lead)
            target.summary_body = _clean_public_text(entry.body)
            target.summary_short = target.summary_lead
            target.summary_detailed = target.summary_body
            target.editorial_order = entry_index
            target.merged_statement_ids = list(entry.statement_ids)
            target.public_exclusion_reason = None
            target.include_in_main = True
            _merge_statement_sources(target, linked)
            public_order.append(target)
            covered.update(entry.statement_ids)
            for merged in linked[1:]:
                merged.include_in_main = False
                merged.public_exclusion_reason = f"merged_into:{target.statement_id}"
                merged.merged_statement_ids = list(entry.statement_ids)

        for statement in person.statements:
            if statement.include_in_main and statement.statement_id not in covered:
                raise RuntimeError(f"گزاره {statement.statement_id} در بسته سردبیری تعیین تکلیف نشده است.")
        hidden = [statement for statement in person.statements if statement not in public_order]
        person.statements = public_order + hidden
        person.name_canonical = person_output.canonical_name
        person.position = person_output.position or person.position

    valid_statement_ids = {
        statement.statement_id
        for category in data.categories
        for person in category.people
        for statement in person.statements
    }
    controversies: list[BulletinControversy] = []
    for item in package.controversies:
        ids = [sid for sid in item.statement_ids if sid in valid_statement_ids]
        if not ids:
            continue
        sides = [
            ControversySide(
                label="محورهای اصلی واکنش",
                position_summary=" ".join(item.key_points),
                person_ids=[],
            )
        ] if item.key_points else []
        controversies.append(BulletinControversy(
            title=item.title,
            summary=item.summary,
            sides=sides,
            person_ids=[],
            statement_ids=ids,
            importance_score=item.importance_score,
            confidence=item.confidence,
        ))
    data.controversies = controversies
    data.meta.editorial_version = package.version
    data.meta.editorial_package_status = "validated"
    return data


class EditorialRebuilder:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        client: httpx.AsyncClient,
        key_pool: AIKeyPool,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self.key_pool = key_pool
        self.builder = BulletinDataBuilder(db, settings)

    async def rebuild(self, run_id: int) -> dict[str, Any]:
        if self.settings.ai_provider == "disabled" or not self.settings.ai_model or not self.key_pool.configured:
            raise RuntimeError("برای بازتدوین سردبیری باید AI_PROVIDER، مدل و حداقل یک کلید API فعال باشند.")

        output_dir = self.settings.bulletin_output_dir / f"run_{run_id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        data, _snapshot = await self.builder.build(run_id, output_dir, apply_editorial=False)
        source_digest = editorial_source_digest(data)
        people = [
            (category.title, person)
            for category in data.categories
            for person in category.people
            if any(statement.include_in_main for statement in person.statements)
        ]
        if not people:
            raise RuntimeError("هیچ اظهار تأییدشده‌ای برای بازتدوین وجود ندارد.")

        stage_id = await self.db.start_bulletin_stage(
            run_id,
            "editorial_rebuild",
            message="بازتدوین سردبیری آیتم‌های تأییدشده بدون تحلیل مجدد پیام‌ها آغاز شد",
            details={"people": len(people), "version": EDITORIAL_PACKAGE_VERSION},
        )
        try:
            outputs = await asyncio.gather(
                *(self._rewrite_person(run_id, category, person) for category, person in people)
            )
            controversies = await self._rewrite_controversies(run_id, outputs)
            package = EditorialPackage(
                run_id=run_id,
                generated_at=datetime.now(timezone.utc).isoformat(),
                source_digest=source_digest,
                provider=self.settings.ai_provider,
                model=self.settings.ai_model,
                people=outputs,
                controversies=controversies,
            )
            # Final cross-object validation before atomic replacement.
            apply_editorial_package(data.model_copy(deep=True), package)
            path = editorial_package_path(self.settings, run_id)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(package.model_dump_json(indent=2), encoding="utf-8")
            temp.replace(path)
            await self.db.finish_bulletin_stage(
                stage_id,
                message="بازتدوین سردبیری کامل و اعتبارسنجی شد",
                details={
                    "people": len(outputs),
                    "controversies": len(controversies),
                    "path": str(path),
                    "source_digest": source_digest,
                },
            )
            return {
                "ok": True,
                "run_id": run_id,
                "people": len(outputs),
                "controversies": len(controversies),
                "path": str(path),
                "source_digest": source_digest,
                "ai_reprocessed_messages": 0,
            }
        except Exception as exc:
            await self.db.finish_bulletin_stage(
                stage_id,
                status="failed",
                level="ERROR",
                message=str(exc),
                details={"version": EDITORIAL_PACKAGE_VERSION},
            )
            raise

    async def _rewrite_person(self, run_id: int, category: str, person: BulletinPerson) -> EditorialPersonOutput:
        statements = [statement for statement in person.statements if statement.include_in_main]
        source_payload = {
            "person_id": person.person_id,
            "canonical_name": person.name_canonical,
            "position": person.position,
            "category": category,
            "statements": [
                {
                    "statement_id": statement.statement_id,
                    "topic": statement.topic,
                    "current_context": statement.statement_mode,
                    "current_summary": statement.summary_short,
                    "detailed_summary": statement.summary_detailed,
                    "source_texts": [
                        {
                            "message_id": source.message_id,
                            "source_name": source.source_name,
                            "published_at": source.published_at,
                            "text": (source.text or "")[:1800],
                        }
                        for source in statement.source_records[:5]
                    ],
                }
                for statement in statements
            ],
        }
        source_text = json.dumps(source_payload, ensure_ascii=False, indent=2)
        evidence_text = " ".join(
            [statement.summary_short + " " + statement.summary_detailed for statement in statements]
            + [source.text or "" for statement in statements for source in statement.source_records]
        )
        system_prompt = (
            "تو دبیر ارشد یک خبرنامه رسمی فارسی هستی. فقط اظهارات تأییدشده را بازتدوین کن و هیچ پیام تازه‌ای را تحلیل نکن. "
            "نام معیار، سمت، جهت موضع، اعداد و نسبت هر ادعا به گوینده باید دقیقاً حفظ شود. گزاره‌های هم‌معنای یک شخص را ادغام کن، "
            "اما موضوعات مستقل را در ورودی‌های جدا نگه دار. از نثر ماشینی مانند «فلانی درباره موضوع...» استفاده نکن. "
            "عبارت «محل بیان نامشخص» و هر توضیح فنی ممیزی ممنوع است؛ اگر محل دقیق احراز نشد بنویس «در اظهارنظری». "
            "برای هر ورودی عنوان خبری کوتاه، خلاصه برجسته و متن روان سردبیری بساز. فقط JSON معتبر و بدون Markdown برگردان."
        )
        user_prompt = (
            "داده تأییدشده زیر را به بسته سردبیری همان شخص تبدیل کن. هر statement_id باید دقیقاً یک بار در entries ظاهر شود؛ "
            "می‌توان چند statement_id هم‌معنا را در یک entry ادغام کرد. canonical_name را دقیقاً برابر ورودی نگه دار.\n\n"
            + source_text
        )
        output = await self._call_json(
            run_id=run_id,
            item_id=None,
            request_kind="editorial_person_v10_4",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=EditorialPersonOutput,
            cache_key={"person": source_payload, "version": EDITORIAL_PACKAGE_VERSION},
            validator=lambda value: self._validate_person_output(value, person, statements, evidence_text),
        )
        return EditorialPersonOutput.model_validate(output)

    def _validate_person_output(
        self,
        output: EditorialPersonOutput,
        person: BulletinPerson,
        statements: list[BulletinStatement],
        source_text: str,
    ) -> list[str]:
        errors: list[str] = []
        if normalize_persian(output.canonical_name).strip() != normalize_persian(person.name_canonical).strip():
            errors.append("canonical_name با نام معیار ورودی یکسان نیست.")
        expected = [statement.statement_id for statement in statements]
        observed = [sid for entry in output.entries for sid in entry.statement_ids]
        if sorted(observed) != sorted(expected):
            errors.append(f"statement_idها باید دقیقاً یک بار پوشش داده شوند. expected={expected}, observed={observed}")
        for entry in output.entries:
            joined = " ".join((entry.context_label, entry.headline, entry.lead, entry.body))
            if any(phrase in joined for phrase in _FORBIDDEN_PUBLIC_PHRASES):
                errors.append(f"عبارت فنی/نامناسب در خروجی {entry.statement_ids} وجود دارد.")
            if len(entry.headline.split()) > self.settings.editorial_headline_max_words:
                errors.append(f"عنوان {entry.statement_ids} بیش از حد طولانی است.")
            if len(entry.body.split()) > self.settings.editorial_body_max_words:
                errors.append(f"متن {entry.statement_ids} بیش از حد طولانی است.")
            unknown_numbers = _numbers(joined) - _numbers(source_text)
            if unknown_numbers:
                errors.append(f"عدد بدون شاهد در {entry.statement_ids}: {sorted(unknown_numbers)}")
        return errors

    async def _rewrite_controversies(
        self,
        run_id: int,
        people: list[EditorialPersonOutput],
    ) -> list[EditorialControversyOutput]:
        entries = [
            {
                "person": person.canonical_name,
                "statement_ids": entry.statement_ids,
                "topic": entry.topic,
                "headline": entry.headline,
                "lead": entry.lead,
            }
            for person in people
            for entry in person.entries
        ]
        system_prompt = (
            "تو دبیر بخش اخبار جلب‌توجه‌کننده یک خبرنامه روزانه هستی. رویدادهای عینی و مشخص روز را پیدا کن، نه عنوان‌های کلی موضوعی. "
            "عنوان‌هایی مانند «اختلاف‌نظر درباره سیاست خارجی» ممنوع است. یک رویداد باید حول مصاحبه، بیانیه، تصمیم، آمار، خبر یا واکنش مشخص شکل گرفته باشد. "
            "حداکثر پنج رویداد مهم انتخاب کن. اگر رویداد واقعی وجود ندارد آرایه خالی برگردان. هر ادعا فقط بر statement_idهای ورودی متکی باشد. "
            "فقط JSON معتبر و بدون Markdown برگردان."
        )
        user_prompt = "ورودی‌های سردبیری:\n" + json.dumps(entries, ensure_ascii=False, indent=2)
        valid_ids = {sid for entry in entries for sid in entry["statement_ids"]}
        output = await self._call_json(
            run_id=run_id,
            item_id=None,
            request_kind="editorial_controversies_v10_4",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=EditorialControversiesOutput,
            cache_key={"entries": entries, "version": EDITORIAL_PACKAGE_VERSION},
            validator=lambda value: self._validate_controversies(value, valid_ids),
        )
        return EditorialControversiesOutput.model_validate(output).controversies

    def _validate_controversies(
        self,
        output: EditorialControversiesOutput,
        valid_ids: set[str],
    ) -> list[str]:
        errors: list[str] = []
        max_count = self.settings.editorial_max_controversies
        if len(output.controversies) > max_count:
            errors.append(f"تعداد جنجال‌ها بیشتر از {max_count} است.")
        seen_titles: set[str] = set()
        for item in output.controversies:
            normalized_title = normalize_persian(item.title).strip().lower()
            if any(normalized_title.startswith(prefix) for prefix in _GENERIC_CONTROVERSY_PREFIXES):
                errors.append(f"عنوان جنجال کلی و غیررویدادمحور است: {item.title}")
            if normalized_title in seen_titles:
                errors.append(f"عنوان جنجال تکراری است: {item.title}")
            seen_titles.add(normalized_title)
            invalid = set(item.statement_ids) - valid_ids
            if invalid:
                errors.append(f"شناسه نامعتبر در جنجال {item.title}: {sorted(invalid)}")
        return errors

    async def _call_json(
        self,
        *,
        run_id: int,
        item_id: int | None,
        request_kind: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        cache_key: dict[str, Any],
        validator,
    ) -> dict[str, Any]:
        endpoint_kind = "responses" if self.settings.ai_provider == "openai" else "chat/completions"
        endpoint = _api_endpoint(self.settings.ai_base_url, endpoint_kind)
        schema_json = schema.model_json_schema()
        input_hash = hashlib.sha256(
            dumps({
                "provider": self.settings.ai_provider,
                "model": self.settings.ai_model,
                "prompt_version": EDITORIAL_PROMPT_VERSION,
                "request_kind": request_kind,
                "cache_key": cache_key,
            }).encode("utf-8")
        ).hexdigest()
        cached = await self.db.get_cached_ai_response(
            input_hash=input_hash,
            provider=self.settings.ai_provider,
            model=self.settings.ai_model,
            prompt_version=EDITORIAL_PROMPT_VERSION,
            request_kind=request_kind,
        )
        if cached and cached.get("parsed_response"):
            try:
                parsed = schema.model_validate(cached["parsed_response"])
                errors = validator(parsed)
                if not errors:
                    return parsed.model_dump()
            except ValidationError:
                pass

        previous_output = ""
        feedback: list[str] = []
        attempts = max(2, int(self.settings.editorial_ai_max_retries))
        last_error = "مدل پاسخ معتبر تولید نکرد."
        for attempt in range(1, attempts + 1):
            repair = ""
            if attempt > 1:
                repair = (
                    "\n\nپاسخ قبلی را با توجه به خطاهای زیر اصلاح کن و ساختار را از نو اختراع نکن:\n- "
                    + "\n- ".join(feedback[-12:])
                    + "\nپاسخ قبلی:\n" + previous_output[:9000]
                )
            effective_user_prompt = user_prompt + repair
            if self.settings.ai_provider == "openai":
                payload: dict[str, Any] = {
                    "model": self.settings.ai_model,
                    "input": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": effective_user_prompt},
                    ],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": request_kind,
                            "strict": True,
                            "schema": schema_json,
                        }
                    },
                }
            else:
                payload = {
                    "model": self.settings.ai_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt + "\nJSON Schema:\n" + json.dumps(schema_json, ensure_ascii=False),
                        },
                        {"role": "user", "content": effective_user_prompt},
                    ],
                    "temperature": self.settings.ai_temperature,
                }

            started = time.perf_counter()
            lease = None
            raw_response: Any = None
            http_status: int | None = None
            try:
                lease = await self.key_pool.acquire()
                response = await self.client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {lease.key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.settings.ai_timeout_seconds,
                )
                http_status = response.status_code
                try:
                    raw_response = response.json()
                except ValueError:
                    raw_response = {"text": response.text[:12000]}
                latency_ms = int((time.perf_counter() - started) * 1000)
                if response.status_code in {401, 403}:
                    await self.key_pool.release_failure(lease, kind="auth", reason=f"HTTP {response.status_code}")
                    last_error = f"خطای احراز هویت HTTP {response.status_code} در slot {lease.slot}"
                    await self.db.add_ai_request(
                        run_id=run_id, item_id=item_id, input_hash=input_hash,
                        provider=self.settings.ai_provider, model=self.settings.ai_model,
                        prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                        raw_response=raw_response, parsed_response=None, token_usage=None,
                        latency_ms=latency_ms, status="failed", error_text=last_error,
                        key_slot=lease.slot, attempt_number=attempt, endpoint=endpoint,
                        http_status=http_status, request_kind=request_kind,
                    )
                    continue
                if response.status_code in _TRANSIENT_HTTP:
                    retry_after = response.headers.get("retry-after")
                    await self.key_pool.release_failure(
                        lease, kind="transient", reason=f"HTTP {response.status_code}",
                        retry_after_seconds=float(retry_after) if retry_after and retry_after.isdigit() else None,
                    )
                    last_error = f"خطای موقت HTTP {response.status_code}"
                    await self.db.add_ai_request(
                        run_id=run_id, item_id=item_id, input_hash=input_hash,
                        provider=self.settings.ai_provider, model=self.settings.ai_model,
                        prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                        raw_response=raw_response, parsed_response=None, token_usage=None,
                        latency_ms=latency_ms, status="retry", error_text=last_error,
                        key_slot=lease.slot, attempt_number=attempt, endpoint=endpoint,
                        http_status=http_status, request_kind=request_kind,
                    )
                    continue
                response.raise_for_status()
                output_text = _clean_json_output(_extract_output_text(raw_response if isinstance(raw_response, dict) else {}))
                previous_output = output_text
                parsed = schema.model_validate_json(output_text)
                feedback = validator(parsed)
                if feedback:
                    await self.key_pool.release_failure(lease, kind="model_output", reason=" | ".join(feedback))
                    last_error = "؛ ".join(feedback)
                    await self.db.add_ai_request(
                        run_id=run_id, item_id=item_id, input_hash=input_hash,
                        provider=self.settings.ai_provider, model=self.settings.ai_model,
                        prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                        raw_response=raw_response, parsed_response=parsed.model_dump(),
                        token_usage=(raw_response or {}).get("usage") if isinstance(raw_response, dict) else None,
                        latency_ms=latency_ms, status="retry", error_text=last_error,
                        key_slot=lease.slot, attempt_number=attempt, endpoint=endpoint,
                        http_status=http_status, request_kind=request_kind,
                    )
                    continue
                await self.key_pool.release_success(lease)
                await self.db.add_ai_request(
                    run_id=run_id, item_id=item_id, input_hash=input_hash,
                    provider=self.settings.ai_provider, model=self.settings.ai_model,
                    prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                    raw_response=raw_response, parsed_response=parsed.model_dump(),
                    token_usage=(raw_response or {}).get("usage") if isinstance(raw_response, dict) else None,
                    latency_ms=latency_ms, status="validated", error_text=None,
                    key_slot=lease.slot, attempt_number=attempt, endpoint=endpoint,
                    http_status=http_status, request_kind=request_kind,
                )
                return parsed.model_dump()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                if lease is not None:
                    await self.key_pool.release_failure(lease, kind="transient", reason=last_error)
                await self.db.add_ai_request(
                    run_id=run_id, item_id=item_id, input_hash=input_hash,
                    provider=self.settings.ai_provider, model=self.settings.ai_model,
                    prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                    raw_response=raw_response, parsed_response=None, token_usage=None,
                    latency_ms=latency_ms, status="retry", error_text=last_error,
                    key_slot=lease.slot if lease else None, attempt_number=attempt, endpoint=endpoint,
                    http_status=http_status, request_kind=request_kind,
                )
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                last_error = f"خروجی JSON نامعتبر: {exc}"
                feedback = [last_error]
                if lease is not None:
                    await self.key_pool.release_failure(lease, kind="model_output", reason=last_error)
                await self.db.add_ai_request(
                    run_id=run_id, item_id=item_id, input_hash=input_hash,
                    provider=self.settings.ai_provider, model=self.settings.ai_model,
                    prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                    raw_response=raw_response, parsed_response=None, token_usage=None,
                    latency_ms=latency_ms, status="retry", error_text=last_error,
                    key_slot=lease.slot if lease else None, attempt_number=attempt, endpoint=endpoint,
                    http_status=http_status, request_kind=request_kind,
                )
            except NoAvailableAPIKey as exc:
                raise RuntimeError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                last_error = f"HTTP {exc.response.status_code}: {exc.response.text[:1000]}"
                if lease is not None:
                    await self.key_pool.release_failure(lease, kind="transient", reason=last_error)
                await self.db.add_ai_request(
                    run_id=run_id, item_id=item_id, input_hash=input_hash,
                    provider=self.settings.ai_provider, model=self.settings.ai_model,
                    prompt_version=EDITORIAL_PROMPT_VERSION, raw_request={"request_kind": request_kind},
                    raw_response=raw_response, parsed_response=None, token_usage=None,
                    latency_ms=latency_ms, status="retry", error_text=last_error,
                    key_slot=lease.slot if lease else None, attempt_number=attempt, endpoint=endpoint,
                    http_status=http_status, request_kind=request_kind,
                )
            except Exception:
                if lease is not None:
                    await self.key_pool.release_neutral(lease)
                raise

        raise RuntimeError(
            f"بازتدوین سردبیری پس از {attempts} تلاش معتبر نشد: {last_error}. "
            "بسته قبلی حفظ شده و تحلیل کامل پیام‌ها دوباره اجرا نشده است."
        )
