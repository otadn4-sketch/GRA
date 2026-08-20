from __future__ import annotations

import unittest
from io import BytesIO

from openpyxl import Workbook

from app.eitan_library import (
    build_eitan_insights,
    eitan_search_groups,
    parse_eitan_library_upload,
)
from app.html_layout_engine import _public_categories
from app.bulletin_models import (
    BulletinCategory,
    BulletinData,
    BulletinMeta,
    BulletinPerson,
    BulletinStatement,
)
from app.weekday_art import weekday_cover_png_bytes, weekday_cover_svg
from app.weekday_palette import weekday_palette_for_report
from app.word_rtl import apply_persian_document, rtl_table


def _xlsx(rows: list[list[object]], sheet: str = "کتابخانه") -> bytes:
    workbook = Workbook()
    ws = workbook.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _structured_library_xlsx() -> bytes:
    workbook = Workbook()
    keywords = workbook.active
    keywords.title = "Keyword_Library"
    keywords.append(
        [
            "keyword_id",
            "canonical_term",
            "aliases",
            "category",
            "subcategory",
            "priority",
            "helper_terms",
            "search_query",
        ]
    )
    keywords.append(
        [
            "k1",
            "تنگه هرمز",
            "هرمز|تنگه",
            "امنیت دریایی",
            "عبور کشتی",
            5,
            "نفتکش",
            '"بندرعباس"',
        ]
    )
    keywords.append(
        ["k2", "خاموشی", "قطع برق", "انرژی", "برق", 2, "", "خاموشی"]
    )
    people = workbook.create_sheet("Person_Library")
    people.append(
        [
            "person_id",
            "canonical_name",
            "aliases",
            "role",
            "institution",
            "view_cluster",
            "subcluster",
            "topic_keywords",
            "priority",
            "source_scope",
            "search_query",
        ]
    )
    people.append(
        [
            "p1",
            "عباس عراقچی",
            "عراقچی",
            "وزیر",
            "وزارت خارجه",
            "سیاست خارجی",
            "مذاکره",
            "برجام",
            4,
            "رسانه ملی",
            "عباس عراقچی",
        ]
    )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class EitanLibraryTests(unittest.TestCase):
    def test_keyword_and_person_library_sheets(self) -> None:
        library = parse_eitan_library_upload("library.xlsx", _structured_library_xlsx())
        self.assertEqual(library.sheets, ["Keyword_Library", "Person_Library"])
        self.assertIn("تنگه هرمز", library.keywords)
        self.assertIn("عباس عراقچی", library.people)
        self.assertEqual(library.keyword_records[0].category, "امنیت دریایی")
        self.assertEqual(library.person_records[0].institution, "وزارت خارجه")
        terms = [term for group in eitan_search_groups(library) for term in group]
        self.assertIn("تنگه هرمز", terms)
        self.assertIn("هرمز", terms)
        self.assertIn("بندرعباس", terms)
        self.assertIn("عباس عراقچی", terms)
        self.assertIn("عراقچی", terms)
        self.assertIn("برجام", terms)

    def test_library_insights_use_sheet_dimensions(self) -> None:
        library = parse_eitan_library_upload("library.xlsx", _structured_library_xlsx())
        insights = build_eitan_insights(
            library,
            [
                {
                    "day": "1405/05/26",
                    "source": "منبع الف",
                    "message_type": "text",
                    "haystack": "عبور از تنگه هرمز و موضع عباس عراقچی",
                },
                {
                    "day": "1405/05/27",
                    "source": "منبع ب",
                    "message_type": "photo",
                    "haystack": "خاموشی گسترده در چند استان",
                },
            ],
        )
        self.assertGreaterEqual(insights["matched_count"], 2)
        self.assertTrue(insights["keywords"])
        self.assertTrue(insights["people"])
        self.assertTrue(insights["categories"])
        self.assertTrue(insights["heatmap"]["matrix"])
        self.assertTrue(insights["keyword_flow"])
        self.assertTrue(insights["trend"]["series"])

    def test_single_column_stays_or_search(self) -> None:
        payload = _xlsx([["کلیدواژه"], ["هرمز"], ["نفتکش"]])
        library = parse_eitan_library_upload("keywords.xlsx", payload)
        self.assertFalse(library.paired)
        self.assertEqual(eitan_search_groups(library), [["هرمز"], ["نفتکش"]])


class LayoutOrderTests(unittest.TestCase):
    def test_events_come_last(self) -> None:
        meta = BulletinMeta(
            issue_number=1,
            report_mode="concise",
            report_date_jalali="1405/05/26",
            generated_at="2026-08-17T00:00:00+00:00",
        )
        statement = BulletinStatement(
            statement_id="s1",
            topic="آزمایش",
            statement_mode="media_report",
            summary_short="کوتاه",
            summary_detailed="بلند",
            importance_score=0.5,
            include_in_main=True,
        )
        person = BulletinPerson(name_canonical="شخص", is_registry_person=True, statements=[statement])
        data = BulletinData(
            meta=meta,
            categories=[
                BulletinCategory(category_id="events", title="وقایع و رویدادهای مهم ایران و جهان", order=1, section_letter="الف", people=[person]),
                BulletinCategory(category_id="government", title="مسئولان دولت", order=2, section_letter="ب", people=[person]),
            ],
        )
        keys = [category.category_id for category, _people in _public_categories(data)]
        self.assertEqual(keys[-1], "events")
        self.assertEqual(keys[0], "government")


class WeekdayArtTests(unittest.TestCase):
    def test_svg_and_png_for_palette(self) -> None:
        palette = weekday_palette_for_report("1405/05/26")
        svg = weekday_cover_svg(palette)
        self.assertIn(str(palette.get("name") or ""), svg)
        png = weekday_cover_png_bytes(palette)
        self.assertGreater(len(png.getvalue()), 100)


class WordRtlTests(unittest.TestCase):
    def test_document_language_is_persian(self) -> None:
        from docx import Document
        from docx.oxml.ns import qn

        doc = Document()
        apply_persian_document(doc)
        table = doc.add_table(rows=1, cols=1)
        rtl_table(table)
        settings = doc.settings.element.find(qn("w:themeFontLang"))
        self.assertIsNotNone(settings)
        self.assertEqual(settings.get(qn("w:bidi")), "fa-IR")
        self.assertIsNotNone(table._tbl.tblPr.find(qn("w:bidiVisual")))


if __name__ == "__main__":
    unittest.main()
