"""Single rotating-file logging setup with secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable

from .config import Settings

_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*[:=]\s*.+"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"authorization|bearer|cookie)\b(\s*[=:]\s*|\s+is\s+)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+\S+")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:password|passwd|secret|token|api[_-]?key|access_token|refresh_token)=)[^&]*"
)

logger = logging.getLogger("prasad")


def collect_secret_values(settings: Settings | None = None) -> tuple[str, ...]:
    if settings is None:
        return ()
    raw: list[str] = [
        settings.token,
        settings.admin_password,
        settings.webhook_secret,
        settings.ai_api_key,
        *settings.ai_api_keys,
        *settings.editorial_ai_api_keys,
        settings.high_attention_ai_api_key,
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = str(value or "").strip()
        if len(text) < 8 or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return tuple(unique)


def redact_secrets(text: str, extra_secrets: Iterable[str] = ()) -> str:
    redacted = str(text or "")
    for secret in extra_secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    redacted = _HEADER_RE.sub(r"\1: ***", redacted)
    redacted = _ASSIGNMENT_RE.sub(r"\1\2***", redacted)
    redacted = _BEARER_RE.sub(r"\1 ***", redacted)
    redacted = _QUERY_SECRET_RE.sub(r"\1***", redacted)
    return redacted


class SecretRedactFilter(logging.Filter):
    def __init__(self, extra_secrets: Iterable[str] = ()) -> None:
        super().__init__(name="garaye-secret-redact")
        self.extra_secrets = tuple(extra_secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            record.msg = redact_secrets(rendered, self.extra_secrets)
            record.args = ()
            if record.exc_text:
                record.exc_text = redact_secrets(record.exc_text, self.extra_secrets)
        except Exception:
            return True
        return True


def log_dir_for(settings: Settings) -> Path:
    path = settings.database_path.parent / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(settings: Settings) -> Path:
    """Attach one rotating file handler to the root logger. Safe to call once."""

    global _CONFIGURED
    directory = log_dir_for(settings)
    level = getattr(logging, settings.log_level, logging.INFO)
    secrets = collect_secret_values(settings)
    redact_filter = SecretRedactFilter(secrets)
    formatter = logging.Formatter(_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler) for handler in root.handlers):
        logging.basicConfig(level=level, format=_LOG_FORMAT)

    log_path = directory / "prasad.log"
    already = False
    for handler in root.handlers:
        handler.addFilter(redact_filter)
        handler.setFormatter(formatter)
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(getattr(handler, "baseFilename", "")).resolve() == log_path.resolve():
                    already = True
            except OSError:
                pass

    if not already:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redact_filter)
        root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _CONFIGURED = True
    return directory


def asyncio_exception_handler(loop: Any, context: dict[str, Any]) -> None:
    message = str(context.get("message") or "Unhandled asyncio exception")
    exception = context.get("exception")
    if exception:
        logger.error("Background task error: %s", message, exc_info=exception)
    else:
        logger.error("Background task error: %s", redact_secrets(message))


def observe_background_task(task: Any) -> Any:
    """Log unhandled exceptions from create_task() without changing task behaviour."""

    def _done(done: Any) -> None:
        try:
            if done.cancelled():
                return
            exc = done.exception()
        except Exception:
            return
        if exc:
            logger.error(
                "Background task %s failed",
                getattr(done, "get_name", lambda: "task")(),
                exc_info=exc,
            )

    task.add_done_callback(_done)
    return task
