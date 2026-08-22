from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
import traceback
from dataclasses import replace
from typing import Any

import httpx

from .ai_key_pool import AIKeyPool
from .bulletin_export import BulletinExporter
from .bulletin_pipeline import (
    HybridBulletinPipeline,
    PersonMatch,
    TopicMatch,
    _clean_json_output,
    _extract_output_text,
)
from .config import Settings
from .db import Database
from .editorial_rebuild import EditorialRebuilder, load_editorial_package
from .near_duplicate import choose_representatives
from .persian_text import canonical_key, normalize_persian
from .speaker_grounding import (
    evidence_supports_name,
    grounding_span,
    is_title_only_name,
    may_bind_short_name,
)

logger = logging.getLogger("prasad.bulletins")

SELECTED_ANALYSIS_PROMPT_VERSION = "garaye-registry-match-v5"
EDITORIAL_SPEAKER_PROMPT_VERSION = "garaye-speaker-summaries-v2"
EDITORIAL_EVENT_PROMPT_VERSION = "garaye-event-summaries-v1"
HIGH_ATTENTION_PROMPT_VERSION = "garaye-high-attention-v2"

SELECTED_MESSAGE_ANALYSIS_PROMPT = '''شما یک تحلیل‌گر حرفه‌ای متن‌های خبری فارسی هستید. پیام ورودی ممکن است از خبرگزاری، کانال، شبکهٔ اجتماعی یا متن رسمی آمده باشد.

پیش از استخراج اطلاعات، نوع محتوای اصلی را فقط با تکیه بر متن تعیین کن:

۱. «اظهارنظر شخص‌محور»: تمرکز اصلی پیام بر سخن، موضع، درخواست، هشدار، تکذیب، دفاع، نقد یا دیدگاهِ یک یا چند شخص مشخص است و این مطلب مستقیماً به آن شخص نسبت داده شده است.
۲. «رویداد مهم ایران و جهان»: تمرکز اصلی پیام بر رخداد، اقدام، تصمیم، سفر، حمله، حادثه، دیدار، نتیجه، آمار یا تحول مهم است و سخن یا موضعِ مستقیمِ یک شخص، موضوع اصلی پیام نیست.

وجود نام یک شخص به‌تنهایی او را گوینده نمی‌کند. نام خبرگزاری، خبرنگار، کانال، منتشرکننده، کشور، سازمان، ارتش، تیم یا نهاد را گوینده ثبت نکن؛ مگر آن‌که متن صراحتاً یک موضع یا بیانیه را به مقام یا شخصی نسبت دهد.

برای هر پیام این موارد را استخراج کن:

الف) اطلاعات مشترک
- content_type: دقیقاً یکی از «اظهارنظر شخص‌محور» یا «رویداد مهم ایران و جهان»
- main_subject: سوژهٔ اصلی خبر؛ یک عبارت کوتاه، دقیق و چندکلمه‌ای (ترجیحاً ۲ تا ۸ واژه) که موضوع محوری پیام را روشن کند. این عبارت عنوان رسانه یا نام کانال نیست و باید فقط از متن پیام استخراج شود.
- general_topic: حوزهٔ عمومی مانند «سیاست داخلی»، «سیاست خارجی»، «اقتصاد»، «فرهنگ»، «اجتماعی»، «امنیت»، «ورزش»، «فناوری»، «انرژی» یا دستهٔ مناسب دیگر

ب) اگر content_type «اظهارنظر شخص‌محور» است، برای هر گوینده جداگانه ثبت کن:
- name: نام گوینده؛ اگر متن واقعاً گوینده را مشخص نکرده «نامشخص»
- position: سمت یا وابستگی سازمانیِ تصریح‌شده؛ در غیر این صورت «نامشخص»
- specific_topic: موضوع مستقیم سخنان او، کوتاه و دقیق
- expression_method: شیوه و محل بیان سخن با سه جزء زیر:
  - type: فقط یکی از «صفحه شخصی در شبکه اجتماعی»، «مصاحبه با خبرگزاری»، «مصاحبه رسانه‌ای»، «سخنرانی»، «نشست خبری»، «دیدار»، «جلسه رسمی»، «برنامه تلویزیونی یا رادیویی»، «بیانیه یا اطلاعیه»، «یادداشت یا مقاله»، «نامه»، «نامشخص»
  - source_or_context: نام رسانه، محل، برنامه، دیدار یا زمینه‌ای که در متن صراحتاً آمده؛ در غیر این صورت «نامشخص»
  - evidence: عبارت کوتاهی از متن که شیوه یا محل بیان را نشان می‌دهد
- speaker_evidence: عبارت کوتاهی از متن که انتساب سخن به آن شخص را نشان می‌دهد

راهنمای تشخیص شیوهٔ بیان:
- متن منتشرشده در حساب یا صفحهٔ شخصی: «صفحه شخصی در شبکه اجتماعی».
- گفت‌وگو با خبرگزاری یا رسانه: «مصاحبه با خبرگزاری» یا «مصاحبه رسانه‌ای».
- ایراد سخن در مراسم: «سخنرانی»؛ نشست پاسخ‌گویی با خبرنگاران: «نشست خبری».
- گفت‌وگو در دیدار یا جلسه: «دیدار» یا «جلسه رسمی»؛ حضور در برنامهٔ صداوسیما: «برنامه تلویزیونی یا رادیویی».
- متن رسمی منتسب به فرد یا نهاد: «بیانیه یا اطلاعیه»؛ متن تحلیلی شخص: «یادداشت یا مقاله»؛ نامهٔ رسمی: «نامه».

پ) اگر content_type «رویداد مهم ایران و جهان» است:
- speakers باید آرایهٔ خالی باشد.
- event را با title، involved_entities (آرایهٔ نام اشخاص/نهادهای درگیر که در متن آمده‌اند)، location و time تکمیل کن.
- اگر بخشی از اطلاعات رویداد در متن نیست، برای رشته‌ها «نامشخص» و برای involved_entities آرایهٔ خالی ثبت کن.

قواعد قطعی:
- فقط از اطلاعات موجود در متن و فرادادهٔ همان پیام استفاده کن و چیزی را حدس نزن.
- شناسنامه اشخاص، فهرست مقامات یا دانش بیرونی نباید در این مرحله باعث ایجاد، حدس یا تکمیل نام و سمت شود.
- نام گوینده (speakers[].name) و سمت او (speakers[].position) فقط وقتی پر شوند که متن یا فراداده پیام صراحتاً آن‌ها را نشان دهد؛ در غیر این صورت «نامشخص».
- تیتر، نام کانال و نام خبرگزاری لزوماً گوینده نیستند.
- اگر چند گوینده وجود دارند، هر کدام را جداگانه استخراج کن.
- اگر رویداد در متن آمده اما سخنِ مستقیم و منتسب به شخص موضوع اصلی است، نوع «اظهارنظر شخص‌محور» را انتخاب کن.
- اگر خبر فقط وقوع یک رویداد را گزارش می‌کند، حتی با وجود نام افراد حاضر، نوع «رویداد مهم ایران و جهان» است.
- موضوع (general_topic)، زیرموضوع/موضوع مشخص (specific_topic)، موجودیت‌های رویداد (involved_entities)، نوع پیام (content_type) و سایر بخش‌های تحلیل را مطابق همین قواعد و بدون اتکا به شناسنامه انجام بده.
- پاسخ را فقط به‌صورت JSON معتبر و بدون هیچ توضیح اضافی ارائه کن.

ساختار خروجی:

{
  "content_type": "اظهارنظر شخص‌محور یا رویداد مهم ایران و جهان",
  "main_subject": "موضوع محوری پیام",
  "general_topic": "موضوع کلی",
  "speakers": [
    {
      "name": "نام گوینده یا نامشخص",
      "position": "سمت یا نامشخص",
      "specific_topic": "موضوع مشخص سخنان",
      "expression_method": {
        "type": "شیوه بیان یا نامشخص",
        "source_or_context": "محل یا زمینه بیان یا نامشخص",
        "evidence": "شاهد شیوه بیان"
      },
      "speaker_evidence": "شاهد انتساب سخن به گوینده"
    }
  ],
  "event": {
    "title": "عنوان رویداد یا نامشخص",
    "involved_entities": ["اشخاص یا نهادهای درگیر"],
    "location": "محل رویداد یا نامشخص",
    "time": "زمان رویداد یا نامشخص"
  }
}

متن پیام:
"""
{{MESSAGE}}
"""'''

EDITORIAL_SPEAKER_SUMMARY_PROMPT = '''شما یک تحلیل‌گر حرفه‌ای اخبار فارسی هستید. متن مرجع زیر شامل یک یا چند خبر، نقل‌قول یا گزارش از سخنان یک فرد است.

تمام مطالب را بررسی، یکپارچه و بدون تکرار خلاصه کن. خروجی باید منعکس‌کننده سخنان، دیدگاه و موضع همان فرد باشد، نه تحلیل رسانه یا نویسنده خبر.

خروجی را در سه سطح تولید کن:

1. خلاصه یک‌پاراگرافی:
تمام نکات اصلی، استدلال‌ها، نگرانی‌ها، درخواست‌ها و مواضع گوینده را در یک پاراگراف منسجم خلاصه کن.
خبر را از زبان خود گوینده بنویس؛ طوری که گویی همان شخص در حال بیان خبر است، نه گزارشگر.
فعل‌ها را در ساخت اول‌شخص صرف کن، اما واژهٔ «من»، «از نظر من»، «به اعتقاد من»، «تأکید می‌کنم»، «باید بگویم» و عبارت‌های مشابه را نیاور.
از عبارت‌هایی مانند «او گفت»، «وی افزود» و «به گفته او» استفاده نکن.

2. خلاصه یک‌خطی:
مجموع سخنان و موضع اصلی گوینده را در یک جمله کوتاه، دقیق و فشرده بیان کن.
این جمله نیز باید از زبان خود گوینده نوشته شود و مهم‌ترین پیام او را منتقل کند.

3. خلاصه عبارتی موضع:
مهم‌ترین جهت‌گیری، مطالبه یا اقدام موردنظر گوینده را در قالب یک عبارت بسیار کوتاه و معنادار بیان کن.
عبارت باید مشخص کند گوینده درباره موضوع اصلی چه تغییر، اقدام یا نتیجه‌ای را دنبال می‌کند؛ نه صرفاً نوع بیان او را با واژه‌هایی مانند «حمایت» یا «انتقاد» توصیف کند.
عبارت را ترجیحاً بین ۲ تا ۶ کلمه و به‌صورت یک ترکیب اسمی بنویس؛ در صورت امکان «اقدام یا جهت‌گیری موردنظر + موضوع یا هدف آن» باشد.
نمونه: «انتقال انبار نفت به خارج شهر»، «کاهش مالیات واحدهای تولیدی»، «توقف واردات خودرو».

قواعد:
- فقط سخنانی را خلاصه کن که در متن به گوینده موردنظر نسبت داده شده‌اند.
- اطلاعات، انگیزه یا موضع جدیدی به متن اضافه نکن.
- نکات تکراری در خبرهای مختلف را ادغام کن.
- اگر گزارش‌ها دارای جزئیات مکمل هستند، آن‌ها را به‌شکل منسجم ترکیب کن.
- اگر میان گزارش‌ها تعارض وجود دارد، آن را قطعی جلوه نده.
- درجه قطعیت، تردید، شرط یا احتمال موجود در سخنان را حفظ کن.
- لحن گوینده را حفظ کن، اما متن را روان و فشرده بنویس.
- خبر باید از زبان همان شخص جاری شود، بدون خودارجاعی و بدون قیدهایی مثل «از نظر من» یا «تأکید می‌کنم».
- خلاصه یک‌پاراگرافی باید دقیقاً یک پاراگراف باشد.
- خلاصه یک‌خطی باید دقیقاً یک جمله باشد.
- به‌جای عبارت‌های کلی، اقدام یا نتیجهٔ موردنظر را مشخص کن؛ فقط نام موضوع را ننویس.
- از جمله کامل، فعل صرف‌شده و توضیح اضافی استفاده نکن. اگر اقدام مشخص نبود، موضوع محوری را کوتاه بنویس.
- هیچ جهت‌گیری یا اقدامی را که در متن نیست استنباط نکن.
- پاسخ را فقط به‌صورت JSON معتبر و بدون توضیح اضافی ارائه کن.

نام گوینده:
{{SPEAKER_NAME}}

متن مرجع:
"""
{{REFERENCE_TEXT}}
"""

ساختار خروجی:

{
  "paragraph_summary": "خلاصه یک‌پاراگرافی از زبان گوینده",
  "one_sentence_summary": "خلاصه یک‌جمله‌ای از زبان گوینده",
  "detail": "عبارت کوتاهِ موضع"
}'''

EDITORIAL_EVENT_SUMMARY_PROMPT = '''شما یک دبیر حرفه‌ای اخبار فارسی هستید. متن مرجع زیر دربارهٔ یک رویداد مستقل است، نه یک اظهارنظر قابل انتساب به یک گوینده.

فقط بر پایهٔ متن، سه خروجی زیر را بدون افزودن واقعیت یا تحلیل بیرونی تولید کن:
1. paragraph_summary: یک پاراگراف منسجم، بی‌طرف و خبری از رویداد، بازیگران/نهادهای ذکرشده و پیامد یا اقدام مطرح‌شده.
2. one_sentence_summary: یک جملهٔ کوتاه و خبری که محور رویداد را بیان کند.
3. detail: یک عبارت ۲ تا ۶ کلمه‌ای که اقدام، تغییر یا موضوع محوری رویداد را روشن کند.

اگر جزئیاتی در متن نیست، حدس نزن. پاسخ فقط JSON معتبر با این ساختار باشد:
{
  "paragraph_summary": "...",
  "one_sentence_summary": "...",
  "detail": "..."
}

عنوان رویداد:
{{EVENT_TITLE}}

متن مرجع:
"""
{{REFERENCE_TEXT}}
"""'''

HIGH_ATTENTION_PROMPT = '''شما دبیر بخش «پربازتاب» یک بولتن خبری هستید. ورودی شامل مجموعه‌ای از اخبار، اظهارنظرها، مصاحبه‌ها، سخنرانی‌ها، بیانیه‌ها و واکنش‌های منتشرشده در یک بازه زمانی مشخص است.

وظیفه شما شناسایی و تدوین مهم‌ترین محورهای پربازتاب در میان این محتواهاست.

تعریف «محور پربازتاب»:
یک اظهارنظر یا موضوع اظهارنظری زمانی پربازتاب محسوب می‌شود که دست‌کم یکی از نشانه‌های زیر را داشته باشد:
- در چند خبر یا منبع مختلف مورد توجه و پیگیری قرار گرفته باشد.
- چند شخص، مقام، سازمان یا جریان درباره آن اظهارنظر کرده باشند.
- اظهارنظر اولیه باعث واکنش، حمایت، مخالفت، انتقاد، تکذیب، توضیح یا پاسخ دیگران شده باشد.
- در طول بازه زمانی ورودی چند مرتبه به آن بازگشته شده باشد.
- به محور یک بحث خبری، سیاسی، اجتماعی، اقتصادی یا رسانه‌ای تبدیل شده باشد.

مراحل تحلیل:
1. اخبار تکراری، بازنشرها و روایت‌های مشابه را شناسایی و ادغام کن.
2. اظهارنظرها و واکنش‌هایی را که درباره یک مسئله مشترک هستند، در قالب یک «محور» گروه‌بندی کن.
3. میزان بازتاب هر محور را فقط بر اساس شواهد موجود در ورودی ارزیابی کن.
4. محورهایی را انتخاب کن که بیشترین پوشش، واکنش یا تنوع اظهارنظر را داشته‌اند.
5. حداکثر ۴ محور را به‌ترتیب میزان بازتاب ارائه کن.

معیارهای اولویت‌بندی:
- تعداد اخبار مستقل مرتبط با محور
- تعداد اشخاص یا نهادهای متفاوتی که درباره آن موضع گرفته‌اند
- وجود واکنش مستقیم، پاسخ، مخالفت، حمایت یا تکذیب
- تنوع دیدگاه‌ها و مواضع مطرح‌شده
- استمرار پوشش موضوع در مجموعه اخبار
- اهمیت خبری اظهارنظر و پیامدهای مطرح‌شده برای آن

توجه: تکرار یک خبر یکسان در چند کانال یا بازنشر عین همان محتوا، به‌تنهایی نشانه پربازتاب بودن نیست. واکنش اشخاص و نهادهای مختلف یا پیگیری موضوع در خبرهای مستقل، اهمیت بیشتری دارد.

برای هر محور منتخب، دو بخش تولید کن:

1. تیتر:
- کوتاه، چگال و خبری باشد: حجم کمتر، اما محتوای دقیق‌تر و غنی‌تر.
- موضوع اصلی و جهت اظهارنظر یا واکنش‌ها را در کمترین واژهٔ ممکن منعکس کند.
- در صورت محوری بودن یک شخص، نام او را در تیتر بیاور؛ در غیر این صورت محور موضوع مشترک یا تقابل دیدگاه‌ها را بنویس.
- مبهم، هیجانی، جانبدارانه، شعاری یا کلیک‌محور نباشد.
- ترجیحاً ۴ تا ۹ کلمه باشد و هیچ واژهٔ زائدی نداشته باشد.

2. توضیح:
- در یک پاراگراف کوتاه و منسجم، اظهارنظر اصلی، گوینده و واکنش‌ها یا مواضع پس از آن را جمع‌بندی کن.
- مهم‌ترین دیدگاه‌های موافق، مخالف، انتقادی یا تکمیلی را همان‌جا بیاور و فقط بر مبنای ورودی توضیح بده چرا موضوع پربازتاب شده است.
- نام اشخاص و نسبت دادن مواضع دقیق باشد، تیتر تکرار نشود و متن ترجیحاً ۲ تا ۴ جمله باشد.

قواعد:
- حداکثر ۴ محور ارائه کن؛ ۴ سهمیه نیست و اگر فقط یک یا دو محور واقعاً پربازتاب است همان تعداد را برگردان.
- هر محور فقط یک‌بار بیاید و خبرهای یک موضوع واحد به چند عنوان جدا تبدیل نشوند.
- رویدادی که فقط اتفاق افتاده و واکنش یا اظهارنظر قابل‌توجهی ندارد در این بخش قرار نده.
- فقط از اطلاعات موجود در ورودی استفاده کن، بازتاب یا واکنش را حدس نزن و اختلاف دیدگاه‌ها را بی‌طرفانه منعکس کن.
- اگر هیچ محور پربازتابی قابل شناسایی نیست، آرایه خالی برگردان.
- پاسخ را فقط به‌صورت JSON معتبر و بدون مقدمه یا توضیح اضافی ارائه کن.

ساختار خروجی:

{
  "high_attention_topics": [
    {
      "title": "تیتر محور پربازتاب",
      "summary": "یک پاراگراف کوتاه درباره اظهارنظر اصلی، واکنش‌ها و دلیل برجسته‌شدن موضوع"
    }
  ]
}

مجموعه اخبار:
"""
{{NEWS_COLLECTION}}
"""'''


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class BulletinService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=settings.ai_timeout_seconds)
        self.pipeline = HybridBulletinPipeline(db, settings, self._client)
        self.high_attention_ai_key_pool = AIKeyPool(
            (settings.high_attention_ai_api_key,)
            if settings.high_attention_ai_api_key
            else (),
            max_concurrency=1,
            concurrency_per_key=1,
            circuit_breaker_seconds=settings.ai_circuit_breaker_seconds,
        )
        self.exporter = BulletinExporter(db, settings)
        self.editorial_settings = replace(
            settings,
            ai_provider=settings.editorial_ai_provider,
            ai_api_key=(
                settings.editorial_ai_api_keys[0]
                if settings.editorial_ai_api_keys
                else ""
            ),
            ai_api_keys=settings.editorial_ai_api_keys,
            ai_base_url=settings.editorial_ai_base_url,
            ai_model=settings.editorial_ai_model,
            ai_timeout_seconds=settings.editorial_ai_timeout_seconds,
            ai_temperature=settings.editorial_ai_temperature,
        )
        self.editorial_ai_key_pool = AIKeyPool(
            settings.editorial_ai_api_keys,
            max_concurrency=settings.ai_max_concurrency,
            concurrency_per_key=settings.ai_concurrency_per_key,
            circuit_breaker_seconds=settings.ai_circuit_breaker_seconds,
        )
        self.editorial_rebuilder = EditorialRebuilder(
            db,
            self.editorial_settings,
            self._client,
            self.editorial_ai_key_pool,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _editorial_fallback(base_text: str) -> dict[str, str]:
        clean = " ".join(str(base_text or "").split())
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[.!؟])\s+|\n+", clean)
            if part.strip()
        ]
        paragraph = " ".join(sentences[:3])[:900].strip()
        sentence = (sentences[0] if sentences else clean)[:320].strip()
        stance = "نامشخص"
        stance_patterns = (
            ("حمایت", ("حمایت", "پشتیبانی")),
            ("مخالفت", ("مخالفت", "مخالف")),
            ("انتقاد", ("انتقاد", "انتقاد کرد")),
            ("اعتراض", ("اعتراض", "معترض")),
            ("هشدار", ("هشدار", "هشدار داد")),
            ("درخواست", ("درخواست", "خواستار")),
            ("تأکید", ("تأکید", "تاکید")),
            ("تکذیب", ("تکذیب", "رد کرد")),
            ("دفاع", ("دفاع", "دفاع کرد")),
            ("نگرانی", ("نگران", "نگرانی")),
            ("تهدید", ("تهدید",)),
            ("پیشنهاد", ("پیشنهاد",)),
        )
        normalized = normalize_persian(clean)
        for label, needles in stance_patterns:
            if any(needle in normalized for needle in needles):
                stance = label
                break
        return {
            "summary_paragraph": paragraph,
            "summary_sentence": sentence,
            "summary_title": stance,
            "detail": sentence[:180] if sentence else stance,
        }

    @staticmethod
    def _validate_selected_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize the model-one envelope without silently turning an event into a person.

        The old implementation accepted only speaker tags.  Keeping the
        normalization in one place makes the stored analysis resilient to
        harmless model variation while retaining the deliberately strict split
        between a person-centred statement and a standalone event.
        """
        if not isinstance(payload, dict):
            raise RuntimeError("خروجی مدل تحلیل باید یک شیء JSON باشد.")
        raw_type = normalize_persian(str(payload.get("content_type") or "")).strip()
        event_markers = ("رویداد", "event")
        content_type = (
            "event"
            if any(marker in raw_type.casefold() for marker in event_markers)
            else "person_statement"
        )
        raw_speakers = payload.get("speakers")
        if not isinstance(raw_speakers, list):
            raw_speakers = []
        speakers: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in raw_speakers:
            if not isinstance(raw, dict):
                continue
            expression_method = raw.get("expression_method")
            if not isinstance(expression_method, dict):
                expression_method = {}
            item = {
                "name": normalize_persian(str(raw.get("name") or "نامشخص")).strip()
                or "نامشخص",
                "position": normalize_persian(
                    str(raw.get("position") or "نامشخص")
                ).strip()
                or "نامشخص",
                "specific_topic": normalize_persian(
                    str(raw.get("specific_topic") or "نامشخص")
                ).strip()
                or "نامشخص",
                "general_topic": normalize_persian(
                    str(raw.get("general_topic") or payload.get("general_topic") or "نامشخص")
                ).strip()
                or "نامشخص",
                "evidence": normalize_persian(
                    str(raw.get("speaker_evidence") or raw.get("evidence") or "")
                ).strip(),
                "expression_method_type": normalize_persian(
                    str(expression_method.get("type") or "نامشخص")
                ).strip()
                or "نامشخص",
                "expression_method_context": normalize_persian(
                    str(expression_method.get("source_or_context") or "نامشخص")
                ).strip()
                or "نامشخص",
                "expression_method_evidence": normalize_persian(
                    str(expression_method.get("evidence") or "")
                ).strip(),
            }
            key = (
                canonical_key(item["name"]),
                canonical_key(item["specific_topic"]),
                canonical_key(item["general_topic"]),
            )
            if key in seen:
                continue
            seen.add(key)
            speakers.append(item)
        if content_type == "person_statement" and not speakers:
            speakers.append(
                {
                    "name": "نامشخص",
                    "position": "نامشخص",
                    "specific_topic": "نامشخص",
                    "general_topic": "نامشخص",
                    "evidence": "",
                    "expression_method_type": "نامشخص",
                    "expression_method_context": "نامشخص",
                    "expression_method_evidence": "",
                }
            )
        raw_event = payload.get("event")
        if not isinstance(raw_event, dict):
            raw_event = {}
        raw_entities = raw_event.get("involved_entities")
        entities = (
            [
                normalize_persian(str(value)).strip()[:240]
                for value in raw_entities
                if normalize_persian(str(value)).strip()
            ]
            if isinstance(raw_entities, list)
            else []
        )
        main_subject = normalize_persian(str(payload.get("main_subject") or "")).strip()
        general_topic = normalize_persian(str(payload.get("general_topic") or "")).strip()
        event = {
            "title": normalize_persian(
                str(raw_event.get("title") or main_subject or "نامشخص")
            ).strip()
            or "نامشخص",
            "involved_entities": list(dict.fromkeys(entities)),
            "location": normalize_persian(
                str(raw_event.get("location") or "نامشخص")
            ).strip()
            or "نامشخص",
            "time": normalize_persian(str(raw_event.get("time") or "نامشخص")).strip()
            or "نامشخص",
        }
        if content_type == "event":
            # A model may leave residual speaker objects while correctly
            # classifying an event.  They must not leak into person grouping.
            speakers = []
        return {
            "content_type": content_type,
            "main_subject": main_subject or (
                event["title"] if content_type == "event" else speakers[0]["specific_topic"]
            ),
            "general_topic": general_topic
            or (speakers[0]["general_topic"] if speakers else "نامشخص"),
            "speakers": speakers,
            "event": event if content_type == "event" else None,
        }

    @staticmethod
    def _positions_are_same_role(extracted: str, registry: str) -> bool:
        left = canonical_key(extracted)
        right = canonical_key(registry)
        if not left or not right or left == canonical_key("نامشخص"):
            return False
        if left == right:
            return True
        if left in right or right in left:
            return True
        left_words = {word for word in left.split() if len(word) >= 3}
        right_words = {word for word in right.split() if len(word) >= 3}
        if not left_words or not right_words:
            return False
        overlap = left_words & right_words
        return len(overlap) >= max(1, min(len(left_words), len(right_words)) - 1)

    async def _match_speakers_to_registry(
        self,
        speakers: list[dict[str, Any]],
        *,
        message_text: str,
    ) -> list[dict[str, Any]]:
        """Apply post-extraction registry identity matching without inventing fields.

        Rules (engine-one only):
        1. No name/position → leave untouched; never invent from registry.
        2. Title-only labels are not names.
        3. Match extracted name against canonical name + aliases.
        4. Bind only when a name/alias actually appears in the message text.
        5. If evidence is present, it must be a substring of the text and contain
           the grounded name.
        6. Short single-token names bind only when that exact unique alias is the
           grounded span.
        7. Use extracted position only to disambiguate same-name people.
        8. Unique grounded match → replace name with registry canonical full_name.
        9. Ambiguous / ungrounded / no match → keep the initial extraction exactly.
        """

        matched: list[dict[str, Any]] = []
        for speaker in speakers:
            item = dict(speaker)
            name = normalize_persian(str(item.get("name") or "")).strip()
            position = normalize_persian(str(item.get("position") or "")).strip()
            evidence = normalize_persian(str(item.get("evidence") or "")).strip()
            if not name or name == "نامشخص" or is_title_only_name(name):
                if is_title_only_name(name) and name and name != "نامشخص":
                    item["name"] = "نامشخص"
                    item.pop("person_id", None)
                matched.append(item)
                continue
            candidates = await self.db.find_people_by_name(name)
            if position and position != "نامشخص" and len(candidates) > 1:
                narrowed = [
                    person
                    for person in candidates
                    if self._positions_are_same_role(
                        position, str(person.get("position") or "")
                    )
                ]
                if len(narrowed) == 1:
                    candidates = narrowed
            if len(candidates) != 1:
                matched.append(item)
                continue
            person = await self.db.get_person(int(candidates[0]["person_id"]))
            if not person:
                matched.append(item)
                continue
            aliases = [
                str(row.get("alias_text") or "")
                for row in person.get("alias_rows") or []
            ]
            grounded = grounding_span(
                message_text,
                extracted_name=name,
                full_name=str(person.get("full_name") or ""),
                aliases=aliases,
            )
            if not grounded or not may_bind_short_name(name, grounded):
                matched.append(item)
                continue
            if not evidence_supports_name(message_text, evidence, grounded):
                matched.append(item)
                continue
            item["name"] = str(person.get("full_name") or name)
            item["person_id"] = int(person["person_id"])
            registry_position = normalize_persian(str(person.get("position") or "")).strip()
            if (
                position
                and position != "نامشخص"
                and registry_position
                and self._positions_are_same_role(position, registry_position)
            ):
                item["position"] = registry_position
            matched.append(item)
        return matched

    async def _analyze_selected_message(
        self,
        message_id: int,
        *,
        actor: str,
    ) -> dict[str, Any]:
        """Analyze and persist one selected message without affecting siblings."""
        message = await self.db.message_detail(message_id)
        if not message:
            return {"message_id": message_id, "ok": False, "error": "پیام پیدا نشد."}
        text = str(message.get("text") or message.get("caption") or "").strip()
        if not text:
            return {
                "message_id": message_id,
                "ok": False,
                "error": "پیام متن یا کپشن قابل تحلیل ندارد.",
            }

        prompt = SELECTED_MESSAGE_ANALYSIS_PROMPT.replace(
            "{{MESSAGE}}",
            text[: self.settings.ai_enrichment_max_chars_per_message],
        )
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        try:
            parsed = await self.pipeline._call_json(
                run_id=None,
                item_id=None,
                input_hash=input_hash,
                system_prompt=(
                    "شما تحلیل‌گر متن خبری فارسی هستید و فقط JSON معتبر "
                    "مطابق ساختار خواسته‌شده برمی‌گردانید. شناسنامه اشخاص را "
                    "در استخراج اولیه دخالت ندهید."
                ),
                user_prompt=prompt,
                request_kind="selected_message_speaker_analysis",
                prompt_version=SELECTED_ANALYSIS_PROMPT_VERSION,
            )
            analysis = self._validate_selected_analysis_payload(parsed)
            # Phase 2: registry matching happens only after text-only extraction
            # and only when a name/alias actually appears in this message.
            speakers = await self._match_speakers_to_registry(
                analysis["speakers"],
                message_text=text,
            )
            analysis["speakers"] = speakers
            tags = await self.db.replace_message_speaker_tags(
                message_id,
                speakers,
                provider=self.settings.ai_provider,
                model=self.settings.ai_model,
                prompt_version=SELECTED_ANALYSIS_PROMPT_VERSION,
                analyzed_by=actor,
                content_type=analysis["content_type"],
                main_subject=analysis["main_subject"],
                general_topic=analysis["general_topic"],
                event=analysis["event"],
            )
            await self.db.add_action(
                message_id,
                None,
                "selected_message_ai_analyzed",
                details={
                    "actor": actor,
                    "speaker_count": len(tags),
                    "content_type": analysis["content_type"],
                    "prompt_version": SELECTED_ANALYSIS_PROMPT_VERSION,
                },
            )
            return {
                "message_id": message_id,
                "ok": True,
                "speakers": tags,
                "analysis": analysis,
            }
        except Exception as exc:
            logger.exception("Selected message analysis failed for %s", message_id)
            return {
                "message_id": message_id,
                "ok": False,
                "error": str(exc),
            }

    async def analyze_selected_messages(
        self,
        message_ids: list[int],
        *,
        actor: str,
    ) -> dict[str, Any]:
        if not self.pipeline._ai_ready:
            raise RuntimeError(
                "مدل تحلیل خبر فعال نیست؛ تنظیمات AI_ANALYSIS_* را بررسی کنید."
            )
        unique_ids = list(dict.fromkeys(int(value) for value in message_ids))
        if not unique_ids:
            raise RuntimeError("حداقل یک پیام برای تحلیل انتخاب کنید.")
        if len(unique_ids) > 200:
            raise RuntimeError("در هر نوبت حداکثر ۲۰۰ پیام قابل تحلیل است.")

        selected_rows = await self.db.messages_for_duplicate_compare(unique_ids)
        already_duplicate = {
            int(row["id"]): int(row["duplicate_of"] or row["id"])
            for row in selected_rows
            if row.get("duplicate_of")
            or str(row.get("ai_enrichment_status") or "").lower() == "duplicate"
        }
        fresh_rows = [
            row for row in selected_rows if int(row["id"]) not in already_duplicate
        ]
        existing_rows = await self.db.recent_message_bodies_for_duplicate_compare(
            exclude_ids=unique_ids,
            days=7,
            limit=800,
        )
        claimed_existing = [
            row
            for row in existing_rows
            if row.get("duplicate_of")
            or str(row.get("ai_enrichment_status") or "").lower()
            in {"validated", "duplicate"}
        ]
        claimed_ids = {int(row["id"]) for row in claimed_existing}
        pending_existing = [
            row for row in existing_rows if int(row["id"]) not in claimed_ids
        ]
        representatives, duplicate_of = choose_representatives(
            [*fresh_rows, *pending_existing],
            threshold=self.settings.analysis_near_duplicate_threshold,
            existing=claimed_existing,
        )
        duplicate_of = {**already_duplicate, **duplicate_of}
        skipped_results: list[dict[str, Any]] = []
        if duplicate_of:
            await self.db.mark_messages_duplicate(duplicate_of)
            skipped_results = [
                {
                    "message_id": message_id,
                    "ok": True,
                    "skipped": "duplicate",
                    "duplicate_of": representative_id,
                }
                for message_id, representative_id in duplicate_of.items()
            ]
        analyze_ids = [
            message_id
            for message_id in unique_ids
            if message_id in set(representatives) and message_id not in duplicate_of
        ]

        # The pool rotates calls across configured API keys.  A local limiter
        # prevents a large UI selection from creating more work than the
        # configured key/concurrency budget can serve at once.
        parallelism = max(
            1,
            min(
                max(len(analyze_ids), 1),
                self.settings.ai_max_concurrency,
                self.pipeline.ai_key_pool.key_count,
            ),
        )
        limiter = asyncio.Semaphore(parallelism)

        async def analyze_bounded(message_id: int) -> dict[str, Any]:
            async with limiter:
                return await self._analyze_selected_message(message_id, actor=actor)

        analyzed = list(
            await asyncio.gather(*(analyze_bounded(message_id) for message_id in analyze_ids))
        ) if analyze_ids else []
        results = analyzed + skipped_results
        analyzed_ok = sum(1 for result in analyzed if result.get("ok"))
        failed = sum(1 for result in analyzed if not result.get("ok"))
        return {
            "ok": failed == 0,
            "requested": len(unique_ids),
            "succeeded": analyzed_ok,
            "failed": failed,
            "analyzed": analyzed_ok,
            "skipped_duplicates": len(duplicate_of),
            "results": results,
            "prompt_version": SELECTED_ANALYSIS_PROMPT_VERSION,
            "parallelism": parallelism,
            "configured_key_count": self.pipeline.ai_key_pool.key_count,
        }

    @staticmethod
    def _validate_high_attention_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
        if not isinstance(payload, dict):
            raise RuntimeError("خروجی پربازتاب باید یک شیء JSON باشد.")
        raw_items = payload.get("high_attention_topics")
        if not isinstance(raw_items, list):
            raise RuntimeError("خروجی پربازتاب فاقد آرایه high_attention_topics است.")
        items: list[dict[str, str]] = []
        seen_titles: set[str] = set()
        for raw in raw_items[:4]:
            if not isinstance(raw, dict):
                continue
            title = normalize_persian(str(raw.get("title") or "")).strip()
            summary = normalize_persian(str(raw.get("summary") or "")).strip()
            if not title or not summary:
                continue
            key = canonical_key(title)
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            items.append({"title": title[:220], "summary": summary[:1800]})
        # An empty result is valid: the prompt explicitly prohibits padding the
        # section with weakly supported subjects.
        return items

    async def generate_high_attention(
        self,
        drafts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Use analysis-model API key slot 8 for the editorial Pervaztab list."""
        if not drafts:
            raise RuntimeError("حداقل یک خبر نهایی برای تولید پربازتاب لازم است.")
        if not self.pipeline._ai_ready:
            raise RuntimeError(
                "مدل اول تحلیل فعال نیست؛ تنظیمات AI_ANALYSIS_* را بررسی کنید."
            )
        if not self.high_attention_ai_key_pool.configured:
            raise RuntimeError(
                "کلید هشتم مدل اول تنظیم نشده است؛ AI_ANALYSIS_API_KEY_8 را در .env وارد کنید."
            )
        news_blocks: list[str] = []
        for index, draft in enumerate(drafts, start=1):
            fields = draft.get("bulletin_fields") if isinstance(draft.get("bulletin_fields"), dict) else {}
            event = fields.get("event") if isinstance(fields.get("event"), dict) else {}
            subject = (
                draft.get("person_name")
                or event.get("title")
                or draft.get("event_title")
                or draft.get("title")
                or "نامشخص"
            )
            text = "\n".join(
                value
                for value in (
                    f"خبر {index}",
                    f"عنوان: {draft.get('title') or subject}",
                    f"شخص/رویداد: {subject}",
                    f"سمت: {draft.get('position') or 'نامشخص'}",
                    f"دسته: {draft.get('category_name') or 'نامشخص'}",
                    f"موضوع: {draft.get('topic_name') or 'نامشخص'}",
                    f"محل بیان: {draft.get('oration_location') or 'نامشخص'}",
                    f"خلاصه یک‌خطی: {draft.get('summary_sentence') or ''}",
                    f"خلاصه: {draft.get('summary_paragraph') or ''}",
                    f"متن مرجع: {draft.get('base_text') or ''}",
                )
                if str(value).strip()
            )
            news_blocks.append(text[:7000])
        collection = "\n\n==========\n\n".join(news_blocks)[:48000]
        prompt = HIGH_ATTENTION_PROMPT.replace("{{NEWS_COLLECTION}}", collection)
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        parsed = await self.pipeline._call_json(
            run_id=None,
            item_id=None,
            input_hash=input_hash,
            system_prompt=(
                "شما دبیر بخش پربازتاب بولتن خبری فارسی هستید و فقط JSON معتبر "
                "مطابق ساختار خواسته‌شده برمی‌گردانید."
            ),
            user_prompt=prompt,
            request_kind="high_attention_topics",
            prompt_version=HIGH_ATTENTION_PROMPT_VERSION,
            key_pool=self.high_attention_ai_key_pool,
            key_slot_offset=7,
        )
        return {
            "items": self._validate_high_attention_payload(parsed),
            "provider": self.settings.ai_provider,
            "model": self.settings.ai_model,
            "prompt_version": HIGH_ATTENTION_PROMPT_VERSION,
            "api_key_slot": 8,
        }

    def _editorial_profile_status(self) -> dict[str, Any]:
        editorial = self.editorial_settings
        pool = self.editorial_ai_key_pool
        configured = bool(
            editorial.ai_provider != "disabled"
            and editorial.ai_model
            and pool.configured
        )
        distinct = bool(
            configured
            and (
                not self.settings.ai_model
                or editorial.ai_model.casefold() != self.settings.ai_model.casefold()
            )
        )
        configuration_error = None
        if not configured:
            configuration_error = (
                "مدل دوم تدوین فعال نیست؛ تنظیمات AI_EDITORIAL_* را بررسی کنید."
            )
        elif not distinct:
            configuration_error = (
                "نام مدل تدوین با مدل تحلیل یکسان تنظیم شده است؛ "
                "AI_EDITORIAL_MODEL باید یک مدل متفاوت باشد."
            )
        return {
            "configured": configured,
            "enabled": configured and distinct,
            "distinct_from_analysis": distinct,
            "configuration_error": configuration_error,
        }

    async def _editorial_ai_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        request_kind: str,
        prompt_version: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        editorial = self.editorial_settings
        pool = self.editorial_ai_key_pool
        profile = self._editorial_profile_status()
        if not profile["enabled"]:
            return None, str(profile["configuration_error"])

        input_hash = hashlib.sha256(
            f"{system_prompt}\n\n{user_prompt}".encode("utf-8")
        ).hexdigest()
        cached = await self.db.get_cached_ai_response(
            input_hash=input_hash,
            provider=editorial.ai_provider,
            model=editorial.ai_model,
            prompt_version=prompt_version,
            request_kind=request_kind,
        )
        if cached and isinstance(cached.get("parsed_response"), dict):
            return dict(cached["parsed_response"]), None

        endpoint = (
            f"{editorial.ai_base_url}/responses"
            if editorial.ai_provider == "openai"
            else f"{editorial.ai_base_url}/chat/completions"
        )
        last_error = ""
        attempts = max(1, int(self.settings.editorial_ai_max_retries))
        for attempt in range(1, attempts + 1):
            lease = None
            raw: Any = None
            http_status: int | None = None
            started = time.perf_counter()
            try:
                lease = await pool.acquire()
                if editorial.ai_provider == "openai":
                    payload = {
                        "model": editorial.ai_model,
                        "input": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "text": {"format": {"type": "json_object"}},
                        "temperature": editorial.ai_temperature,
                    }
                else:
                    payload = {
                        "model": editorial.ai_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": editorial.ai_temperature,
                    }
                response = await self._client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {lease.key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=editorial.ai_timeout_seconds,
                )
                http_status = response.status_code
                try:
                    raw = response.json()
                except ValueError:
                    raw = {"text": response.text}
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise RuntimeError(f"HTTP {response.status_code}")
                response.raise_for_status()
                parsed = json.loads(
                    _clean_json_output(_extract_output_text(raw))
                )
                if not isinstance(parsed, dict):
                    raise ValueError("پاسخ هوش باید شیء JSON باشد.")
                latency = int((time.perf_counter() - started) * 1000)
                await pool.release_success(lease)
                await self.db.add_ai_request(
                    run_id=None,
                    item_id=None,
                    input_hash=input_hash,
                    provider=editorial.ai_provider,
                    model=editorial.ai_model,
                    prompt_version=prompt_version,
                    request_kind=request_kind,
                    raw_request={"kind": request_kind},
                    raw_response=raw,
                    parsed_response=parsed,
                    token_usage=raw.get("usage") if isinstance(raw, dict) else None,
                    latency_ms=latency,
                    status="validated",
                    error_text=None,
                    key_slot=lease.slot,
                    attempt_number=attempt,
                    endpoint=endpoint,
                    http_status=http_status,
                )
                return parsed, None
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                key_slot = lease.slot if lease is not None else None
                if lease is not None:
                    await pool.release_failure(
                        lease,
                        kind="transient",
                        reason=last_error,
                    )
                await self.db.add_ai_request(
                    run_id=None,
                    item_id=None,
                    input_hash=input_hash,
                    provider=editorial.ai_provider,
                    model=editorial.ai_model,
                    prompt_version=prompt_version,
                    request_kind=request_kind,
                    raw_request={"kind": request_kind},
                    raw_response=raw,
                    parsed_response=None,
                    token_usage=None,
                    latency_ms=latency,
                    status="retry" if attempt < attempts else "failed",
                    error_text=last_error,
                    key_slot=key_slot,
                    attempt_number=attempt,
                    endpoint=endpoint,
                    http_status=http_status,
                )
        logger.warning(
            "Editorial generation fell back after %s attempts: %s",
            attempts,
            last_error,
        )
        return None, (
            f"مدل دوم پس از {attempts} تلاش پاسخ معتبر نداد: {last_error}"
        )

    async def generate_editorial_content(
        self,
        draft_id: int,
        *,
        kind: str,
        base_text: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"base", "summaries", "all"}:
            raise RuntimeError("نوع تولید باید base، summaries یا all باشد.")
        draft = await self.db.get_editorial_draft(draft_id)
        if not draft:
            raise RuntimeError("پیش‌نویس پیدا نشد.")
        source_parts: list[str] = []
        seen: set[str] = set()
        for item in draft.get("inputs") or []:
            text = " ".join(
                str(
                    item.get("selected_text")
                    or item.get("text")
                    or item.get("caption")
                    or ""
                ).split()
            )
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                source_parts.append(text)
        deterministic_base = "\n\n".join(source_parts)
        working_base = str(base_text or draft.get("base_text") or deterministic_base).strip()
        profile = self._editorial_profile_status()
        result: dict[str, Any] = {
            "ai_used": False,
            "ai_available": bool(profile["enabled"]),
            "editorial_model": self.editorial_settings.ai_model or None,
            "editorial_model_distinct": bool(profile["distinct_from_analysis"]),
        }
        fallback_reasons: list[str] = []

        if kind in {"base", "all"}:
            # A manual draft has no crawler inputs by design.  Only polish
            # editor-provided text in that case; never fabricate a news item
            # from an empty prompt.
            generation_source = deterministic_base or working_base
            if not generation_source:
                raise RuntimeError(
                    "برای تولید متن پایهٔ خبر دستی، ابتدا متن یا یادداشت‌های اولیه را وارد کنید."
                )
            ai_base, base_error = await self._editorial_ai_json(
                system_prompt=(
                    "شما دبیر خبر فارسی هستید. فقط JSON معتبر برگردانید. "
                    "هیچ واقعیت، نام، عدد یا نقل‌قولی خارج از متن منابع اضافه نکنید."
                ),
                user_prompt=(
                    "از منابع زیر یک متن پایه منسجم و دقیق برای خبر بساز. "
                    "تکرارها را حذف کن اما دیدگاه‌های متفاوت را حفظ کن. "
                    'ساختار خروجی: {"base_text":"..."}\n\n'
                    + generation_source[:16000]
                ),
                request_kind="editorial_base_text",
                prompt_version=EDITORIAL_SPEAKER_PROMPT_VERSION,
            )
            if base_error:
                fallback_reasons.append(base_error)
            generated = str((ai_base or {}).get("base_text") or generation_source).strip()
            result["base_text"] = generated
            result["ai_used"] = bool(ai_base)
            working_base = generated

        if kind in {"summaries", "all"}:
            if not working_base:
                raise RuntimeError("برای ساخت خلاصه، متن پایه لازم است.")
            is_event = str(draft.get("content_type") or "") == "event"
            speaker_name = str(draft.get("person_name") or "نامشخص").strip() or "نامشخص"
            summary_prompt = (
                EDITORIAL_EVENT_SUMMARY_PROMPT
                .replace("{{EVENT_TITLE}}", str(draft.get("event_title") or draft.get("title") or "رویداد"))
                .replace("{{REFERENCE_TEXT}}", working_base[:16000])
                if is_event
                else EDITORIAL_SPEAKER_SUMMARY_PROMPT
                .replace("{{SPEAKER_NAME}}", speaker_name)
                .replace("{{REFERENCE_TEXT}}", working_base[:16000])
            )
            ai_summaries, summary_error = await self._editorial_ai_json(
                system_prompt=(
                    "شما تحلیل‌گر حرفه‌ای اخبار فارسی هستید. فقط JSON معتبر "
                    "و دقیقاً مطابق ساختار خواسته‌شده برگردانید."
                ),
                user_prompt=summary_prompt,
                request_kind="editorial_event_summaries" if is_event else "editorial_speaker_summaries",
                prompt_version=EDITORIAL_EVENT_PROMPT_VERSION if is_event else EDITORIAL_SPEAKER_PROMPT_VERSION,
            )
            if summary_error:
                fallback_reasons.append(summary_error)
            fallback = self._editorial_fallback(working_base)
            paragraph = str(
                (ai_summaries or {}).get("paragraph_summary")
                or fallback["summary_paragraph"]
            ).strip()
            sentence = str(
                (ai_summaries or {}).get("one_sentence_summary")
                or fallback["summary_sentence"]
            ).strip()
            detail = " ".join(str(
                (ai_summaries or {}).get("detail")
                or (ai_summaries or {}).get("stance")
                or fallback.get("detail")
                or fallback["summary_title"]
                or "نامشخص"
            ).split())[:180]
            legacy_stance = " ".join(str(
                (ai_summaries or {}).get("stance")
                or (ai_summaries or {}).get("detail")
                or fallback["summary_title"]
                or detail
            ).split())[:180]
            result["summary_paragraph"] = " ".join(paragraph.split())
            result["summary_sentence"] = " ".join(sentence.split())
            # ``summary_title`` and ``stance`` remain for older exports.  New
            # screens and the bulletin payload use the explicit ``detail``.
            result["summary_title"] = legacy_stance
            result["detail"] = detail
            result["stance"] = legacy_stance
            result["prompt_version"] = EDITORIAL_EVENT_PROMPT_VERSION if is_event else EDITORIAL_SPEAKER_PROMPT_VERSION
            result["ai_used"] = bool(ai_summaries) or bool(result["ai_used"])
        if fallback_reasons:
            result["fallback_reason"] = " | ".join(dict.fromkeys(fallback_reasons))
        return result

    def ai_status(self) -> dict[str, Any]:
        provider = self.settings.ai_provider
        endpoint = None
        if provider == "openai":
            endpoint = f"{self.settings.ai_base_url}/responses"
        elif provider == "openai_compatible":
            endpoint = f"{self.settings.ai_base_url}/chat/completions"
        analysis = {
            "enabled": provider != "disabled" and self.pipeline.ai_key_pool.configured and bool(self.settings.ai_model),
            "provider": provider,
            "model": self.settings.ai_model or None,
            "base_url": self.settings.ai_base_url,
            "endpoint": endpoint,
            "timeout_seconds": self.settings.ai_timeout_seconds,
            "temperature": self.settings.ai_temperature,
            "enrichment": {
                "enabled": self.settings.ai_enrichment_enabled,
                "scope": self.settings.ai_enrichment_scope,
                "batch_size": self.settings.ai_enrichment_batch_size,
                "max_retries": self.settings.ai_enrichment_max_retries,
                "require_validated_output": self.settings.ai_require_validated_output,
            },
            "pool": self.pipeline.ai_key_pool.snapshot(),
        }
        editorial_provider = self.editorial_settings.ai_provider
        editorial_profile = self._editorial_profile_status()
        editorial_endpoint = None
        if editorial_provider == "openai":
            editorial_endpoint = f"{self.editorial_settings.ai_base_url}/responses"
        elif editorial_provider == "openai_compatible":
            editorial_endpoint = f"{self.editorial_settings.ai_base_url}/chat/completions"
        return {
            **analysis,
            "profiles": {
                "analysis": analysis,
                "editorial": {
                    "enabled": editorial_profile["enabled"],
                    "configured": editorial_profile["configured"],
                    "distinct_from_analysis": editorial_profile[
                        "distinct_from_analysis"
                    ],
                    "configuration_error": editorial_profile[
                        "configuration_error"
                    ],
                    "provider": editorial_provider,
                    "model": self.editorial_settings.ai_model or None,
                    "base_url": self.editorial_settings.ai_base_url,
                    "endpoint": editorial_endpoint,
                    "timeout_seconds": self.editorial_settings.ai_timeout_seconds,
                    "temperature": self.editorial_settings.ai_temperature,
                    "pool": self.editorial_ai_key_pool.snapshot(),
                },
            },
            "editorial": {
                "version": "human_editorial_v11",
                "require_package_for_public_export": self.settings.bulletin_require_editorial_package,
                "generate_classic_pdf": self.settings.bulletin_generate_classic_pdf,
                "generate_magazine_pdf": self.settings.bulletin_generate_magazine_pdf,
            },
        }

    async def create_run(
        self,
        *,
        requested_by: str | None,
        template_id: int | None,
        date_from: str | None,
        date_to: str | None,
        source_chat_ids: list[int] | None,
        statuses: list[str] | None,
        filters: dict[str, Any] | None = None,
    ) -> int:
        return await self.db.create_bulletin_run(
            requested_by=requested_by,
            template_id=template_id,
            date_from=date_from,
            date_to=date_to,
            source_chat_ids=source_chat_ids,
            statuses=statuses or ["approved"],
            filters=filters or {},
            provider=self.settings.ai_provider,
            model=self.settings.ai_model or None,
        )

    async def execute_run(self, run_id: int) -> None:
        if not await self.db.claim_bulletin_run(run_id):
            return
        run = await self.db.get_bulletin_run(run_id)
        if not run:
            return
        service_log = await self.db.start_bulletin_stage(
            run_id, "service_start", message="اجرای سرویس تولید بولتن آغاز شد",
            details={
                "requested_by": run.get("requested_by"),
                "provider": run.get("provider"),
                "model": run.get("model"),
                "configured_key_count": self.pipeline.ai_key_pool.key_count,
                "max_ai_concurrency": self.pipeline.ai_key_pool.max_concurrency,
            },
        )
        try:
            template_id = run.get("template_id")
            template = await self.db.get_bulletin_template(int(template_id)) if template_id else None
            if not template:
                templates = await self.db.list_bulletin_templates()
                template = next((x for x in templates if x.get("output_format") == "json_schema"), None)
                template = template or (templates[0] if templates else None)
            if not template:
                raise RuntimeError("هیچ قالب بولتنی تعریف نشده است.")

            output, report = await self.pipeline.execute(run, template)
            await self.db.complete_bulletin_run(
                run_id,
                output_text=output,
                output_html=f"<pre>{html.escape(output)}</pre>",
                raw_response={"mode": "hybrid_pipeline_v10_3", "report": report},
                usage=None,
            )
            await self.db.finish_bulletin_stage(
                service_log,
                status="completed_with_warnings" if int(report.get("warning_count") or 0) else "completed",
                level="WARNING" if int(report.get("warning_count") or 0) else "INFO",
                message="سرویس تولید بولتن تکمیل شد",
                details={"items": report.get("items"), "warnings": report.get("warning_count", 0)},
            )
            await self.db.add_system_event(
                "bulletin", "INFO", "bulletin_pipeline_completed",
                f"Bulletin run {run_id} is ready for editorial review",
                report,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            logger.exception("Bulletin run %s failed", run_id)
            current = await self.db.get_bulletin_run(run_id) or {}
            stage = str(current.get("current_stage") or "unknown")
            await self.db.finish_bulletin_stage(
                service_log, status="failed", level="ERROR", message=str(exc),
                details={"stage": stage, "error_type": type(exc).__name__},
            )
            await self.db.add_bulletin_run_log(
                run_id, stage, status="failed", level="ERROR", message=str(exc),
                details={"error_type": type(exc).__name__, "traceback": tb[-12000:]},
            )
            await self.db.fail_bulletin_run(
                run_id, str(exc), stage=stage, error_type=type(exc).__name__, error_traceback=tb,
            )
            await self.db.add_system_event(
                "bulletin", "ERROR", "bulletin_failed", str(exc), {"run_id": run_id}
            )

    async def rebuild_item_summary(self, item_id: int, *, actor: str) -> dict[str, Any]:
        detail = await self.db.bulletin_item_detail(item_id)
        if not detail:
            raise RuntimeError("آیتم پیدا نشد.")
        topics = await self.db.list_topics_with_keywords()
        topic = next((x for x in topics if int(x["topic_id"]) == int(detail.get("topic_id") or 0)), None)
        if not topic:
            raise RuntimeError("موضوع آیتم در جدول موضوعات پیدا نشد.")
        messages = [
            x for x in detail.get("messages") or []
            if x.get("relation_type") != "duplicate"
        ]
        if not messages:
            raise RuntimeError("پیام شاهدی برای بازسازی خلاصه وجود ندارد.")
        for message in messages:
            message["statement_type"] = message.get("processed_statement_type") or detail.get("statement_type")
            message["source_quality"] = float(message.get("source_quality") or 0.5)
            message["importance_score"] = float(message.get("importance_score") or detail.get("importance_score") or 0)
            message["personal_channel"] = message.get("statement_location_type") == "personal_channel"
        templates = await self.db.list_bulletin_templates()
        template = next((x for x in templates if x.get("output_format") == "json_schema"), None) or (templates[0] if templates else {})
        person = PersonMatch(
            person_id=detail.get("person_id"), candidate_id=detail.get("person_candidate_id"),
            name=str(detail.get("person_name") or "نامشخص"), registry_bucket=str(detail.get("registry_bucket") or "outside"),
            method="editorial_reprocess", confidence=1.0,
        )
        topic_match = TopicMatch(
            topic_id=int(topic["topic_id"]), name=str(topic["name"]), score=1.0, is_primary=True,
        )
        log_id = await self.db.create_reprocess_log(
            run_id=int(detail["run_id"]), item_id=item_id, mode="summary", actor=actor,
        )
        try:
            summary_data, degraded, _ = await self.pipeline._summarize_cluster(
                run_id=int(detail["run_id"]), template=template, person=person, topic=topic_match,
                topic_keywords=list(topic.get("keywords") or []), messages=messages,
            )
            await self.db.update_bulletin_item_generated_summary(item_id, summary_data=summary_data, actor=actor)
            result = {"ok": True, "item_id": item_id, "summary": summary_data["summary"], "degraded": degraded}
            await self.db.complete_reprocess_log(log_id, status="completed", details=result)
            return result
        except Exception as exc:
            await self.db.complete_reprocess_log(log_id, status="failed", details={"error": str(exc)})
            raise

    async def reprocess_run(self, run_id: int, *, mode: str, actor: str) -> int:
        if mode not in {"all", "person", "topic", "dedup"}:
            raise RuntimeError("حالت بازپردازش نامعتبر است.")
        run = await self.db.get_bulletin_run(run_id)
        if not run:
            raise RuntimeError("اجرای بولتن پیدا نشد.")
        source_ids = _loads(run.get("source_ids_json"), [])
        statuses = _loads(run.get("statuses_json"), ["approved"])
        filters = _loads(run.get("filters_json"), {})
        filters["reprocess_mode"] = mode
        filters["source_run_id"] = run_id
        new_run_id = await self.create_run(
            requested_by=actor,
            template_id=run.get("template_id"),
            date_from=run.get("date_from"),
            date_to=run.get("date_to"),
            source_chat_ids=[int(x) for x in source_ids],
            statuses=[str(x) for x in statuses],
            filters=filters,
        )
        log_id = await self.db.create_reprocess_log(run_id=new_run_id, item_id=None, mode=mode, actor=actor, details={"source_run_id": run_id})
        try:
            await self.execute_run(new_run_id)
            await self.db.complete_reprocess_log(log_id, status="completed", details={"source_run_id": run_id, "new_run_id": new_run_id})
        except Exception as exc:
            await self.db.complete_reprocess_log(log_id, status="failed", details={"error": str(exc)})
            raise
        return new_run_id

    async def rebuild_editorial_output(self, run_id: int, *, actor: str) -> dict[str, Any]:
        run = await self.db.get_bulletin_run(run_id)
        if not run:
            raise RuntimeError("اجرای بولتن پیدا نشد.")
        items = await self.db.list_bulletin_items(run_id)
        if any(item.get("status") == "review_pending" for item in items):
            raise RuntimeError("پیش از بازتدوین سردبیری باید همه آیتم‌ها تأیید یا رد شوند.")
        approved = [item for item in items if item.get("status") == "approved"]
        if not approved:
            raise RuntimeError("هیچ آیتم تأییدشده‌ای برای بازتدوین وجود ندارد.")
        result = await self.editorial_rebuilder.rebuild(run_id)
        result["actor"] = actor
        return result

    def editorial_status(self, run_id: int) -> dict[str, Any]:
        package = load_editorial_package(self.settings, run_id)
        return {
            "exists": package is not None,
            "version": package.version if package else None,
            "generated_at": package.generated_at if package else None,
            "source_digest": package.source_digest if package else None,
            "people": len(package.people) if package else 0,
            "controversies": len(package.controversies) if package else 0,
        }

    async def export_run(self, run_id: int, *, actor: str = "export") -> dict[str, Any]:
        # تصمیم‌های صریح سردبیر را پیش از کنترل خروجی با وضعیت‌های AI همگام کن.
        # این عملیات فقط دیتابیس را اصلاح می‌کند و هیچ درخواست تازه‌ای به مدل نمی‌فرستد.
        await self.db.reconcile_editorial_ai_state(
            run_id,
            actor=actor,
            exclude_unlinked=False,
        )
        items = await self.db.list_bulletin_items(run_id)
        if not items:
            raise RuntimeError("برای این اجرا هیچ آیتم سردبیری وجود ندارد.")
        pending = [x for x in items if x.get("status") == "review_pending"]
        if pending:
            raise RuntimeError(f"هنوز {len(pending)} آیتم تعیین تکلیف نشده است. ابتدا همه آیتم‌ها را تأیید یا رد کنید.")
        approved = [x for x in items if x.get("status") == "approved"]
        if not approved:
            raise RuntimeError("هیچ آیتم تأییدشده‌ای برای تولید فایل نهایی وجود ندارد.")
        if self.settings.ai_require_validated_output and not self.settings.ai_allow_pending_export:
            pending_methods = {"pending_ai_extractive_draft", "ai_unvalidated_pending_review"}
            pending_items = [x for x in approved if str(x.get("summary_method") or "") in pending_methods]
            # شمارش پیام‌های معلق باید از وضعیت زنده دیتابیس انجام شود؛ گزارش پردازش
            # اولیه ممکن است پس از تصمیم‌های سردبیری قدیمی شده باشد.
            pending_message_rows = await self.db.list_pending_ai_messages(run_id)
            pending_messages = len(pending_message_rows)
            if pending_items or pending_messages:
                raise RuntimeError(
                    "خروجی نهایی مسدود شد: "
                    f"{len(pending_items)} آیتم و {pending_messages} پیام هنوز تأیید نهایی هوش را ندارند. "
                    "برای آیتم‌ها همان تأیید یا رد سردبیر کافی است و AI دوباره اجرا نمی‌شود. "
                    "پیام‌های باقیمانده را در بخش «پیام‌های معلق» به‌عنوان پوشش‌داده‌شده یا خارج از بولتن تعیین تکلیف کنید."
                )
        return await self.exporter.export_run(run_id)

    async def execute_schedule(self, schedule: dict[str, Any]) -> int:
        filters = _loads(schedule.get("filters_json"), {})
        run_id = await self.create_run(
            requested_by=f"schedule:{schedule['id']}",
            template_id=schedule.get("template_id"),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            source_chat_ids=filters.get("source_chat_ids") or None,
            statuses=filters.get("statuses") or ["approved"],
            filters=filters,
        )
        await self.execute_run(run_id)
        await self.db.mark_schedule_run(int(schedule["id"]))
        return run_id
