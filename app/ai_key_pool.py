from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


class NoAvailableAPIKey(RuntimeError):
    pass


@dataclass(frozen=True)
class AIKeyLease:
    key: str
    slot: int


class AIKeyPool:
    def __init__(
        self,
        keys: tuple[str, ...] | list[str],
        *,
        max_concurrency: int = 3,
        concurrency_per_key: int = 1,
        circuit_breaker_seconds: int = 60,
    ) -> None:
        self._keys = tuple(dict.fromkeys(key for key in keys if key))
        self.max_concurrency = max(1, max_concurrency)
        self._global = asyncio.Semaphore(self.max_concurrency)
        self._per_key = [asyncio.Semaphore(max(1, concurrency_per_key)) for _ in self._keys]
        self._blocked_until = [0.0 for _ in self._keys]
        self._cursor = 0
        self._lock = asyncio.Lock()
        self._circuit_breaker_seconds = max(1, circuit_breaker_seconds)

    @property
    def configured(self) -> bool:
        return bool(self._keys)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    async def _choose(self) -> int:
        async with self._lock:
            now = time.monotonic()
            for step in range(len(self._keys)):
                slot = (self._cursor + step) % len(self._keys)
                if self._blocked_until[slot] <= now:
                    self._cursor = (slot + 1) % len(self._keys)
                    return slot
        raise NoAvailableAPIKey("تمام کلیدهای هوش موقتاً در وضعیت توقف هستند.")

    async def acquire(self) -> AIKeyLease:
        if not self._keys:
            raise NoAvailableAPIKey("کلید API هوش تنظیم نشده است.")
        await self._global.acquire()
        try:
            slot = await self._choose()
            await self._per_key[slot].acquire()
        except Exception:
            self._global.release()
            raise
        return AIKeyLease(key=self._keys[slot], slot=slot)

    def _release(self, lease: AIKeyLease) -> None:
        self._per_key[lease.slot].release()
        self._global.release()

    async def release_success(self, lease: AIKeyLease) -> None:
        self.report_success(lease.slot)
        self._release(lease)

    async def release_failure(
        self,
        lease: AIKeyLease,
        *,
        kind: str,
        reason: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        if kind in {"auth", "transient"}:
            delay = (
                max(1.0, float(retry_after_seconds))
                if retry_after_seconds is not None
                else float(self._circuit_breaker_seconds)
            )
            self._blocked_until[lease.slot] = time.monotonic() + delay
        self._release(lease)

    async def release_neutral(self, lease: AIKeyLease) -> None:
        self._release(lease)

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[AIKeyLease]:
        acquired = await self.acquire()
        try:
            yield acquired
        finally:
            await self.release_neutral(acquired)

    def report_failure(self, slot: int, *, retryable: bool = True) -> None:
        if retryable and 0 <= slot < len(self._blocked_until):
            self._blocked_until[slot] = time.monotonic() + self._circuit_breaker_seconds

    def report_success(self, slot: int) -> None:
        if 0 <= slot < len(self._blocked_until):
            self._blocked_until[slot] = 0.0

    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        return {
            "configured": self.configured,
            "key_count": self.key_count,
            "max_concurrency": self.max_concurrency,
            "blocked_slots": [
                index for index, until in enumerate(self._blocked_until) if until > now
            ],
        }
