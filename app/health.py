"""Lightweight public health routes used by the Windows supervisor and Caddy probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import __version__

router = APIRouter()


def health_payload() -> dict[str, Any]:
    """Return a cheap liveness body. Do not touch the database or AI here."""

    return {
        "status": "ok",
        "ok": True,
        "version": __version__,
    }


@router.get("/health")
async def health() -> dict[str, Any]:
    return health_payload()
