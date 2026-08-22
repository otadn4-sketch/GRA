from __future__ import annotations

"""Persian RTL defaults for Garaye Word documents."""

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


PERSIAN_LANG = "fa-IR"


def _append_once(parent, tag: str, **attrs: str):
    existing = parent.find(qn(tag))
    if existing is not None:
        for key, value in attrs.items():
            existing.set(qn(key), value)
        return existing
    element = OxmlElement(tag)
    for key, value in attrs.items():
        element.set(qn(key), value)
    parent.append(element)
    return element


def apply_persian_document(doc) -> None:
    """Set document, Normal style, and sections to Persian RTL."""

    try:
        styles = doc.styles
        normal = styles["Normal"]
        p_pr = normal.element.get_or_add_pPr()
        _append_once(p_pr, "w:bidi")
        jc = p_pr.find(qn("w:jc"))
        if jc is None:
            jc = OxmlElement("w:jc")
            p_pr.append(jc)
        jc.set(qn("w:val"), "right")
        r_pr = normal.element.get_or_add_rPr()
        apply_persian_run(r_pr)
    except Exception:
        pass
    try:
        settings = doc.settings.element
        _append_once(
            settings,
            "w:themeFontLang",
            **{"w:val": PERSIAN_LANG, "w:eastAsia": PERSIAN_LANG, "w:bidi": PERSIAN_LANG},
        )
    except Exception:
        pass
    for section in doc.sections:
        apply_persian_section(section)


def apply_persian_section(section) -> None:
    sect_pr = section._sectPr
    bidi = sect_pr.find(qn("w:bidi"))
    if bidi is None:
        bidi = OxmlElement("w:bidi")
        sect_pr.append(bidi)
    bidi.set(qn("w:val"), "1")


def apply_persian_run(r_pr) -> None:
    _append_once(r_pr, "w:rtl")
    lang = r_pr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        r_pr.append(lang)
    lang.set(qn("w:val"), PERSIAN_LANG)
    lang.set(qn("w:bidi"), PERSIAN_LANG)
    lang.set(qn("w:eastAsia"), PERSIAN_LANG)
    cs = r_pr.find(qn("w:cs"))
    if cs is None:
        r_pr.append(OxmlElement("w:cs"))


def rtl_paragraph(paragraph, *, align: str = "right") -> None:
    alignment = {
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
    }.get(align, WD_ALIGN_PARAGRAPH.RIGHT)
    paragraph.alignment = alignment
    p_pr = paragraph._p.get_or_add_pPr()
    _append_once(p_pr, "w:bidi")
    jc = p_pr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        p_pr.append(jc)
    jc.set(qn("w:val"), align)
    for run in paragraph.runs:
        apply_persian_run(run._element.get_or_add_rPr())


def rtl_table(table) -> None:
    table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    tbl_pr = table._tbl.tblPr
    if tbl_pr.find(qn("w:bidiVisual")) is None:
        tbl_pr.append(OxmlElement("w:bidiVisual"))
    bidi = tbl_pr.find(qn("w:bidiVisual"))
    if bidi.get(qn("w:val")) is None:
        bidi.set(qn("w:val"), "1")
