from __future__ import annotations

"""Shared, deterministic A3 layout plan for the bulletin news cards.

The HTML/PDF and editable Word renderers intentionally consume this plan.  It
contains no renderer-specific coordinates: it is a compact editorial order and
column/page packing contract.  Renderers are free to draw the card with their
native primitives, but may not change its page, column, or identity treatment.
"""

from dataclasses import dataclass
from math import ceil
from typing import Iterable


# A3 portrait content area after the fixed header/footer used by the approved
# human reference layout: 420 - 54 - 23 = 343 mm.
PAGE_WIDTH_MM = 297.0
PAGE_HEIGHT_MM = 420.0
CONTENT_TOP_MM = 54.0
CONTENT_BOTTOM_MM = 23.0
CONTENT_HEIGHT_MM = PAGE_HEIGHT_MM - CONTENT_TOP_MM - CONTENT_BOTTOM_MM
COLUMN_GAP_MM = 12.0
CARD_GAP_MM = 5.0


@dataclass(frozen=True)
class LayoutPlanInput:
    """The small set of editorial values that affect card geometry."""

    key: str
    person_key: str
    section_key: str
    section_title: str
    main_subject: str = ""
    detail: str = ""
    sentence: str = ""
    location: str = ""
    paragraph: str = ""
    has_qr: bool = False


@dataclass(frozen=True)
class LayoutPlacement:
    key: str
    page_index: int
    column_index: int  # 0 is the right column; 1 is the left column.
    sequence_index: int
    show_full_identity: bool
    estimated_height_mm: float
    section_key: str
    section_title: str


@dataclass(frozen=True)
class LayoutPlanPage:
    page_index: int
    section_key: str
    section_title: str
    right: tuple[LayoutPlacement, ...]
    left: tuple[LayoutPlacement, ...]


@dataclass(frozen=True)
class LayoutPlan:
    """A render-neutral two-column A3 pagination plan."""

    pages: tuple[LayoutPlanPage, ...]
    placements: tuple[LayoutPlacement, ...]
    input_count: int
    estimated_content_pages: int

    def by_key(self) -> dict[str, LayoutPlacement]:
        return {placement.key: placement for placement in self.placements}

    def page_for(self, page_index: int) -> LayoutPlanPage:
        return self.pages[page_index]

    def audit(self) -> dict[str, object]:
        return {
            "contract": "shared-a3-two-column-v1",
            "page_size": "A3 portrait",
            "content_height_mm": CONTENT_HEIGHT_MM,
            "card_gap_mm": CARD_GAP_MM,
            "content_pages": self.estimated_content_pages,
            "cards": self.input_count,
            "placements": [
                {
                    "key": p.key,
                    "page": p.page_index + 1,
                    "column": "right" if p.column_index == 0 else "left",
                    "sequence": p.sequence_index + 1,
                    "full_identity": p.show_full_identity,
                    "estimated_height_mm": p.estimated_height_mm,
                }
                for p in self.placements
            ],
        }


def _line_count(value: str, characters_per_line: int) -> int:
    clean = " ".join(str(value or "").split())
    if not clean:
        return 0
    return max(1, ceil(len(clean) / characters_per_line))


def estimate_card_height_mm(card: LayoutPlanInput, *, show_full_identity: bool) -> float:
    """Conservative natural-height estimate used only for column packing.

    The estimate is deliberately slightly larger than the CSS/Word typography.
    This keeps a whole card in its intended column without reserving a fixed
    card height or truncating editorial content.
    """

    identity = 26.0 if show_full_identity else 7.0
    band = 10.5
    sentence = max(1, _line_count(card.sentence, 31)) * 5.2
    location = 6.5 if card.location else 0.0
    paragraph = max(1, _line_count(card.paragraph, 38)) * 4.9
    # A QR is deliberately small but receives its own breathing room at the
    # bottom of the body.  Only editorial-workbench QR files set ``has_qr``.
    qr = 18.0 if card.has_qr else 0.0
    detail = 2.0 if _line_count(card.detail, 28) > 1 else 0.0
    subject = 2.0 if _line_count(card.main_subject, 28) > 1 else 0.0
    # identity + band/margins + vertical rail/body padding + message content.
    # Word's editable nested-table card has a small but material intrinsic
    # paragraph/row overhead.  Planning to that upper bound is what lets the
    # same plan remain valid in *both* renderers; HTML simply leaves natural
    # white space instead of squeezing or splitting a card.
    raw = identity + band + sentence + location + paragraph + qr + detail + subject + 15.0
    return round(raw * 1.35, 1)


def build_layout_plan(cards: Iterable[LayoutPlanInput]) -> LayoutPlan:
    """Pack cards in the exact supplied editorial order.

    Every news item keeps its own photo, name and position.  The human
    reference uses identity as part of each independent editorial unit, so
    adjacent statements are never visually collapsed. Cards are still never
    re-sorted or coalesced.
    """

    inputs = list(cards)
    pages: list[LayoutPlanPage] = []
    placements: list[LayoutPlacement] = []
    right: list[LayoutPlacement] = []
    left: list[LayoutPlacement] = []
    page_index = 0
    column_index = 0
    used_height = 0.0
    page_section_key = "other"
    page_section_title = "سایر اخبار"

    def flush_page() -> None:
        nonlocal right, left, page_index, used_height, column_index
        if not right and not left:
            return
        pages.append(
            LayoutPlanPage(
                page_index=page_index,
                section_key=page_section_key,
                section_title=page_section_title,
                right=tuple(right),
                left=tuple(left),
            )
        )
        page_index += 1
        right = []
        left = []
        used_height = 0.0
        column_index = 0

    for sequence_index, card in enumerate(inputs):
        show_full_identity = True
        height = estimate_card_height_mm(card, show_full_identity=show_full_identity)
        # A pathological message is still preserved in full and receives a
        # dedicated column; normal editorial summaries are much shorter.
        height = min(height, CONTENT_HEIGHT_MM)
        additional = height if used_height == 0 else height + CARD_GAP_MM

        if used_height and used_height + additional > CONTENT_HEIGHT_MM:
            if column_index == 0:
                column_index = 1
                used_height = 0.0
            else:
                flush_page()
            additional = height

        if not right and not left:
            page_section_key = card.section_key
            page_section_title = card.section_title

        placement = LayoutPlacement(
            key=card.key,
            page_index=page_index,
            column_index=column_index,
            sequence_index=sequence_index,
            show_full_identity=show_full_identity,
            estimated_height_mm=height,
            section_key=card.section_key,
            section_title=card.section_title,
        )
        if column_index == 0:
            right.append(placement)
        else:
            left.append(placement)
        placements.append(placement)
        used_height += additional

    flush_page()
    return LayoutPlan(
        pages=tuple(pages),
        placements=tuple(placements),
        input_count=len(inputs),
        estimated_content_pages=len(pages),
    )
