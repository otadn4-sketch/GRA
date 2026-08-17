"""Apply a new Garaye zip while the service stays up, then request a restart."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT_ROOT / "run"
RELOAD_FLAG = RUN_DIR / "reload.request"
STOP_FLAG = RUN_DIR / "stop.request"
PIP_FLAG = RUN_DIR / "pip.request"
UPDATE_LOG = RUN_DIR / "last-update.json"

ALLOWED_TOP_LEVEL = {
    "app",
    "web",
    "deploy",
    "VERSION",
    "requirements.txt",
}
FORBIDDEN_NAMES = {
    ".env",
    ".venv",
    "data",
    "backups",
    "run",
    "__pycache__",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".pid", ".log"}
MAX_ZIP_BYTES = 80 * 1024 * 1024
MAX_FILES = 4000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_version() -> str:
    version_file = PROJECT_ROOT / "VERSION"
    if version_file.is_file():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return __version__


def _is_forbidden(relative: Path) -> bool:
    parts = [part.lower() for part in relative.parts]
    if any(part in {name.lower() for name in FORBIDDEN_NAMES} for part in parts):
        return True
    if relative.name.lower() in {name.lower() for name in FORBIDDEN_NAMES}:
        return True
    suffix = relative.suffix.lower()
    name = relative.name.lower()
    if suffix in FORBIDDEN_SUFFIXES or name.endswith(".db-wal") or name.endswith(".db-shm"):
        return True
    return False


def _unwrap_root(names: list[str]) -> str:
    """Accept both a flat zip and a single wrapping folder from Compress-Archive."""
    cleaned = [name.replace("\\", "/") for name in names if name and not name.endswith("/")]
    if not cleaned:
        return ""
    tops = {item.split("/", 1)[0] for item in cleaned}
    if len(tops) == 1:
        wrapper = next(iter(tops))
        if wrapper not in ALLOWED_TOP_LEVEL:
            return wrapper + "/"
    return ""


def _safe_members(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path]]:
    prefix = _unwrap_root(archive.namelist())
    members: list[tuple[zipfile.ZipInfo, Path]] = []
    for info in archive.infolist():
        raw = info.filename.replace("\\", "/")
        if prefix and raw.startswith(prefix):
            raw = raw[len(prefix) :]
        if not raw or raw.endswith("/"):
            continue
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"مسیر نامعتبر داخل بسته به‌روزرسانی: {info.filename}")
        top = relative.parts[0] if relative.parts else ""
        if top not in ALLOWED_TOP_LEVEL:
            continue
        if _is_forbidden(relative):
            continue
        members.append((info, relative))
    if not members:
        raise ValueError(
            "بسته به‌روزرسانی باید شامل پوشه‌های app یا web یا فایل‌های VERSION و requirements.txt باشد."
        )
    if len(members) > MAX_FILES:
        raise ValueError("تعداد فایل‌های بسته به‌روزرسانی بیش از حد مجاز است.")
    return members


def apply_update_zip(content: bytes, *, actor: str) -> dict[str, Any]:
    if not content:
        raise ValueError("فایل به‌روزرسانی خالی است.")
    if len(content) > MAX_ZIP_BYTES:
        raise ValueError("حجم بسته به‌روزرسانی باید کمتر از ۸۰ مگابایت باشد.")
    digest = hashlib.sha256(content).hexdigest()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    with tempfile.TemporaryDirectory(prefix="garaye-update-") as raw_tmp:
        tmp = Path(raw_tmp)
        zip_path = tmp / "update.zip"
        zip_path.write_bytes(content)
        try:
            archive = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile as exc:
            raise ValueError("فایل انتخاب‌شده یک ZIP معتبر نیست.") from exc
        with archive:
            members = _safe_members(archive)
            extracted = tmp / "extracted"
            extracted.mkdir()
            for info, relative in members:
                target = extracted / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
            for info, relative in members:
                source = extracted / relative
                destination = PROJECT_ROOT / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied.append(str(relative).replace("\\", "/"))
    version = current_version()
    payload = {
        "ok": True,
        "applied_at": _utc_now(),
        "actor": actor,
        "sha256": digest,
        "file_count": len(copied),
        "files": copied[:80],
        "version": version,
        "requirements_changed": "requirements.txt" in copied,
        "python_restart_requested": any(
            path.startswith("app/") or path == "requirements.txt" for path in copied
        ),
        "web_updated": any(path.startswith("web/") for path in copied),
    }
    if payload["requirements_changed"]:
        PIP_FLAG.write_text(_utc_now(), encoding="utf-8")
    UPDATE_LOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def request_reload(*, delay_seconds: float = 1.4) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    RELOAD_FLAG.write_text(_utc_now(), encoding="utf-8")
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()

    def _exit_after_delay() -> None:
        time.sleep(max(0.4, delay_seconds))
        os._exit(0)

    threading.Thread(target=_exit_after_delay, name="garaye-reload", daemon=True).start()


def last_update_status() -> dict[str, Any]:
    if not UPDATE_LOG.is_file():
        return {"ok": True, "applied": False, "version": current_version()}
    try:
        payload = json.loads(UPDATE_LOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload["ok"] = True
    payload["applied"] = True
    payload["version"] = current_version()
    payload["reload_pending"] = RELOAD_FLAG.is_file()
    return payload
