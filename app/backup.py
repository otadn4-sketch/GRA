from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def create_backup(database_path: Path, backup_dir: Path) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"garaye-{stamp}.sqlite3"
    with sqlite3.connect(database_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = destination.with_suffix(".json")
    manifest.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "database_backup": str(destination.resolve()),
                "sha256": digest,
                "size": destination.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "database_backup": str(destination.resolve()),
        "manifest": str(manifest.resolve()),
        "sha256": digest,
        "size": destination.stat().st_size,
    }


def prune_backups(backup_dir: Path, *, keep: int = 30) -> int:
    files = sorted(backup_dir.glob("garaye-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[max(1, keep) :]:
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
        removed += 1
    return removed
