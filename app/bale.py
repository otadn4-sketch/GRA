from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx


class BaleAPIError(RuntimeError):
    def __init__(self, method: str, status_code: int | None, description: str) -> None:
        super().__init__(f"Bale API {method}: {description}")
        self.method = method
        self.status_code = status_code
        self.description = description

    @property
    def is_forbidden(self) -> bool:
        return self.status_code == 403


class BaleClient:
    """Small, token-safe async wrapper around Bale Bot API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://tapi.bale.ai",
        timeout: float = 45,
        retries: int = 3,
    ) -> None:
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.retries = max(1, retries)
        self._client = httpx.AsyncClient(timeout=timeout)
        self._file_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise BaleAPIError(method, None, "توکن بله تنظیم نشده و اتصال غیرفعال است.")
        url = f"{self.base_url}/bot{self.token}/{method}"
        last_error: BaleAPIError | None = None
        for attempt in range(self.retries):
            try:
                response = await self._client.post(url, json=payload or {})
                data = response.json()
                if response.is_success and bool(data.get("ok")):
                    return data.get("result")
                last_error = BaleAPIError(
                    method,
                    response.status_code,
                    str(data.get("description") or data.get("message") or response.text),
                )
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise last_error
            except (httpx.HTTPError, ValueError) as exc:
                last_error = BaleAPIError(method, None, str(exc))
            if attempt + 1 < self.retries:
                await asyncio.sleep(min(2**attempt, 5))
        raise last_error or BaleAPIError(method, None, "خطای نامشخص")

    async def get_updates(self, *, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return list(await self._call("getUpdates", payload) or [])

    async def get_chat_administrators(self, chat_id: int) -> list[dict[str, Any]]:
        return list(await self._call("getChatAdministrators", {"chat_id": chat_id}) or [])

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None, *, show_alert: bool = False) -> Any:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return await self._call("answerCallbackQuery", payload)

    async def answer_callback(
        self,
        callback_query_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> Any:
        return await self.answer_callback_query(
            callback_query_id,
            text,
            show_alert=show_alert,
        )

    async def get_file(self, file_id: str) -> dict[str, Any]:
        key = str(file_id or "").strip()
        if not key:
            raise BaleAPIError("getFile", None, "شناسه فایل خالی است.")
        now = time.monotonic()
        cached = self._file_cache.get(key)
        if cached and cached[0] > now:
            return dict(cached[1])
        expired = [cache_key for cache_key, (expires, _) in self._file_cache.items() if expires <= now]
        for cache_key in expired:
            self._file_cache.pop(cache_key, None)
        result = await self._get_file_uncached(key)
        file_path = bale_file_path(result)
        if not file_path:
            raise BaleAPIError("getFile", None, "مسیر فایل از بله برنگشت.")
        result["file_path"] = file_path
        if len(self._file_cache) >= 512:
            oldest = min(self._file_cache, key=lambda item: self._file_cache[item][0])
            self._file_cache.pop(oldest, None)
        self._file_cache[key] = (now + 50 * 60, result)
        return result

    async def _get_file_uncached(self, file_id: str) -> dict[str, Any]:
        last_error: BaleAPIError | None = None
        try:
            result = dict(await self._call("getFile", {"file_id": file_id}) or {})
            if bale_file_path(result):
                return result
            last_error = BaleAPIError("getFile", None, "مسیر فایل از بله برنگشت.")
        except BaleAPIError as exc:
            last_error = exc
        url = f"{self.base_url}/bot{self.token}/getFile"
        payload = {"file_id": file_id}
        for style in ("form", "get"):
            try:
                if style == "form":
                    response = await self._client.post(url, data=payload)
                else:
                    response = await self._client.get(url, params=payload)
                data = response.json()
                if response.is_success and bool(data.get("ok")):
                    result = dict(data.get("result") or {})
                    if bale_file_path(result):
                        return result
                    last_error = BaleAPIError("getFile", response.status_code, "مسیر فایل از بله برنگشت.")
                    continue
                last_error = BaleAPIError(
                    "getFile",
                    response.status_code,
                    str(data.get("description") or data.get("message") or response.text),
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                last_error = BaleAPIError("getFile", None, str(exc))
        raise last_error or BaleAPIError("getFile", None, "دریافت مسیر فایل ناموفق بود.")

    def file_download_url(self, file_path: str) -> str:
        return bale_file_download_url(self.base_url, self.token, file_path)

    async def download_file(self, file_path: str) -> tuple[bytes, str]:
        url = self.file_download_url(file_path)
        timeout = httpx.Timeout(20.0, read=180.0, write=30.0, pool=20.0)
        try:
            response = await self._client.get(url, follow_redirects=True, timeout=timeout)
        except httpx.HTTPError as exc:
            raise BaleAPIError("downloadFile", None, str(exc)) from exc
        if response.status_code >= 400:
            raise BaleAPIError(
                "downloadFile",
                response.status_code,
                (response.text or "")[:300] or f"HTTP {response.status_code}",
            )
        content_type = str(response.headers.get("content-type") or "").split(";")[0].strip()
        return response.content, content_type

    async def download_by_file_id(self, file_id: str) -> tuple[bytes, str, str]:
        info = await self.get_file(file_id)
        file_path = bale_file_path(info)
        payload, content_type = await self.download_file(file_path)
        return payload, content_type, file_path

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendMessage", {"chat_id": chat_id, "text": text, **_clean(kwargs)}))

    async def send_photo(self, chat_id: int, photo: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendPhoto", {"chat_id": chat_id, "photo": photo, **_clean(kwargs)}))

    async def send_video(self, chat_id: int, video: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendVideo", {"chat_id": chat_id, "video": video, **_clean(kwargs)}))

    async def send_document(self, chat_id: int, document: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendDocument", {"chat_id": chat_id, "document": document, **_clean(kwargs)}))

    async def send_audio(self, chat_id: int, audio: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendAudio", {"chat_id": chat_id, "audio": audio, **_clean(kwargs)}))

    async def send_voice(self, chat_id: int, voice: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendVoice", {"chat_id": chat_id, "voice": voice, **_clean(kwargs)}))

    async def send_animation(self, chat_id: int, animation: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendAnimation", {"chat_id": chat_id, "animation": animation, **_clean(kwargs)}))

    async def send_location(self, chat_id: int, latitude: float, longitude: float, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendLocation", {"chat_id": chat_id, "latitude": latitude, "longitude": longitude, **_clean(kwargs)}))

    async def send_contact(self, chat_id: int, phone_number: str, first_name: str, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("sendContact", {"chat_id": chat_id, "phone_number": phone_number, "first_name": first_name, **_clean(kwargs)}))

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("copyMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id, **_clean(kwargs)}))

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        return bool(await self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}))

    async def edit_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict[str, Any]) -> Any:
        return await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def bale_file_path(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, dict):
        return ""
    for key in ("file_path", "filePath"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    nested = result.get("file")
    if isinstance(nested, dict):
        return bale_file_path(nested)
    return ""


def bale_file_download_url(base_url: str, token: str, file_path: str) -> str:
    path = str(file_path or "").strip()
    if not path:
        raise BaleAPIError("getFile", None, "مسیر فایل نامعتبر است.")
    parsed = urlparse(path)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return path
    normalized = path.replace("\\", "/").lstrip("/")
    if ".." in normalized.split("/"):
        raise BaleAPIError("getFile", None, "مسیر فایل نامعتبر است.")
    prefix = f"file/bot{token}/"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix):]
    elif normalized.startswith("file/bot"):
        parts = [part for part in normalized.split("/") if part]
        rest = "/".join(quote(part, safe=".-_") for part in parts[2:])
        head = "/".join(parts[:2])
        return f"{base_url.rstrip('/')}/{head}/{rest}" if rest else f"{base_url.rstrip('/')}/{head}"
    encoded = "/".join(quote(part, safe=".-_") for part in normalized.split("/") if part)
    if not encoded:
        raise BaleAPIError("getFile", None, "مسیر فایل نامعتبر است.")
    return f"{base_url.rstrip('/')}/file/bot{token}/{encoded}"
