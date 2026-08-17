from __future__ import annotations

import base64
import html
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .bulletin_models import BulletinCategory, BulletinData, BulletinPerson, BulletinStatement
from .bulletin_validation import is_generic_url, valid_public_url
from .config import Settings

_TOPIC_COLORS = {
    "سیاست خارجی": "#b10016",
    "سیاست داخلی و حکمرانی": "#b10016",
    "سیاسی": "#b10016",
    "اقتصاد کلان": "#0c9b32",
    "اقتصاد": "#0c9b32",
    "بانک و پول": "#138a3a",
    "انرژی": "#d4a423",
    "اجتماعی و رفاه": "#2376b8",
    "فرهنگ و رسانه": "#7651a8",
    "فناوری و فضای مجازی": "#008b8b",
    "مسکن و شهر": "#a65f16",
    "نظامی و امنیتی": "#4b5563",
    "سایر": "#6b7280",
}


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _topic_color(topic: str) -> str:
    clean = str(topic or "").strip()
    for name, color in _TOPIC_COLORS.items():
        if name in clean or clean in name:
            return color
    return "#526070"


def _file_data_uri(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _placeholder_portrait(name: str) -> str:
    parts = [part for part in str(name or "").split() if part]
    initials = "".join(part[:1] for part in parts[:2]) or "؟"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="240" height="280" viewBox="0 0 240 280">
<rect width="240" height="280" fill="#e5e7eb"/><circle cx="120" cy="96" r="58" fill="#b8bec7"/>
<path d="M38 260c8-65 43-101 82-101s74 36 82 101" fill="#9ba3af"/>
<text x="120" y="108" text-anchor="middle" font-family="Tahoma,Arial" font-size="42" fill="#303845">{html.escape(initials)}</text>
</svg>'''
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _portrait(person: BulletinPerson, show: bool) -> str | None:
    if not show:
        return None
    return _file_data_uri(person.portrait_path) or _placeholder_portrait(person.name_canonical)


def _qr(statement: BulletinStatement, show: bool) -> str | None:
    if (
        not show
        or not statement.show_qr_in_bulletin
        or not valid_public_url(statement.editorial_source_url)
        or is_generic_url(statement.editorial_source_url)
    ):
        return None
    return _file_data_uri(statement.qr_code_path)


def _word_limited(text: str, maximum: int) -> str:
    words = str(text or "").split()
    if len(words) <= maximum:
        return " ".join(words)
    return " ".join(words[:maximum]).rstrip("،؛.") + "…"


def _iter_public(data: BulletinData):
    for category in data.categories:
        people = []
        for person in category.people:
            statements = [statement for statement in person.statements if statement.include_in_main]
            if statements:
                people.append((person, statements))
        if people:
            yield category, people


def _bulletin_font_faces() -> str:
    """Embed both available IRZar weights for the HTML/PDF export.

    ``IRZAR-BOLD.ttf`` is optional in source control because of font licensing,
    but when the deployment supplies it the one-line summary uses that exact
    weight rather than browser-synthesised bold.
    """
    font_dir = Path(__file__).resolve().parents[1] / "web" / "assets" / "fonts"
    rules: list[str] = []
    for family, names, weight in (
        ("IRZarEmbedded", ("IRZar.ttf", "IRZar.otf"), 400),
        ("IRZarBoldEmbedded", ("IRZAR-BOLD.ttf", "IRZAR-BOLD.otf", "IRZar-Bold.ttf", "IRZar-Bold.otf"), 700),
    ):
        for name in names:
            path = font_dir / name
            uri = _file_data_uri(str(path))
            if uri:
                fmt = "opentype" if path.suffix.lower() == ".otf" else "truetype"
                rules.append(
                    f'@font-face{{font-family:"{family}";src:url("{uri}") format("{fmt}");font-weight:{weight};font-style:normal;font-display:block;}}'
                )
                break
    return "\n".join(rules)


def _base_css(settings: Settings, *, magazine: bool) -> str:
    columns = settings.magazine_columns if magazine else 1
    page_size = settings.magazine_page_size
    return f'''
{_bulletin_font_faces()}
@page {{ size: {page_size}; margin: 15mm 13mm 19mm 13mm; }}
* {{ box-sizing: border-box; }}
html {{ direction: rtl; }}
body {{ margin:0; color:#171717; background:#fff; font-family:"IRZarEmbedded",IRZar,"B Zar","Vazirmatn",Tahoma,"DejaVu Sans",Arial,sans-serif; font-size:12.2pt; line-height:1.68; direction:rtl; text-align:right; unicode-bidi:plaintext; }}
.cover {{ height:250mm; display:table; table-layout:fixed; width:100%; text-align:center; break-after:page; direction:rtl; }}
.cover-inner {{ display:table-cell; width:100%; vertical-align:middle; text-align:center; }}
.cover .bismillah {{ font-size:16pt; margin-bottom:18mm; }}
.cover h1 {{ font-size:32pt; margin:0 0 4mm; }}
.cover h2 {{ font-size:24pt; margin:0 0 12mm; font-weight:600; }}
.cover .issue {{ font-size:18pt; font-weight:700; border-top:2px solid #111; border-bottom:2px solid #111; padding:3mm 14mm; }}
.cover .date {{ margin-top:8mm; font-size:15pt; }}
.cover .period {{ margin-top:3mm; color:#555; font-size:10.5pt; }}
.introduction, .controversies {{ break-after:page; }}
.section-title {{ break-before:page; margin:0 0 8mm; border-bottom:2px solid #111; text-align:right; }}
.section-title span {{ display:inline-block; background:#c8c8c8; border-right:7px solid #111; padding:2mm 7mm 2mm 16mm; font-size:27pt; font-weight:800; line-height:1.2; }}
.publication-columns {{ column-count:{columns}; column-gap:11mm; column-rule:1px solid #222; }}
.person-card {{ display:block; width:100%; break-inside:avoid; page-break-inside:avoid; margin:0 0 8mm; padding-bottom:5mm; border-bottom:2px solid #222; vertical-align:top; }}
.person-card.long {{ break-inside:auto; page-break-inside:auto; }}
.ribbon {{ height:7mm; color:#fff; display:table; table-layout:fixed; width:100%; font-size:9.5pt; font-weight:700; margin-bottom:3mm; }}
.ribbon .topic {{ display:table-cell; width:45%; padding:0 3mm; vertical-align:middle; }}
.ribbon .context {{ display:table-cell; width:55%; vertical-align:middle; background:#fff; color:#222; margin:1mm; height:5mm; line-height:5mm; padding:0 2mm; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; border:1px solid currentColor; }}
.card-header {{ display:block; min-height:30mm; margin-bottom:3mm; }}
.card-header::after {{ content:""; display:block; clear:both; }}
.card-header.no-photo {{ min-height:0; }}
.portrait {{ float:right; width:25mm; height:29mm; margin-left:4mm; object-fit:cover; filter:grayscale(1); border:1px solid #bbb; }}
.person-name {{ font-size:13.2pt; font-weight:800; margin:0 0 1mm; }}
.person-position {{ color:#333; font-size:10.5pt; margin-bottom:2mm; }}
.headline {{ font-size:12.5pt; font-weight:800; border-bottom:1px solid #1c6775; padding-bottom:2mm; margin-bottom:2.5mm; }}
.lead {{ font-family:"IRZarBoldEmbedded","IRZAR-BOLD","IRZarEmbedded",IRZar,"B Zar",Tahoma,Arial,sans-serif; font-size:11.3pt; font-weight:700; margin:0 0 2.5mm; }}
.body-row {{ display:block; }}
.body-row::after {{ content:""; display:block; clear:both; }}
.body {{ text-align:justify; margin:0; }}
.qr {{ float:left; width:18mm; height:18mm; object-fit:contain; margin:3mm 3mm 0 0; }}
.footer {{ position:fixed; left:12mm; right:12mm; bottom:5mm; height:9mm; background:#c8c8c8; display:table; table-layout:fixed; width:calc(100% - 24mm); padding:0 5mm; font-size:10pt; z-index:50; }}
.footer > div {{ display:table-cell; width:33.33%; vertical-align:middle; }}
.footer .center {{ text-align:center; font-weight:700; }}
.footer .left {{ direction:ltr; text-align:left; }}
.footer .page-number::after {{ content:""; }}
.intro-box {{ border-right:5px solid #111; padding:4mm 6mm; background:#f4f4f4; text-align:justify; }}
.controversy {{ margin:0 0 6mm; padding:4mm; border:1px solid #bbb; break-inside:avoid; }}
.controversy h3 {{ margin:0 0 2mm; font-size:15pt; }}
.controversy p {{ margin:0; text-align:justify; }}
.classic-person {{ break-inside:avoid; margin-bottom:7mm; border-bottom:1px solid #777; padding-bottom:4mm; }}
.classic-person h3 {{ margin:0 0 2mm; font-size:15pt; }}
.classic-entry {{ margin:0 0 3mm; }}
.classic-entry .context-line {{ font-weight:700; }}
@media screen {{ body {{ max-width:210mm; margin:auto; box-shadow:0 0 20px #aaa; padding:10mm; }} .footer {{ display:none; }} }}
'''


def _cover(data: BulletinData) -> str:
    period = ""
    if data.meta.window_start and data.meta.window_end:
        period = f'<div class="period">بازه رصد: {_esc(data.meta.window_start)} تا {_esc(data.meta.window_end)}</div>'
    return f'''
<section class="cover"><div class="cover-inner">
  <div class="bismillah">باسمه تعالی</div>
  <h1>خبرنامه رصد روزانه</h1>
  <h2>اظهارات شخصیت‌ها</h2>
  <div class="issue">شماره {_esc(data.meta.issue_number)}</div>
  <div class="date">{_esc(data.meta.report_date_jalali)}</div>
  {period}
</div></section>'''


def _introduction(data: BulletinData) -> str:
    categories = "، ".join(category.title for category in data.categories if any(s.include_in_main for p in category.people for s in p.statements))
    text = (
        f"این شماره از خبرنامه، مهم‌ترین و قابل‌توجه‌ترین اظهارات روز را در بازه {data.meta.window_start or 'رصد تعیین‌شده'} "
        f"تا {data.meta.window_end or 'پایان بازه'} پوشش می‌دهد. محتوای منتشرشده پس از تشخیص صاحب اظهار، "
        "حذف موارد روزمره و تکراری، تفکیک موضوعی و بازبینی سردبیری انتخاب شده است. "
        f"حوزه‌های دارای محتوای قابل انتشار عبارت‌اند از: {categories or 'حوزه‌های منتخب روز'}. "
        "این خبرنامه گزارش تحلیلی مستقل نیست و مواضع اشخاص را به‌صورت خنثی، فشرده و مستند منعکس می‌کند."
    )
    return f'<section class="introduction"><h2>مقدمه</h2><div class="intro-box">{_esc(text)}</div></section>'


def _controversies(data: BulletinData) -> str:
    if not data.controversies:
        return ""
    blocks = []
    for index, item in enumerate(data.controversies, 1):
        points = "".join(f"<li>{_esc(side.position_summary)}</li>" for side in item.sides if side.position_summary)
        blocks.append(
            f'<article class="controversy"><h3>{index}. {_esc(item.title)}</h3><p>{_esc(item.summary)}</p>'
            + (f"<ul>{points}</ul>" if points else "") + "</article>"
        )
    return '<section class="controversies"><h2>پربازتاب</h2>' + "".join(blocks) + "</section>"


def _footer(data: BulletinData) -> str:
    return (
        '<div class="footer">'
        f'<div>رصد شماره {_esc(data.meta.issue_number)}</div>'
        '<div class="center"><span class="page-number"></span></div>'
        f'<div class="left">{_esc(data.meta.report_date_jalali)}</div>'
        '</div>'
    )


def _magazine_card(
    person: BulletinPerson,
    statement: BulletinStatement,
    settings: Settings,
) -> str:
    color = _topic_color(statement.topic)
    portrait = _portrait(person, settings.magazine_show_portraits)
    qr = _qr(statement, settings.magazine_show_qr_codes)
    headline = statement.headline or statement.summary_lead or statement.summary_short
    lead = statement.summary_lead or statement.summary_short
    body = statement.summary_body or statement.summary_detailed or statement.summary_short
    body = _word_limited(body, settings.magazine_max_body_words)
    long_class = " long" if len(body.split()) > 125 else ""
    header_class = "card-header" if portrait else "card-header no-photo"
    portrait_html = f'<img class="portrait" src="{portrait}" alt="تصویر {_esc(person.name_canonical)}">' if portrait else ""
    qr_html = f'<img class="qr" src="{qr}" alt="QR منبع">' if qr else ""
    position = f'<div class="person-position">{_esc(person.position)}</div>' if person.position else ""
    context = statement.editorial_context_label or statement.statement_mode or "در اظهارنظری"
    return f'''
<article class="person-card{long_class}">
  <div class="ribbon" style="background:{color}"><div class="topic">{_esc(statement.topic)}</div><div class="context" style="color:{color}">{_esc(context)}</div></div>
  <div class="{header_class}">
    {portrait_html}
    <div><div class="person-name">{_esc(person.name_canonical)}</div>{position}<div class="headline">{_esc(headline)}</div></div>
  </div>
  <p class="lead">خلاصه: {_esc(lead)}</p>
  <div class="body-row"><p class="body">{_esc(body)}</p>{qr_html}</div>
</article>'''


def render_magazine_html(data: BulletinData, settings: Settings) -> str:
    sections = []
    for category, people in _iter_public(data):
        cards = []
        for person, statements in people:
            for statement in statements:
                cards.append(_magazine_card(person, statement, settings))
        sections.append(
            f'<section class="category"><h2 class="section-title"><span>{_esc(category.title)}</span></h2>'
            f'<div class="publication-columns">{"".join(cards)}</div></section>'
        )
    return f'''<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>خبرنامه شماره {_esc(data.meta.issue_number)}</title><style>{_base_css(settings, magazine=True)}</style></head><body>{_footer(data)}{_cover(data)}{_introduction(data)}{_controversies(data)}{"".join(sections)}</body></html>'''


def render_classic_html(data: BulletinData, settings: Settings) -> str:
    sections = []
    for category, people in _iter_public(data):
        blocks = []
        for person, statements in people:
            entries = []
            for statement in statements:
                context = statement.editorial_context_label or statement.statement_mode or "در اظهارنظری"
                lead = statement.summary_lead or statement.summary_short or statement.headline
                body = statement.summary_body or statement.summary_detailed or statement.summary_short
                qr = _qr(statement, settings.magazine_show_qr_codes)
                qr_html = f'<img class="qr" src="{qr}" alt="QR منبع">' if qr else ""
                entries.append(
                    f'<div class="classic-entry"><div class="context-line">{_esc(context)}:</div>'
                    f'<p class="lead">خلاصه: {_esc(lead)}</p>'
                    f'<div class="body-row"><p class="body">{_esc(body)}</p>{qr_html}</div></div>'
                )
            descriptor = f" - {_esc(person.position)}" if person.position else ""
            blocks.append(f'<article class="classic-person"><h3>{person.continuous_number}. {_esc(person.name_canonical)}{descriptor}</h3>{"".join(entries)}</article>')
        sections.append(f'<section class="category"><h2 class="section-title"><span>{_esc(category.title)}</span></h2>{"".join(blocks)}</section>')
    return f'''<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>خبرنامه کلاسیک شماره {_esc(data.meta.issue_number)}</title><style>{_base_css(settings, magazine=False)}</style></head><body>{_footer(data)}{_cover(data)}{_introduction(data)}{_controversies(data)}{"".join(sections)}</body></html>'''


def _browser_candidates(configured: str) -> list[str]:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    for command in ("msedge", "chrome", "chromium", "chromium-browser", "google-chrome"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(resolved)
    if os.name == "nt":
        roots = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]
        relative = [
            Path("Microsoft/Edge/Application/msedge.exe"),
            Path("Google/Chrome/Application/chrome.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for item in relative:
                candidates.append(str(Path(root) / item))
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique and Path(candidate).exists():
            unique.append(candidate)
    return unique



def _stamp_page_numbers(pdf_path: Path) -> dict[str, Any]:
    """Stamp a deterministic page number over the centre cell of the fixed footer.

    Browser print engines do not consistently implement CSS ``counter(page)`` inside
    fixed elements. PyMuPDF is therefore used after rendering so Edge, Chrome and
    WeasyPrint produce the same numbered output.
    """
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"PyMuPDF unavailable: {type(exc).__name__}: {exc}"}

    source = Path(pdf_path)
    temp = source.with_name(source.stem + ".numbered.tmp.pdf")
    try:
        document = fitz.open(source)
        for index, page in enumerate(document, 1):
            height = float(page.rect.height)
            width = float(page.rect.width)
            # The HTML footer is positioned inside the printable area, approximately 25–35 mm above the physical page bottom.
            # A narrow centred box avoids changing issue/date text on either side.
            box = fitz.Rect(width * 0.42, height - 99.0, width * 0.58, height - 72.0)
            page.insert_textbox(
                box,
                str(index),
                fontsize=10,
                fontname="helv",
                align=fitz.TEXT_ALIGN_CENTER,
                color=(0, 0, 0),
                overlay=True,
            )
        document.save(temp, garbage=4, deflate=True)
        document.close()
        temp.replace(source)
        return {"ok": True, "pages": index if 'index' in locals() else 0}
    except Exception as exc:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

def html_to_pdf(html_path: Path, pdf_path: Path, settings: Settings) -> dict[str, Any]:
    # WeasyPrint provides the most deterministic paged-media rendering when it is
    # already available. It is optional because some Windows servers do not have
    # its native runtime; Edge/Chrome remains the zero-configuration fallback.
    try:
        from weasyprint import HTML  # type: ignore
        HTML(filename=str(html_path.resolve()), base_url=str(html_path.parent.resolve())).write_pdf(str(pdf_path.resolve()))
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            stamp = _stamp_page_numbers(pdf_path)
            return {"ok": True, "browser": "weasyprint", "pdf": str(pdf_path), "stderr": "", "page_numbering": stamp}
    except Exception as exc:
        weasy_error = f"{type(exc).__name__}: {exc}"
    else:
        weasy_error = "WeasyPrint produced no file"

    candidates = _browser_candidates(settings.magazine_browser_path)
    if not candidates:
        return {"ok": False, "error": "WeasyPrint/Edge/Chrome/Chromium برای تولید PDF در دسترس نیست. " + weasy_error, "pdf": None}
    last_error = weasy_error
    for browser in candidates:
        with tempfile.TemporaryDirectory(prefix="prasad_browser_") as profile:
            command = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.resolve().as_uri(),
            ]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if result.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 1000:
                stamp = _stamp_page_numbers(pdf_path)
                return {"ok": True, "browser": browser, "pdf": str(pdf_path), "stderr": result.stderr[-2000:], "page_numbering": stamp}
            last_error = (result.stderr or result.stdout or f"returncode={result.returncode}")[-4000:]
    return {"ok": False, "error": last_error or "تولید PDF ناموفق بود.", "pdf": None}


class PublicLayoutExporter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def export(self, data: BulletinData, output_dir: Path) -> dict[str, str]:
        output_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}

        if self.settings.bulletin_generate_classic_pdf:
            classic_html = output_dir / "bulletin_classic.html"
            classic_pdf = output_dir / "bulletin_classic.pdf"
            classic_html.write_text(render_classic_html(data, self.settings), encoding="utf-8")
            files["classic_html"] = str(classic_html)
            result = html_to_pdf(classic_html, classic_pdf, self.settings)
            if result.get("ok"):
                files["classic_pdf"] = str(classic_pdf)
            else:
                (output_dir / "classic_pdf_error.txt").write_text(str(result.get("error") or "unknown"), encoding="utf-8")
                files["classic_pdf_error"] = str(output_dir / "classic_pdf_error.txt")

        if self.settings.bulletin_generate_magazine_html or self.settings.bulletin_generate_magazine_pdf:
            magazine_html = output_dir / "bulletin_magazine.html"
            magazine_html.write_text(render_magazine_html(data, self.settings), encoding="utf-8")
            if self.settings.bulletin_generate_magazine_html:
                files["magazine_html"] = str(magazine_html)
            if self.settings.bulletin_generate_magazine_pdf:
                magazine_pdf = output_dir / "bulletin_magazine.pdf"
                result = html_to_pdf(magazine_html, magazine_pdf, self.settings)
                if result.get("ok"):
                    files["magazine_pdf"] = str(magazine_pdf)
                else:
                    (output_dir / "magazine_pdf_error.txt").write_text(str(result.get("error") or "unknown"), encoding="utf-8")
                    files["magazine_pdf_error"] = str(output_dir / "magazine_pdf_error.txt")
        return files
