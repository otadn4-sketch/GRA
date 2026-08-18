from __future__ import annotations

"""Bridge the approved Garaye page layout into the application export flow."""

from dataclasses import dataclass
from pathlib import Path

from .bulletin_models import BulletinData
from .word_layout_engine import build_layout_docx


@dataclass(frozen=True)
class ApprovedLayoutExport:
    files: dict[str, str]
    warnings: list[str]


class ApprovedLayoutExporter:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def export(self, data: BulletinData, output_dir: Path) -> ApprovedLayoutExport:
        output_dir.mkdir(parents=True, exist_ok=True)
        docx_path = output_dir / "bulletin_page_layout.docx"
        warnings = build_layout_docx(data, docx_path)
        return ApprovedLayoutExport(
            files={"layout_docx": str(docx_path)},
            warnings=warnings,
        )
