from __future__ import annotations

"""Bridge the approved Garaye page layout into the application export flow."""

from dataclasses import dataclass
from pathlib import Path

from .bulletin_models import BulletinData

# This is the layout engine authored for the approved Garaye newsletter design.
# The dashboard only prepares editorial data; this secondary renderer makes the
# editable Word companion.  HTML/PDF are produced by html_layout_engine.
from tools.approved_layout import build_bulletin_docx
from tools.generate_approved_layout import load_items


@dataclass(frozen=True)
class ApprovedLayoutExport:
    files: dict[str, str]
    warnings: list[str]


class ApprovedLayoutExporter:
    def __init__(self, project_root: Path) -> None:
        self.reference_cover = (
            project_root / "assets" / "approved_layout" / "cover-v11_7.jpg"
        )

    def export(self, data: BulletinData, output_dir: Path) -> ApprovedLayoutExport:
        items, metadata, high_attention_topics = load_items(data.model_dump(mode="json"))
        if not items:
            raise RuntimeError("برای صفحه‌آرایی بولتن، خبر نهایی قابل انتشار وجود ندارد.")

        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "approved_layout_assets"
        docx_path = output_dir / "bulletin_page_layout.docx"
        build_bulletin_docx(
            items,
            metadata,
            docx_path,
            assets_dir,
            self.reference_cover,
            high_attention_topics=high_attention_topics,
        )

        return ApprovedLayoutExport(
            files={"layout_docx": str(docx_path)},
            warnings=[],
        )
