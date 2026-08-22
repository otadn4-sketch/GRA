from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.crawler_send_guard import CrawlerSendLedger, bale_api_method, origin_key
from app.near_duplicate import choose_representatives, is_near_duplicate, keyword_jaccard
from app.speaker_grounding import (
    contains_phrase,
    evidence_supports_name,
    grounding_span,
    is_title_only_name,
    may_bind_short_name,
)


class NearDuplicateTests(unittest.TestCase):
    def test_identical_news_is_duplicate(self) -> None:
        text = "وزیر نیرو گفت خاموشی در تهران کاهش یافته است."
        self.assertTrue(is_near_duplicate(text, text))

    def test_eighty_percent_overlap_is_duplicate(self) -> None:
        left = "وزیر نیرو گفت خاموشی گسترده در تهران و اصفهان کاهش یافته است امروز"
        right = "وزیر نیرو گفت خاموشی گسترده در تهران و اصفهان کاهش یافته است دیروز"
        self.assertGreaterEqual(keyword_jaccard(left, right), 0.80)
        self.assertTrue(is_near_duplicate(left, right, threshold=0.80))

    def test_separate_news_stay_separate(self) -> None:
        left = "مجلس طرح مالیات بر خانه‌های خالی را تصویب کرد."
        right = "تیم ملی فوتبال برابر ژاپن به پیروزی رسید."
        self.assertFalse(is_near_duplicate(left, right, threshold=0.80))

    def test_longest_message_is_representative(self) -> None:
        items = [
            {
                "id": 1,
                "text": "وزیر نیرو گفت خاموشی گسترده در تهران و اصفهان کاهش یافته است امروز",
                "published_at": "2026-01-02",
            },
            {
                "id": 2,
                "text": "وزیر نیرو گفت خاموشی گسترده در تهران و اصفهان کاهش یافته است امروز ادامه دارد",
                "published_at": "2026-01-03",
            },
        ]
        representatives, duplicate_of = choose_representatives(items, threshold=0.80)
        self.assertEqual(representatives, [2])
        self.assertEqual(duplicate_of, {1: 2})


class SpeakerGroundingTests(unittest.TestCase):
    def test_titles_are_not_names(self) -> None:
        self.assertTrue(is_title_only_name("وزیر نیرو"))
        self.assertTrue(is_title_only_name("رئیس جمهور"))
        self.assertFalse(is_title_only_name("مسعود پزشکیان"))

    def test_registry_bind_requires_name_in_text(self) -> None:
        text = "وزیر نیرو در نشست امروز از کاهش خاموشی سخن گفت."
        self.assertIsNone(
            grounding_span(
                text,
                extracted_name="عباس علی‌آبادی",
                full_name="عباس علی‌آبادی",
                aliases=["علی آبادی"],
            )
        )
        self.assertEqual(
            grounding_span(
                "عباس علی‌آبادی وزیر نیرو گفت خاموشی کم شده است.",
                extracted_name="عباس علی‌آبادی",
                full_name="عباس علی‌آبادی",
                aliases=["علی آبادی"],
            ),
            "عباس علی‌آبادی",
        )

    def test_evidence_must_be_in_text_and_contain_name(self) -> None:
        text = "قالیباف در مجلس گفت بودجه باید اصلاح شود."
        self.assertTrue(contains_phrase(text, "قالیباف"))
        self.assertTrue(evidence_supports_name(text, "قالیباف در مجلس گفت", "قالیباف"))
        self.assertFalse(evidence_supports_name(text, "پزشکیان در دولت گفت", "قالیباف"))
        self.assertFalse(evidence_supports_name(text, "عبارتی که در متن نیست قالیباف", "قالیباف"))

    def test_short_single_token_binds_only_on_exact_span(self) -> None:
        self.assertTrue(may_bind_short_name("قالیباف", "قالیباف"))
        self.assertFalse(may_bind_short_name("علی", "محمدباقر قالیباف"))
        self.assertTrue(may_bind_short_name("محمدباقر قالیباف", "قالیباف"))


class CrawlerSendGuardTests(unittest.TestCase):
    def test_bale_method_and_origin_key(self) -> None:
        self.assertEqual(
            bale_api_method("https://tapi.bale.ai/bot123:abc/copyMessage"),
            "copymessage",
        )
        self.assertEqual(
            origin_key({"from_chat_id": -100, "message_id": 77}),
            "origin:-100:77",
        )

    def test_skips_second_send_of_same_origin(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    text TEXT, caption TEXT, text_sha256 TEXT,
                    forwarded_origin_url TEXT, message_url TEXT,
                    forwarded_origin_chat_id INTEGER, forwarded_origin_message_id INTEGER,
                    published_at TEXT, received_at TEXT, created_at TEXT
                )
                """
            )
            conn.commit()
            conn.close()
            ledger = CrawlerSendLedger(str(path), threshold=0.80)
            payload = {"from_chat_id": 12, "message_id": 9, "chat_id": 1, "text": "خبر یک"}
            skip, reason = ledger.should_skip(method="copymessage", payload=payload)
            self.assertFalse(skip)
            ledger.record(method="copymessage", payload=payload)
            skip, reason = ledger.should_skip(method="copymessage", payload=payload)
            self.assertTrue(skip)
            self.assertEqual(reason, "origin_already_sent")

    def test_skips_near_duplicate_text(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    text TEXT, caption TEXT, text_sha256 TEXT,
                    forwarded_origin_url TEXT, message_url TEXT,
                    forwarded_origin_chat_id INTEGER, forwarded_origin_message_id INTEGER,
                    published_at TEXT, received_at TEXT, created_at TEXT
                )
                """
            )
            conn.commit()
            conn.close()
            ledger = CrawlerSendLedger(str(path), threshold=0.80)
            first = {"chat_id": 1, "text": "وزیر نیرو گفت خاموشی گسترده در تهران کاهش یافته است امروز"}
            ledger.record(method="sendmessage", payload=first)
            second = {"chat_id": 1, "text": "وزیر نیرو گفت خاموشی گسترده در تهران کاهش یافته است دیروز"}
            skip, reason = ledger.should_skip(method="sendmessage", payload=second)
            self.assertTrue(skip)
            self.assertEqual(reason, "near_duplicate_already_sent")


class MarkupRegressionTests(unittest.TestCase):
    def test_automation_page_is_gone_and_crawler_is_on_sources(self) -> None:
        html = Path("/workspace/web/index.html").read_text(encoding="utf-8")
        self.assertNotIn('id="page-automation"', html)
        self.assertNotIn('data-page="automation"', html)
        self.assertIn('id="page-sources"', html)
        self.assertIn('id="sourcesCrawler"', html)
        self.assertIn('id="crawlerChannelsForm"', html)
        self.assertIn('id="deskIdentityCard"', html)


if __name__ == "__main__":
    unittest.main()
