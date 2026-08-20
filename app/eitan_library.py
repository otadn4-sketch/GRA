from __future__ import annotations

"""Parse and search Eitan Gara keyword libraries stored as Excel workbooks."""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .persian_text import canonical_key, normalize_persian


EITAN_FILE_MAX_BYTES = 5 * 1024 * 1024
EITAN_TEXT_MAX_CHARS = 200_000
EITAN_SEARCH_GROUP_LIMIT = 40
EITAN_SEARCH_TERM_LIMIT = 160
EITAN_AND_WIDTH = 4

_EITAN_HEADER_TERMS = {
    "کلیدواژه",
    "کلیدواژه‌ها",
    "کلید واژه",
    "keyword",
    "keywords",
    "نام",
    "افراد",
    "شخص",
    "افراد شاخص",
    "title",
    "name",
    "دسته",
    "گروه",
    "خوشه",
    "محور",
    "وزن",
    "weight",
}

_KEYWORD_HEADERS = {
    "کلیدواژه",
    "کلیدواژه‌ها",
    "کلید واژه",
    "عبارت",
    "واژه",
    "keyword",
    "keywords",
    "term",
    "terms",
}
_PERSON_HEADERS = {
    "فرد",
    "شخص",
    "اشخاص",
    "افراد",
    "افراد شاخص",
    "شخصیت",
    "نام",
    "people",
    "person",
    "persons",
    "name",
}
_CLUSTER_HEADERS = {
    "دسته",
    "گروه",
    "خوشه",
    "محور",
    "موضوع",
    "cluster",
    "group",
    "category",
    "theme",
    "axis",
}
_WEIGHT_HEADERS = {"وزن", "weight", "امتیاز", "score", "priority"}
_MATCH_HEADERS = {"عملگر", "منطق", "match", "operator", "and", "or"}


def _clean_term(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = normalize_persian(text)
    return text.strip(" -_|،,;/")


def _header_key(value: Any) -> str:
    return canonical_key(_clean_term(value)) or _clean_term(value).casefold()


def split_eitan_terms(text: str | None) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for raw_line in str(text or "").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split()).strip()
        if not line or line.startswith("#"):
            continue
        chunks = re.split(r"[|،;]+", line)
        if len(chunks) == 1 and "," in line and "\t" not in line:
            chunks = [part.strip() for part in line.split(",")]
        for chunk in chunks:
            term = _clean_term(chunk)
            if len(term) < 2:
                continue
            key = canonical_key(term) or term
            if key in _EITAN_HEADER_TERMS or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


def _unique_terms(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = _clean_term(value)
        if len(term) < 2:
            continue
        key = canonical_key(term) or term
        if key in _EITAN_HEADER_TERMS or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


@dataclass
class EitanClause:
    keywords: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    cluster: str = ""
    match: str = "any"
    weight: float = 1.0

    def terms(self) -> list[str]:
        return _unique_terms([*self.keywords, *self.people])


@dataclass
class EitanLibrary:
    keywords: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    clusters: list[dict[str, Any]] = field(default_factory=list)
    clauses: list[dict[str, Any]] = field(default_factory=list)
    filename: str = ""
    sheets: list[str] = field(default_factory=list)
    paired: bool = False

    def as_json(self) -> str:
        payload = {
            "version": 1,
            "filename": self.filename,
            "sheets": self.sheets,
            "keywords": self.keywords,
            "people": self.people,
            "clusters": self.clusters,
            "clauses": self.clauses,
            "paired": self.paired,
        }
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > EITAN_TEXT_MAX_CHARS:
            raise ValueError("حجم کتابخانه بیش از حد مجاز است.")
        return text

    def summary(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "sheets": self.sheets,
            "keyword_count": len(self.keywords),
            "people_count": len(self.people),
            "cluster_count": len(self.clusters),
            "clause_count": len(self.clauses),
            "paired": self.paired,
            "keyword_sample": self.keywords[:12],
            "people_sample": self.people[:12],
            "clusters": [item.get("name") for item in self.clusters[:12]],
        }


def library_from_parts(
    *,
    keywords: Iterable[str] | None = None,
    people: Iterable[str] | None = None,
    filename: str = "",
) -> EitanLibrary:
    keyword_list = _unique_terms(keywords or [])
    people_list = _unique_terms(people or [])
    clauses = [{"keywords": [term], "people": [], "cluster": "", "match": "any", "weight": 1.0} for term in keyword_list]
    clauses.extend({"keywords": [], "people": [term], "cluster": "", "match": "any", "weight": 1.0} for term in people_list)
    return EitanLibrary(
        keywords=keyword_list,
        people=people_list,
        clusters=[],
        clauses=clauses,
        filename=filename,
        sheets=[],
        paired=False,
    )


def library_from_json(raw: str | None, *, keywords_text: str = "", people_text: str = "") -> EitanLibrary:
    text = str(raw or "").strip()
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and (payload.get("keywords") or payload.get("people") or payload.get("clauses")):
            return EitanLibrary(
                keywords=_unique_terms(payload.get("keywords") or []),
                people=_unique_terms(payload.get("people") or []),
                clusters=list(payload.get("clusters") or []),
                clauses=list(payload.get("clauses") or []),
                filename=str(payload.get("filename") or ""),
                sheets=list(payload.get("sheets") or []),
                paired=bool(payload.get("paired")),
            )
    return library_from_parts(keywords=split_eitan_terms(keywords_text), people=split_eitan_terms(people_text))


def library_from_axis(axis: dict[str, Any] | None) -> EitanLibrary:
    row = axis or {}
    return library_from_json(
        row.get("library_json"),
        keywords_text=str(row.get("keywords_text") or ""),
        people_text=str(row.get("people_text") or ""),
    )


def _classify_header(header: str) -> str | None:
    key = _header_key(header)
    if key in {canonical_key(item) or item for item in _KEYWORD_HEADERS}:
        return "keyword"
    if key in {canonical_key(item) or item for item in _PERSON_HEADERS}:
        return "person"
    if key in {canonical_key(item) or item for item in _CLUSTER_HEADERS}:
        return "cluster"
    if key in {canonical_key(item) or item for item in _WEIGHT_HEADERS}:
        return "weight"
    if key in {canonical_key(item) or item for item in _MATCH_HEADERS}:
        return "match"
    return None


def _sheet_role(name: str) -> str:
    key = _header_key(name)
    if any(token in key for token in ("فرد", "شخص", "people", "person")):
        return "people"
    if any(token in key for token in ("کلید", "keyword", "واژه")):
        return "keywords"
    return "mixed"


def _parse_weight(value: Any) -> float:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return 1.0
    try:
        number = float(text)
    except ValueError:
        return 1.0
    if number <= 0:
        return 1.0
    return min(number, 100.0)


def _parse_match(value: Any, *, default: str) -> str:
    text = _header_key(value)
    if text in {"and", "و", "همه", "all"}:
        return "all"
    if text in {"or", "یا", "any", "هریک"}:
        return "any"
    return default


def _rows_from_xlsx(payload: bytes) -> tuple[list[str], list[tuple[str, list[list[Any]]]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    sheets: list[tuple[str, list[list[Any]]]] = []
    try:
        for sheet in workbook.worksheets:
            rows: list[list[Any]] = []
            for row in sheet.iter_rows(min_row=1, max_col=24, max_row=5000, values_only=True):
                values = list(row)
                if not any(cell not in (None, "") for cell in values):
                    continue
                rows.append(values)
            sheets.append((str(sheet.title or "Sheet"), rows))
    finally:
        workbook.close()
    return [name for name, _rows in sheets], sheets


def _rows_from_csv(payload: bytes) -> tuple[list[str], list[tuple[str, list[list[Any]]]]]:
    sample = payload.decode("utf-8-sig", errors="replace")
    rows = [list(row) for row in csv.reader(io.StringIO(sample)) if any(str(cell or "").strip() for cell in row)]
    return ["csv"], [("csv", rows)]


def _rows_from_text(payload: bytes) -> tuple[list[str], list[tuple[str, list[list[Any]]]]]:
    lines = payload.decode("utf-8-sig", errors="replace").splitlines()
    rows = [[line] for line in lines if str(line or "").strip()]
    return ["txt"], [("txt", rows)]


def parse_eitan_library_upload(filename: str | None, data: bytes) -> EitanLibrary:
    payload = data or b""
    if len(payload) > EITAN_FILE_MAX_BYTES:
        raise ValueError("حجم فایل نباید بیشتر از ۵ مگابایت باشد.")
    name = str(filename or "").strip()
    lowered = name.lower()
    if lowered.endswith((".xlsx", ".xlsm")):
        sheet_names, sheets = _rows_from_xlsx(payload)
    elif lowered.endswith(".csv"):
        sheet_names, sheets = _rows_from_csv(payload)
    else:
        sheet_names, sheets = _rows_from_text(payload)

    keywords: list[str] = []
    people: list[str] = []
    clauses: list[dict[str, Any]] = []
    cluster_map: dict[str, dict[str, list[str]]] = {}
    paired_rows = 0
    filled_rows = 0

    for sheet_name, rows in sheets:
        if not rows:
            continue
        role = _sheet_role(sheet_name)
        header_kinds = [_classify_header(cell) for cell in rows[0]]
        has_header = any(kind for kind in header_kinds)
        body = rows[1:] if has_header else rows
        if not has_header:
            if role == "people":
                header_kinds = ["person"]
            elif role == "keywords":
                header_kinds = ["keyword"]
            else:
                header_kinds = ["keyword", "person"] if any(len(row) > 1 and _clean_term(row[1]) for row in body[:12]) else ["keyword"]

        for row in body:
            record = {"keyword": [], "person": [], "cluster": "", "weight": 1.0, "match": ""}
            for index, kind in enumerate(header_kinds):
                if not kind or index >= len(row):
                    continue
                value = row[index]
                if kind == "cluster":
                    record["cluster"] = _clean_term(value)
                elif kind == "weight":
                    record["weight"] = _parse_weight(value)
                elif kind == "match":
                    record["match"] = str(value or "")
                elif kind == "keyword":
                    record["keyword"].extend(split_eitan_terms(value))
                elif kind == "person":
                    record["person"].extend(split_eitan_terms(value))
            keyword_terms = _unique_terms(record["keyword"])
            person_terms = _unique_terms(record["person"])
            if not keyword_terms and not person_terms:
                continue
            filled_rows += 1
            cluster = str(record["cluster"] or "")
            both = bool(keyword_terms and person_terms)
            if both:
                paired_rows += 1
            match = _parse_match(record["match"], default="all" if both else "any")
            clauses.append(
                {
                    "keywords": keyword_terms,
                    "people": person_terms,
                    "cluster": cluster,
                    "match": match,
                    "weight": record["weight"],
                }
            )
            keywords.extend(keyword_terms)
            people.extend(person_terms)
            if cluster:
                bucket = cluster_map.setdefault(cluster, {"keywords": [], "people": []})
                bucket["keywords"].extend(keyword_terms)
                bucket["people"].extend(person_terms)

    keywords = _unique_terms(keywords)
    people = _unique_terms(people)
    if not keywords and not people:
        raise ValueError("در فایل کتابخانه عبارتی برای جست‌وجو پیدا نشد.")
    paired = filled_rows > 0 and (paired_rows / filled_rows) >= 0.3
    if not paired:
        for clause in clauses:
            if clause["match"] == "all" and not (clause["keywords"] and clause["people"]):
                clause["match"] = "any"
    clusters = [
        {
            "name": name,
            "keywords": _unique_terms(values["keywords"]),
            "people": _unique_terms(values["people"]),
        }
        for name, values in cluster_map.items()
    ]
    return EitanLibrary(
        keywords=keywords,
        people=people,
        clusters=clusters,
        clauses=clauses,
        filename=name,
        sheets=sheet_names,
        paired=paired,
    )


def parse_eitan_upload(filename: str | None, data: bytes) -> str:
    """Legacy single-column helper used by older two-file uploads."""

    library = parse_eitan_library_upload(filename, data)
    stored = "\n".join(library.keywords or library.people)
    if len(stored) > EITAN_TEXT_MAX_CHARS:
        raise ValueError("حجم فهرست عبارات بیش از حد مجاز است.")
    return stored


def eitan_search_groups(library: EitanLibrary) -> list[list[str]]:
    groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add_group(terms: list[str]) -> None:
        cleaned = _unique_terms(terms)[:EITAN_AND_WIDTH]
        if not cleaned:
            return
        key = tuple(canonical_key(term) or term for term in cleaned)
        if key in seen:
            return
        seen.add(key)
        groups.append(cleaned)

    for clause in library.clauses:
        keywords = _unique_terms(clause.get("keywords") or [])
        people = _unique_terms(clause.get("people") or [])
        match = str(clause.get("match") or "any")
        if match == "all" and (keywords or people):
            add_group([*keywords, *people])
            continue
        for term in [*keywords, *people]:
            add_group([term])
        if len(groups) >= EITAN_SEARCH_GROUP_LIMIT:
            break
    if not groups:
        for term in [*library.keywords, *library.people]:
            add_group([term])
            if len(groups) >= EITAN_SEARCH_GROUP_LIMIT:
                break
    return groups[:EITAN_SEARCH_GROUP_LIMIT]


def eitan_chart_terms(library: EitanLibrary, *, limit: int = 24) -> list[dict[str, Any]]:
    ranked: list[tuple[float, str, str]] = []
    for clause in library.clauses:
        weight = float(clause.get("weight") or 1.0)
        cluster = str(clause.get("cluster") or "")
        for term in _unique_terms([*(clause.get("keywords") or []), *(clause.get("people") or [])]):
            kind = "person" if term in library.people and term not in library.keywords else "keyword"
            ranked.append((weight, term, kind if not cluster else cluster))
    if not ranked:
        ranked = [(1.0, term, "keyword") for term in library.keywords] + [(1.0, term, "person") for term in library.people]
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for weight, term, kind in sorted(ranked, key=lambda item: (-item[0], item[1])):
        key = canonical_key(term) or term
        if key in seen:
            continue
        seen.add(key)
        items.append({"term": term, "weight": weight, "kind": kind})
        if len(items) >= limit:
            break
    return items
