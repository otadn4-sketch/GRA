"""Safe removal helpers for generated bulletin artifacts."""

from __future__ import annotations

import shutil
from pathlib import Path


def bulletin_run_directory(output_root: Path, run_id: int) -> Path:
    """Return the only directory this feature is permitted to remove."""

    root = output_root.resolve()
    candidate = (root / f"run_{int(run_id)}").resolve()
    if candidate.parent != root:
        raise ValueError("مسیر خروجی اجرای بولتن نامعتبر است.")
    return candidate


def remove_bulletin_run_directory(output_root: Path, run_id: int) -> bool:
    """Remove generated files for one concrete run, never the output root."""

    directory = bulletin_run_directory(output_root, run_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory)
    return True
