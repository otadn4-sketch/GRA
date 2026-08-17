from __future__ import annotations

"""HTML-first page-layout engine for the Garaye bulletin.

The generated HTML is the canonical layout artifact.  It is self-contained,
paginated into fixed A3 sheets in the browser, and printed directly to PDF.
The editable Word file is intentionally produced by a separate secondary
renderer from the same :class:`BulletinData` object.
"""

import asyncio
import base64
import html
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .bulletin_models import BulletinData, BulletinPerson, BulletinStatement
from .bulletin_validation import is_generic_url, valid_public_url
from .config import Settings
from .persian_text import to_persian_digits
from .weekday_palette import weekday_palette_for_report


_LAYOUT_VERSION = "garaye-minimal-a3-v7-weekday-palette"
_A3_WIDTH_PT = 841.89
_A3_HEIGHT_PT = 1190.55


@dataclass(frozen=True)
class HtmlLayoutExport:
    files: dict[str, str]
    warnings: list[str]
    audit: dict[str, Any]


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _display(value: Any) -> str:
    """Escape a visible publication value and render every numeral in Persian."""

    return html.escape(to_persian_digits(value), quote=True)


def _data_uri(path_value: str | Path | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _editorial_qr_data_uri(statement: BulletinStatement) -> str | None:
    """Return a QR only for a complete editorial-desk link decision.

    A QR generated from a raw message link is not a publication artifact.  The
    final desk link and its existing QR file must both be present, which keeps
    output rebuilds faithful to the editor's explicit choice.
    """

    if not statement.show_qr_in_bulletin:
        return None
    if not valid_public_url(statement.editorial_source_url) or is_generic_url(statement.editorial_source_url):
        return None
    return _data_uri(statement.qr_code_path)


def _placeholder_portrait(name: str) -> str:
    parts = [part for part in str(name or "").split() if part]
    initials = "".join(part[:1] for part in parts[:2]) or "؟"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="520" height="620" viewBox="0 0 520 620">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f0f7f7"/><stop offset="1" stop-color="#c9dddd"/></linearGradient></defs>
<rect width="520" height="620" fill="url(#g)"/><circle cx="260" cy="205" r="112" fill="#7fa6a6"/>
<path d="M78 590c15-142 91-225 182-225s167 83 182 225" fill="#527d7d"/>
<text x="260" y="230" text-anchor="middle" font-family="Tahoma,Arial" font-size="84" fill="#fff">{html.escape(initials)}</text>
</svg>"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _font_face(project_root: Path) -> tuple[str, bool]:
    regular_candidates = (
        project_root / "web" / "assets" / "fonts" / "IRZar.ttf",
        project_root / "web" / "assets" / "fonts" / "IRZar.otf",
    )
    bold_candidates = (
        project_root / "web" / "assets" / "fonts" / "IRZAR-BOLD.ttf",
        project_root / "web" / "assets" / "fonts" / "IRZAR-BOLD.otf",
        project_root / "web" / "assets" / "fonts" / "IRZar-Bold.ttf",
        project_root / "web" / "assets" / "fonts" / "IRZar-Bold.otf",
    )
    rules: list[str] = []
    embedded_regular = False
    for path in regular_candidates:
        uri = _data_uri(path)
        if uri:
            fmt = "opentype" if path.suffix.lower() == ".otf" else "truetype"
            rules.append(
                f'@font-face{{font-family:"IRZarEmbedded";src:url("{uri}") format("{fmt}");'
                "font-weight:400;font-style:normal;font-display:block;}"
            )
            embedded_regular = True
            break
    for path in bold_candidates:
        uri = _data_uri(path)
        if uri:
            fmt = "opentype" if path.suffix.lower() == ".otf" else "truetype"
            rules.append(
                f'@font-face{{font-family:"IRZarBoldEmbedded";src:url("{uri}") format("{fmt}");'
                "font-weight:700;font-style:normal;font-display:block;}"
            )
            break
    return "\n".join(rules), embedded_regular


def _category_color(value: str) -> str:
    clean = str(value or "").replace("ي", "ی").replace("ك", "ک").strip().lower()
    rules = (
        ("#A83F4A", ("سیاسی", "سیاست", "مجلس", "دولت", "دیپلماسی", "بین‌الملل", "امنیت", "نظامی", "دفاع")),
        ("#347B61", ("فرهنگ", "هنر", "رسانه", "دین", "مذهبی")),
        ("#2877A8", ("اقتصاد", "بانک", "بازار", "تولید", "مالی", "انرژی", "نفت", "گاز", "برق", "پالایشگاه")),
        ("#8D624A", ("اجتماع", "رفاه", "آموزش", "شهری", "سلامت", "درمان", "بهداشت", "محیط زیست", "اقلیم")),
        ("#BC8A22", ("علم", "دانشگاه", "پژوهش", "فضایی", "فناوری", "اینترنت", "دیجیتال")),
        ("#5C5A92", ("ورزش",)),
    )
    for color, words in rules:
        if any(word in clean for word in words):
            return color
    return "#5C5A92"


def _public_categories(data: BulletinData):
    for category in data.categories:
        people: list[tuple[BulletinPerson, list[BulletinStatement]]] = []
        for person in category.people:
            statements = [item for item in person.statements if item.include_in_main]
            if statements:
                people.append((person, statements))
        if people:
            yield category, people


def _statement_card(
    person: BulletinPerson,
    statement: BulletinStatement,
    *,
    sequence_index: int,
    section_key: str,
    section_title: str,
) -> str:
    portrait = _data_uri(person.portrait_path) or _placeholder_portrait(person.name_canonical)
    qr = _editorial_qr_data_uri(statement)
    # The page title retains the established registry grouping, while each
    # news unit communicates only the published editorial fields.  This is
    # intentionally quieter than a dashboard card: colour is reserved for a
    # small topic tag and a hairline on the leading edge.
    general_topic = statement.topic or "موضوع نامشخص"
    main_subject = statement.main_subject or statement.headline or general_topic
    color = _category_color(general_topic)
    one_line = statement.summary_lead or statement.summary_short or statement.headline
    paragraph = statement.summary_body or statement.summary_detailed or statement.summary_short
    location = statement.editorial_context_label or statement.statement_mode or "محل بیان نامشخص"
    headline = statement.headline or one_line
    qr_html = (
        f'<img class="card-qr" src="{qr}" alt="کد QR منبع خبر">'
        if qr
        else ''
    )
    position = person.position or "سمت نامشخص"
    identity_html = f"""
  <div class="identity-row">
    <div class="identity-box"><strong>{_display(person.name_canonical)}</strong><span>{_display(position)}</span></div>
    <img class="portrait" src="{portrait}" alt="تصویر {_display(person.name_canonical)}">
  </div>"""
    # Identity (photo/name/position) must sit above topic and the directional
    # main-subject phrase so every HTML/PDF card opens with the speaker block.
    return f"""
<article class="news-card" data-statement-id="{_esc(statement.statement_id)}" data-layout-sequence="{sequence_index + 1}" data-category-id="{_esc(section_key)}" data-category-title="{_esc(section_title)}" style="--category-color:{color}">
  {identity_html}
  <span class="category-label">{_display(general_topic)}</span>
  <h2 class="news-headline">{_display(main_subject)}</h2>
  <div class="message">
    <p class="one-line"><strong>{_display(one_line)}</strong></p>
    <p class="statement-location">{_display(location)}</p>
    <div class="message-body">
      <p>{_display(paragraph)}</p>
      {qr_html}
    </div>
  </div>
</article>"""


def _cover_sheet(data: BulletinData) -> str:
    return f"""
<section class="sheet cover-sheet" data-page-kind="cover" aria-label="جلد">
  <div class="cover-grid-mark" aria-hidden="true"></div>
  <div class="cover-kicker">بولتن تحلیلی گرایه</div>
  <div class="cover-title">خبرنامه<br>گرایه</div>
  <div class="cover-copy">رصد، تحلیل و صورت‌بندی هوشمند جریان خبر</div>
  <div class="cover-meta"><span>شماره {_display(data.meta.issue_number)}</span><i></i><span>{_display(data.meta.report_date_jalali)}</span></div>
</section>"""


def _footer(data: BulletinData) -> str:
    return f"""
<footer class="page-footer">
  <span>خبرنامه گرایه</span>
  <b class="page-number"></b>
  <span>شماره {_display(data.meta.issue_number)} · {_display(data.meta.report_date_jalali)}</span>
</footer>"""


def _fixed_sheet(
    data: BulletinData,
    *,
    kind: str,
    body: str,
    toc_key: str = "",
    classes: str = "",
) -> str:
    toc_attr = f' data-toc-key="{_esc(toc_key)}"' if toc_key else ""
    return (
        f'<section class="sheet {classes}" data-page-kind="{_esc(kind)}"{toc_attr}>'
        f'<div class="page-frame">{body}</div>{_footer(data)}</section>'
    )


def _toc_sheet(
    data: BulletinData,
    categories: list[tuple[Any, Any]],
    category_page_numbers: dict[str, int] | None = None,
) -> str:
    category_page_numbers = category_page_numbers or {}
    introduction_row = (
        '<li data-toc-ref="introduction"><span>مقدمه</span><i class="toc-tab-leader"></i><b class="toc-page"></b></li>'
        if data.meta.introduction.strip()
        else ""
    )
    controversy_row = (
        '<li data-toc-ref="high-attention"><span>پربازتاب</span><i class="toc-tab-leader"></i><b class="toc-page"></b></li>'
        if data.controversies
        else ""
    )
    category_rows = "".join(
        f"""<li class="toc-group" data-toc-ref="category-{_esc(category.category_id)}">
          <span>{_display(category.section_letter)}) {_display(category.title)}</span><i class="toc-tab-leader"></i><b class="toc-page">{_display(category_page_numbers.get(category.category_id, ""))}</b>
          <ol>{"".join(f'<li><span>{_display(index)}) {_display(person.name_canonical)}</span></li>' for index, (person, _) in enumerate(people, 1))}</ol>
        </li>"""
        for category, people in categories
    )
    body = f"""
<div class="toc-heading">فهرست</div>
<ol class="toc-list">
  {introduction_row}
  {controversy_row}
  {category_rows}
</ol>
"""
    return _fixed_sheet(data, kind="toc", body=body, classes="toc-sheet")


def _introduction_sheet(data: BulletinData, categories: list[tuple[Any, Any]]) -> str:
    paragraphs = [
        line.strip() for line in str(data.meta.introduction or "").splitlines()
        if line.strip()
    ]
    if not paragraphs:
        raise ValueError("صفحه مقدمه فقط با متن واردشده در سامانه ساخته می‌شود.")
    body = f"""
<div class="feature-heading"><span>مقدمه</span></div>
<div class="intro-copy">
  {''.join(f'<p>{_display(paragraph)}</p>' for paragraph in paragraphs)}
</div>"""
    return _fixed_sheet(
        data,
        kind="introduction",
        body=body,
        toc_key="introduction",
        classes="introduction-sheet",
    )


def _high_attention_sheet(data: BulletinData) -> str:
    cards = "".join(
        f"""<article class="attention-item"><h2>{_display(index)}. {_display(item.title)}</h2><p>{_display(item.summary)}</p></article>"""
        for index, item in enumerate(data.controversies, 1)
    )
    body = f'<div class="feature-heading"><span>اخبار پربازتاب</span></div><div class="attention-list">{cards}</div>'
    return _fixed_sheet(
        data,
        kind="high-attention",
        body=body,
        toc_key="high-attention",
        classes="attention-sheet",
    )


def _styles(font_face: str, palette: dict[str, Any]) -> str:
    ink = palette.get("ink", "#20252B")
    identity = palette.get("main", "#B85C4A")
    dark = palette.get("dark", "#783B31")
    accent = palette.get("accent", "#D58A79")
    light = palette.get("light", "#EBC4BA")
    paper = palette.get("ground", "#FAF3F1")
    muted = palette.get("muted", "#5F6872")
    line = palette.get("line", "#D8DDE2")
    return f"""
{font_face}
@page{{size:A3 portrait;margin:0}}
:root{{--ink:{ink};--identity:{identity};--identity-dark:{dark};--identity-accent:{accent};--identity-light:{light};--paper:{paper};--white:#fff;--muted:{muted};--line:{line};--fine-line:rgba(32,37,43,.42)}}
*{{box-sizing:border-box}}
html{{direction:rtl;background:{paper}}}
body{{margin:0;color:var(--ink);background:{paper};font-family:"IRZarEmbedded",IRZar,"B Zar",Tahoma,Arial,sans-serif;font-size:12pt;line-height:1.68;direction:rtl;text-align:right;unicode-bidi:plaintext;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
html,body,body *{{direction:rtl;unicode-bidi:plaintext}}
#publication{{display:flex;flex-direction:column;align-items:center;gap:12mm;padding:12mm 0}}
.sheet{{position:relative;width:297mm;height:420mm;background:var(--paper);overflow:hidden;flex:none;box-shadow:0 4mm 13mm rgba(32,37,43,.17)}}
.page-frame{{position:absolute;inset:21mm 20mm 22mm;overflow:hidden}}
.page-footer{{position:absolute;right:20mm;left:20mm;bottom:8mm;height:7mm;display:grid;grid-template-columns:1fr 1fr 1fr;align-items:center;border-top:.45pt solid var(--fine-line);color:var(--muted);font-size:8.8pt;letter-spacing:.02em}}
.page-footer span:last-child{{text-align:left}}.page-footer b{{text-align:center;color:var(--ink);font-size:10.5pt}}
.cover-sheet{{background:var(--identity-dark);color:#fff;padding:34mm 28mm;display:flex;flex-direction:column;justify-content:center;isolation:isolate}}
.cover-sheet::before{{content:"";position:absolute;inset:0;background:radial-gradient(circle at 78% 14%,color-mix(in srgb,var(--identity) 45%,transparent),transparent 26%),linear-gradient(125deg,color-mix(in srgb,var(--identity) 22%,transparent),transparent 48%);z-index:-1}}
.cover-grid-mark{{position:absolute;left:23mm;bottom:27mm;width:74mm;height:74mm;border:1px solid color-mix(in srgb,var(--identity-light) 80%,#fff);border-radius:50%;background:repeating-linear-gradient(90deg,transparent 0 10mm,color-mix(in srgb,var(--identity-light) 55%,transparent) 10.4mm 10.8mm),repeating-linear-gradient(0deg,transparent 0 10mm,color-mix(in srgb,var(--identity-light) 40%,transparent) 10.4mm 10.8mm);transform:rotate(19deg)}}
.cover-kicker{{color:var(--identity-light);font-size:12pt;font-weight:700;letter-spacing:.08em}}.cover-title{{margin-top:11mm;font-size:54pt;line-height:1.25;font-weight:900;letter-spacing:-.02em}}.cover-copy{{margin-top:8mm;color:#f7f1ef;font-size:15pt}}.cover-meta{{display:flex;align-items:center;gap:7mm;margin-top:22mm;color:#f3e6e2;font-size:13pt}}.cover-meta i{{display:block;width:1px;height:7mm;background:var(--identity)}}
.feature-heading{{position:relative;margin:0 0 11mm;padding:0 0 5mm;border-bottom:.55pt solid var(--fine-line);text-align:center}}
.feature-heading::after{{content:"";position:absolute;right:50%;bottom:-2.5mm;width:5mm;height:5mm;background:var(--identity);transform:translateX(50%) rotate(45deg)}}
.feature-heading span{{color:var(--ink);font-size:28pt;font-weight:900;line-height:1}}
.toc-heading{{margin:0 0 10mm;padding:0 0 5mm;border-bottom:.55pt solid var(--fine-line);font-size:28pt;font-weight:900;text-align:center}}
.toc-list{{list-style:none;margin:0;padding:0;font-size:14pt}}.toc-list>li{{display:grid;grid-template-columns:auto 1fr 14mm;align-items:end;gap:3mm;padding:2.8mm 0;border-bottom:.35pt solid rgba(32,37,43,.2)}}
.toc-list .toc-tab-leader{{border-bottom:1px dotted var(--muted);transform:translateY(-2mm)}}.toc-list b{{text-align:left;color:var(--ink);direction:ltr;unicode-bidi:isolate}}
.toc-list .toc-group{{margin-top:2mm;font-weight:800}}.toc-list .toc-group ol{{grid-column:1/4;list-style:none;margin:2mm 7mm 0 0;padding:0;font-weight:400;font-size:12.2pt}}.toc-list .toc-group ol li{{margin:1.3mm 0}}
.intro-copy{{font-size:16pt;line-height:2;text-align:justify}}.intro-copy p{{margin:0 0 7mm}}.attention-list{{display:block}}.attention-item{{padding:0 0 6mm;margin:0 0 6mm;border-bottom:.45pt solid var(--fine-line)}}.attention-item h2{{font-size:17pt;margin:0 0 2mm}}.attention-item p{{font-size:13.5pt;text-align:justify;line-height:1.85;margin:0}}
.section-heading{{position:absolute;top:23mm;right:20mm;left:20mm;height:21mm;display:flex;justify-content:center;align-items:center;border-bottom:.55pt solid var(--fine-line);font-size:27pt;font-weight:900;z-index:3;white-space:nowrap}}
.section-heading::after{{content:"";position:absolute;right:50%;bottom:-2.8mm;width:5mm;height:5mm;background:var(--identity);transform:translateX(50%) rotate(45deg)}}.section-rule{{display:none}}
.content-frame{{position:absolute;top:57mm;right:20mm;left:20mm;bottom:22mm;display:grid;grid-template-columns:1fr 1fr;gap:12mm;direction:rtl;overflow:hidden}}
.content-frame::after{{content:"";position:absolute;top:0;bottom:0;right:50%;border-right:.35pt solid rgba(32,37,43,.35);pointer-events:none}}.content-column{{height:100%;overflow:visible;display:flex;flex-direction:column;gap:5mm;min-width:0}}
.content-sheet.wide .content-frame{{grid-template-columns:1fr}}.content-sheet.wide .content-frame::after{{display:none}}.content-sheet.wide .content-column:nth-child(2){{display:none}}
.news-card{{position:relative;direction:rtl;background:transparent;flex:none;break-inside:avoid;border-inline-start:3.2px solid var(--category-color);padding-inline-start:4mm;padding-block:0 3.3mm}}
.category-label{{display:inline-flex;align-items:center;min-height:5.6mm;padding:0 2.2mm;background:var(--category-color);color:#fff;font-size:9.4pt;font-weight:800;line-height:1.12;white-space:nowrap}}
.news-headline{{margin:2.6mm 0 2.8mm;color:var(--ink);font-size:16.5pt;line-height:1.45;font-weight:900}}
.identity-row{{display:flex;align-items:center;gap:2.5mm;min-height:17mm;margin:0 0 2.8mm}}.portrait{{order:-1;width:17mm;height:17mm;object-fit:cover;border-radius:50%;border:.4pt solid rgba(32,37,43,.45);filter:grayscale(1);display:block;background:var(--identity-light)}}.identity-box{{display:flex;flex-direction:column;justify-content:center;gap:.35mm;min-height:17mm}}.identity-box strong{{font-size:11.8pt;font-weight:900}}.identity-box span{{color:var(--muted);font-size:10.1pt;line-height:1.35}}
.news-card>.category-label{{margin-top:0}}.news-card>.news-headline{{margin-top:2.2mm}}
.message{{position:relative;padding:0 0 2.8mm;border-bottom:.45pt solid var(--fine-line)}}.message p{{margin:0;text-align:justify}}.one-line,.one-line strong{{font-family:"IRZarBoldEmbedded","IRZAR-BOLD","IRZarEmbedded",IRZar,"B Zar",Tahoma,Arial,sans-serif;font-weight:700;font-size:11.9pt;line-height:1.55}}.statement-location{{display:inline-block;margin:2.2mm 0!important;border-bottom:.65pt solid var(--ink);font-size:10.3pt;font-weight:700;line-height:1.45}}.message-body{{display:flex;flex-direction:column;gap:1.4mm;align-items:stretch}}.message-body p{{font-size:10.6pt;line-height:1.72}}.card-qr{{width:12mm;height:12mm;object-fit:contain;align-self:flex-end;margin-top:.5mm;image-rendering:auto}}.card-qr-empty{{display:none}}
.news-card.compact{{padding-inline-start:3.5mm;padding-block-end:2.5mm}}.news-card.compact .category-label{{min-height:5mm;font-size:8.9pt}}.news-card.compact .news-headline{{font-size:15.2pt;margin:2mm 0 2.2mm;line-height:1.34}}.news-card.compact .portrait{{width:15mm;height:15mm}}.news-card.compact .identity-row{{min-height:15mm;margin-bottom:2mm}}.news-card.compact .identity-box{{min-height:15mm}}.news-card.compact .identity-box strong{{font-size:10.9pt}}.news-card.compact .identity-box span{{font-size:9.5pt}}.news-card.compact .one-line{{font-size:10.9pt;line-height:1.45}}.news-card.compact .statement-location{{margin:1.8mm 0!important;font-size:9.6pt}}.news-card.compact .message{{padding-bottom:2.2mm}}.news-card.compact .message-body p{{font-size:9.9pt;line-height:1.58}}.news-card.compact .card-qr{{width:10.5mm;height:10.5mm}}
.news-card.dense{{padding-inline-start:3mm;padding-block-end:2mm}}.news-card.dense .category-label{{min-height:4.6mm;font-size:8.4pt}}.news-card.dense .news-headline{{font-size:14.2pt;margin:1.7mm 0;line-height:1.25}}.news-card.dense .portrait{{width:13.5mm;height:13.5mm}}.news-card.dense .identity-row{{min-height:13.5mm;margin-bottom:1.6mm}}.news-card.dense .identity-box{{min-height:13.5mm}}.news-card.dense .identity-box strong{{font-size:10.1pt}}.news-card.dense .identity-box span{{font-size:8.9pt}}.news-card.dense .one-line{{font-size:10.1pt;line-height:1.35}}.news-card.dense .statement-location{{margin:1.4mm 0!important;font-size:9pt}}.news-card.dense .message{{padding-bottom:1.8mm}}.news-card.dense .message-body p{{font-size:9.1pt;line-height:1.46}}.news-card.dense .card-qr{{width:9.5mm;height:9.5mm}}
.layout-source{{display:none!important}}
.layout-error{{outline:2mm solid #c60000!important}}
@media print{{html,body{{background:#fff}}#publication{{display:block;padding:0}}.sheet{{box-shadow:none;break-after:page;page-break-after:always;margin:0}}.sheet:last-child{{break-after:auto;page-break-after:auto}}}}
"""


def _pagination_script() -> str:
    return r"""
(function(){
  const publication = document.getElementById("publication");
  const source = document.getElementById("layout-source");
  const footerTemplate = document.getElementById("footer-template").innerHTML;
  const audit = {engine:"garaye-measured-a3-flow-v1", pagination:"measured-browser", pages:0, contentPages:0, cards:0, overflowCards:[], missingImages:[], fontReady:false, columns:[]};
  const toPersianDigits = (value) => String(value).replace(/[0-9٠-٩]/g, (digit) => "۰۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹"["0123456789٠١٢٣٤٥٦٧٨٩".indexOf(digit)] || digit);
  const safeHtml = (value) => String(value || "").replace(/[&<>\"']/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[character]);

  function contentPage(categoryId, categoryTitle, wide=false) {
    const page = document.createElement("section");
    page.className = "sheet content-sheet" + (wide ? " wide" : "");
    page.dataset.pageKind = "content";
    page.dataset.categoryId = categoryId;
    page.innerHTML =
      '<div class="section-heading">' + safeHtml(categoryTitle) + '</div>' +
      '<div class="section-rule"></div>' +
      '<div class="content-frame"><div class="content-column"></div><div class="content-column"></div></div>' +
      footerTemplate;
    publication.appendChild(page);
    return page;
  }
  function overflows(column) {
    return column.scrollHeight > column.clientHeight + 2;
  }
  function tryPlace(column, card, density) {
    card.classList.remove("compact", "dense", "layout-error");
    if (density) card.classList.add(density);
    column.appendChild(card);
    if (!overflows(column)) return true;
    column.removeChild(card);
    return false;
  }
  function columnFor(state) {
    return state.page.querySelectorAll(".content-column")[state.column];
  }
  function startPage(state, categoryId, categoryTitle, wide=false) {
    state.page = contentPage(categoryId, categoryTitle, wide);
    state.column = 0;
  }
  function placeCard(card, categoryId, categoryTitle, state) {
    let column = columnFor(state);
    if (tryPlace(column, card, "") || tryPlace(column, card, "compact")) return;

    if (state.column === 0) {
      state.column = 1;
      column = columnFor(state);
      if (tryPlace(column, card, "") || tryPlace(column, card, "compact")) return;
    }

    startPage(state, categoryId, categoryTitle);
    column = columnFor(state);
    if (tryPlace(column, card, "") || tryPlace(column, card, "compact")) return;

    // A very long, yet still atomic, news card gets a single wide page before
    // being marked as an overflow. Ordinary cards never reserve a fixed height.
    startPage(state, categoryId, categoryTitle, true);
    column = columnFor(state);
    if (tryPlace(column, card, "dense")) {
      startPage(state, categoryId, categoryTitle);
      return;
    }
    card.classList.add("dense", "layout-error");
    column.appendChild(card);
    audit.overflowCards.push(card.dataset.statementId || "unknown");
    startPage(state, categoryId, categoryTitle);
  }
  async function paginate() {
    try {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      audit.fontReady = !document.fonts || document.fonts.status === "loaded";
      await Promise.all([...document.images].map(img => img.complete ? Promise.resolve() : new Promise(resolve => {
        img.addEventListener("load", resolve, {once:true});
        img.addEventListener("error", () => { audit.missingImages.push(img.alt || "image"); resolve(); }, {once:true});
      })));
      const stream = source.querySelector(".layout-card-stream");
      let state = null;
      const seenTocCategories = new Set();
      for (const original of stream.querySelectorAll(".news-card")) {
        const card = original.cloneNode(true);
        const categoryId = card.dataset.categoryId || "other";
        const categoryTitle = card.dataset.categoryTitle || "سایر اخبار";
        if (!state) {
          state = {page:contentPage(categoryId, categoryTitle), column:0};
        } else if (state.page.dataset.categoryId !== categoryId) {
          // Each registry/event section must start on its own page so no card
          // is dropped under the previous section heading.
          startPage(state, categoryId, categoryTitle);
        }
        if (!seenTocCategories.has(categoryId)) {
          card.dataset.tocKey = "category-" + categoryId;
          seenTocCategories.add(categoryId);
        }
        audit.cards += 1;
        placeCard(card, categoryId, categoryTitle, state);
      }
      for (const page of publication.querySelectorAll(".content-sheet")) {
        const columns = page.querySelectorAll(".content-column");
        if ([...columns].every(col => col.children.length === 0)) page.remove();
      }
      const sheets = [...publication.querySelectorAll(".sheet")];
      sheets.forEach((sheet, index) => {
        sheet.dataset.pageNumber = String(index + 1);
        const number = sheet.querySelector(".page-number");
        if (number) number.textContent = toPersianDigits(index + 1);
      });
      for (const row of document.querySelectorAll("[data-toc-ref]")) {
        const target = publication.querySelector(`[data-toc-key="${CSS.escape(row.dataset.tocRef)}"]`);
        const value = row.querySelector(".toc-page");
        const sheet = target && (target.closest(".sheet") || target);
        if (sheet && value) value.textContent = toPersianDigits(sheet.dataset.pageNumber);
      }
      audit.pages = sheets.length;
      audit.contentPages = publication.querySelectorAll(".content-sheet").length;
      audit.columns = [...publication.querySelectorAll(".content-sheet")].flatMap((page, pageIndex) =>
        [...page.querySelectorAll(".content-column")].map((column, columnIndex) => ({
          page: pageIndex + 1,
          column: columnIndex === 0 ? "right" : "left",
          cards: column.children.length,
          fill_percent: Math.round(Math.min(100, (column.scrollHeight / Math.max(1, column.clientHeight)) * 100) * 10) / 10
        }))
      );
    } catch (error) {
      audit.error = String(error && error.stack || error);
    } finally {
      window.__GARAYE_LAYOUT_AUDIT__ = audit;
      window.__GARAYE_LAYOUT_READY__ = true;
      document.documentElement.dataset.layoutReady = "true";
    }
  }
  paginate();
})();
"""


def _ordered_public_cards(
    data: BulletinData,
) -> tuple[
    list[tuple[Any, BulletinPerson, BulletinStatement]],
    dict[str, tuple[Any, BulletinPerson, BulletinStatement]],
]:
    """Return publishable cards in editorial order, never person-sort order."""

    flattened: list[tuple[Any, BulletinPerson, BulletinStatement]] = []
    by_statement_id: dict[str, tuple[Any, BulletinPerson, BulletinStatement]] = {}
    for category, people in _public_categories(data):
        for person, statements in people:
            for statement in statements:
                entry = (category, person, statement)
                flattened.append(entry)
                by_statement_id[statement.statement_id] = entry

    ordered: list[tuple[Any, BulletinPerson, BulletinStatement]] = []
    used: set[str] = set()
    for statement_id in data.publication_order:
        entry = by_statement_id.get(statement_id)
        if entry is not None:
            ordered.append(entry)
            used.add(statement_id)
    # Older runs do not have publication_order.  Their canonical JSON order is
    # retained as a backwards-compatible fallback, and omitted IDs are never
    # silently discarded.
    ordered.extend(entry for entry in flattened if entry[2].statement_id not in used)
    return ordered, by_statement_id


def _numbered_sheet(sheet: str, page_number: int) -> str:
    return sheet.replace(
        "<section ",
        f'<section data-page-number="{page_number}" ',
        1,
    ).replace(
        '<b class="page-number"></b>',
        f'<b class="page-number">{to_persian_digits(page_number)}</b>',
        1,
    )


def render_html(data: BulletinData, project_root: Path) -> tuple[str, dict[str, Any]]:
    font_face, font_embedded = _font_face(project_root)
    palette = weekday_palette_for_report(data.meta.report_date_jalali)
    categories = list(_public_categories(data))
    ordered_cards, _ = _ordered_public_cards(data)
    if not ordered_cards:
        raise RuntimeError("برای صفحه‌آرایی بولتن، خبر نهایی قابل انتشار وجود ندارد.")

    fixed_pages = [
        _cover_sheet(data),
        # Content-page numbers are measured in the browser after embedded
        # font and image loading, so the table of contents begins blank here.
        _toc_sheet(data, categories),
    ]
    if data.meta.introduction.strip():
        fixed_pages.append(_introduction_sheet(data, categories))
    if data.controversies:
        fixed_pages.append(_high_attention_sheet(data))

    numbered_fixed = [
        _numbered_sheet(sheet, index)
        for index, sheet in enumerate(fixed_pages, start=1)
    ]
    source_cards = "".join(
        _statement_card(
            person,
            statement,
            sequence_index=sequence_index,
            section_key=category.category_id,
            section_title=category.title,
        )
        for sequence_index, (category, person, statement) in enumerate(ordered_cards)
    )

    title = f"خبرنامه گرایه، شماره {to_persian_digits(data.meta.issue_number)}"
    document = f"""<!doctype html>
<html lang="fa" dir="rtl" data-layout-engine="html-first" data-weekday="{_esc(palette.get('name'))}" data-weekday-label="{_esc(palette.get('label'))}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="generator" content="{_LAYOUT_VERSION}">
  <title>{_display(title)}</title>
  <style>{_styles(font_face, palette)}</style>
</head>
<body dir="rtl">
  <main id="publication">{"".join(numbered_fixed)}</main>
  <template id="footer-template">{_footer(data)}</template>
  <aside id="layout-source" class="layout-source" aria-hidden="true"><div class="layout-card-stream">{source_cards}</div></aside>
  <script>{_pagination_script()}</script>
</body>
</html>"""
    return document, {
        "engine": _LAYOUT_VERSION,
        "canonical_format": "html",
        "page_size": "A3",
        "font_embedded": font_embedded,
        "category_count": len(categories),
        "card_count": len(ordered_cards),
        "pagination": "measured-browser-after-font-and-image-load",
        "density_profiles": ["standard", "compact", "dense"],
        "weekday_palette": {
            "day": palette.get("name"),
            "label": palette.get("label"),
            "main": palette.get("main"),
            "dark": palette.get("dark"),
            "ground": palette.get("ground"),
        },
    }


def _browser_candidates(configured: str) -> list[str]:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    for command in ("msedge", "chrome", "chromium", "chromium-browser", "google-chrome"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(resolved)
    if os.name == "nt":
        for root in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not root:
                continue
            candidates.extend(
                (
                    str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                    str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                )
            )
    unique: list[str] = []
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and str(path) not in unique:
            unique.append(str(path))
    return unique


async def _playwright_pdf(
    html_path: Path,
    pdf_path: Path,
    browser_path: str,
) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"Playwright unavailable: {type(exc).__name__}: {exc}"}

    playwright = None
    browser = None
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            executable_path=browser_path or None,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page(viewport={"width": 1403, "height": 1985})
        await page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=90000)
        await page.wait_for_function(
            "() => window.__GARAYE_LAYOUT_READY__ === true",
            timeout=180000,
        )
        # Give the final paint/layout pass a breath so no trailing sheet is cut.
        await page.wait_for_timeout(750)
        audit = await page.evaluate("() => window.__GARAYE_LAYOUT_AUDIT__")
        expected_cards = await page.evaluate(
            "() => document.querySelectorAll('#layout-source .news-card').length"
        )
        placed_cards = await page.evaluate(
            "() => document.querySelectorAll('#publication .news-card').length"
        )
        if expected_cards and placed_cards < expected_cards:
            return {
                "ok": False,
                "error": (
                    f"صفحه‌آرایی ناقص بود: {placed_cards} از {expected_cards} کارت جایگذاری شد."
                ),
                "browser_audit": audit or {},
            }
        await page.emulate_media(media="print")
        await page.pdf(
            path=str(pdf_path.resolve()),
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=False,
        )
        return {
            "ok": pdf_path.is_file() and pdf_path.stat().st_size > 1000,
            "renderer": f"playwright:{Path(browser_path).name if browser_path else 'chromium'}",
            "browser_audit": audit or {},
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


def _browser_cli_pdf(html_path: Path, pdf_path: Path, candidates: list[str]) -> dict[str, Any]:
    last_error = "مرورگر سازگار پیدا نشد."
    for browser in candidates:
        with tempfile.TemporaryDirectory(prefix="garaye_layout_") as profile:
            command = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                # Pagination waits on fonts/images; a short virtual-time budget
                # caused incomplete HTML→PDF captures on larger bulletins.
                "--virtual-time-budget=180000",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.resolve().as_uri(),
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=240,
                    check=False,
                )
            except Exception as exc:
                last_error = f"{Path(browser).name}: {type(exc).__name__}: {exc}"
                continue
            if result.returncode == 0 and pdf_path.is_file() and pdf_path.stat().st_size > 1000:
                return {"ok": True, "renderer": f"browser-cli:{Path(browser).name}"}
            last_error = (result.stderr or result.stdout or f"exit={result.returncode}")[-2000:]
    return {"ok": False, "error": last_error}


def _validate_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path))
    if not reader.pages:
        raise RuntimeError("PDF صفحه‌ای ندارد.")
    first = reader.pages[0]
    width = float(first.mediabox.width)
    height = float(first.mediabox.height)
    portrait = sorted((width, height))
    expected = sorted((_A3_WIDTH_PT, _A3_HEIGHT_PT))
    size_ok = abs(portrait[0] - expected[0]) < 4 and abs(portrait[1] - expected[1]) < 4
    if not size_ok:
        raise RuntimeError(
            f"اندازه PDF به جای A3 برابر {width:.1f}×{height:.1f} پوینت است."
        )
    return {
        "page_count": len(reader.pages),
        "page_width_pt": round(width, 2),
        "page_height_pt": round(height, 2),
        "a3_valid": True,
        "file_size": path.stat().st_size,
    }


class HtmlLayoutExporter:
    def __init__(self, settings: Settings, project_root: Path) -> None:
        self.settings = settings
        self.project_root = project_root

    async def export(self, data: BulletinData, output_dir: Path) -> HtmlLayoutExport:
        output_dir.mkdir(parents=True, exist_ok=True)
        html_path = output_dir / "bulletin_page_layout.html"
        pdf_path = output_dir / "bulletin_page_layout.pdf"
        report_path = output_dir / "bulletin_layout_report.json"

        rendered, audit = render_html(data, self.project_root)
        html_path.write_text(rendered, encoding="utf-8")
        files = {"layout_html": str(html_path)}
        warnings: list[str] = []

        candidates = _browser_candidates(
            getattr(self.settings, "layout_browser_path", "")
            or self.settings.magazine_browser_path
        )
        browser_path = candidates[0] if candidates else ""
        pdf_result: dict[str, Any]
        if browser_path:
            pdf_result = await _playwright_pdf(html_path, pdf_path, browser_path)
            if not pdf_result.get("ok"):
                fallback = await asyncio.to_thread(
                    _browser_cli_pdf, html_path, pdf_path, candidates
                )
                if fallback.get("ok"):
                    pdf_result = fallback | {
                        "playwright_warning": pdf_result.get("error")
                    }
                else:
                    pdf_result = {
                        "ok": False,
                        "error": (
                            f"{pdf_result.get('error', '')} | "
                            f"{fallback.get('error', '')}"
                        ).strip(" |"),
                    }
        else:
            pdf_result = {
                "ok": False,
                "error": (
                    "Microsoft Edge، Google Chrome یا Chromium پیدا نشد. "
                    "HTML ساخته شد؛ برای PDF مسیر مرورگر را در LAYOUT_BROWSER_PATH تنظیم کنید."
                ),
            }

        if pdf_result.get("ok"):
            try:
                pdf_audit = _validate_pdf(pdf_path)
            except Exception as exc:
                pdf_path.unlink(missing_ok=True)
                warnings.append(f"اعتبارسنجی PDF ناموفق بود: {exc}")
                audit["pdf"] = {"ok": False, "error": str(exc)}
            else:
                audit["pdf"] = pdf_result | pdf_audit
                audit["browser_audit"] = pdf_result.get("browser_audit") or {}
                files["layout_pdf"] = str(pdf_path)
        else:
            warnings.append(f"تولید مستقیم PDF انجام نشد: {pdf_result.get('error', 'خطای نامشخص')}")
            audit["pdf"] = pdf_result

        if not audit.get("font_embedded"):
            warnings.append(
                "فایل IRZar.ttf در web/assets/fonts موجود نبود؛ HTML از فونت نصب‌شده سامانه استفاده می‌کند."
            )
        browser_audit = audit.get("browser_audit") or {}
        if browser_audit.get("overflowCards"):
            warnings.append(
                f"{len(browser_audit['overflowCards'])} کارت دارای سرریز صفحه است؛ گزارش صفحه‌آرایی را بررسی کنید."
            )
        audit["warnings"] = warnings
        report_path.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files["layout_report"] = str(report_path)
        return HtmlLayoutExport(files=files, warnings=warnings, audit=audit)
