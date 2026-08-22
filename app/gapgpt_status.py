from __future__ import annotations

from typing import Any

import httpx

GAPGPT_STATUS_PAGE = "https://status.gapgpt.app/status"
GAPGPT_PROBES_URL = "https://status.gapgpt.app/status/probes.json"
GAPGPT_HISTORY_DAYS = 14

GAPGPT_GROUPS: list[dict[str, Any]] = [
    {
        "id": "apis",
        "name": "APIها",
        "description": "همه سرویس‌های API در پلتفرم گپ جی‌پی‌تی و ارائه‌دهنده‌های متصل",
        "children": [
            {
                "id": "api-openai",
                "name": "OpenAI",
                "description": "پایش درخواست‌های سازگار با OpenAI در API گپ جی‌پی‌تی",
                "probes": ["openai-chat-completions", "openai-image-generation", "openai-embeddings"],
            },
            {
                "id": "api-google",
                "name": "Google",
                "description": "پایش مدل‌ها و قابلیت‌های Google در مسیرهای API گپ جی‌پی‌تی",
                "probes": ["google-chat-completions", "google-image-generation"],
            },
            {
                "id": "api-grok",
                "name": "Grok",
                "description": "پایش مدل‌های Grok که از طریق API گپ جی‌پی‌تی ارائه می‌شوند",
                "probes": ["grok-chat-completions"],
            },
            {
                "id": "api-qwen",
                "name": "Qwen",
                "description": "پایش مدل‌های Qwen در سرویس‌های API گپ جی‌پی‌تی",
                "probes": ["qwen-chat-completions"],
            },
            {
                "id": "api-claude",
                "name": "Claude",
                "description": "پایش مدل‌های Claude که از طریق API گپ جی‌پی‌تی در دسترس‌اند",
                "probes": ["claude-chat-completions"],
            },
            {
                "id": "api-gapgpt-hosted",
                "name": "GapGPT",
                "description": "مدل‌هایی که مستقیم توسط زیرساخت GapGPT میزبانی می‌شوند",
                "probes": [
                    "gapgpt-hosted-qwen-3-6",
                    "gapgpt-hosted-qwen-3-5",
                    "gapgpt-hosted-z-image",
                ],
            },
            {
                "id": "api-platform",
                "name": "پنل توسعه‌دهندگان",
                "description": "کلیدهای کاربر و اعتبار مصرفی در پلتفرم API گپ جی‌پی‌تی",
                "probes": ["gapapi-token-list", "gapapi-quota"],
            },
        ],
    },
    {
        "id": "gapgpt-app",
        "name": "گپ جی‌پی‌تی",
        "description": "اپلیکیشن اصلی، احراز هویت و مسیرهای مکالمه",
        "probes": [
            "gapgpt-profile",
            "gapgpt-chat-list",
            "gapgpt-send-receive-message",
        ],
    },
    {
        "id": "gapcode",
        "name": "گپ‌کد",
        "description": "دسترسی گپ‌کد، اجرای دستور نمونه و رابط‌های محیط کدنویسی",
        "probes": ["gapcode-command", "gapcode-models", "gapcode-usage"],
    },
]


def _rank_status(value: str) -> int:
    order = {"operational": 0, "unknown": 1, "degraded": 2, "incident": 3}
    return order.get(str(value or "unknown"), 1)


def _worst_status(values: list[str]) -> str:
    if not values:
        return "unknown"
    return max(values, key=_rank_status)


def _summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    history = list(probe.get("history") or [])[-GAPGPT_HISTORY_DAYS:]
    return {
        "id": probe.get("id"),
        "name": probe.get("name") or probe.get("id"),
        "status": probe.get("status") or "unknown",
        "title": probe.get("title"),
        "latency_ms": probe.get("latency_ms"),
        "checked_at": probe.get("checked_at"),
        "history": [
            {
                "date": row.get("date"),
                "status": row.get("status") or "unknown",
                "uptime_percent": row.get("uptime_percent"),
            }
            for row in history
            if isinstance(row, dict)
        ],
    }


def _attach_probes(
    ids: list[str],
    probes: dict[str, dict[str, Any]],
    used: set[str],
) -> list[dict[str, Any]]:
    items = []
    for probe_id in ids:
        used.add(probe_id)
        raw = probes.get(probe_id)
        if raw:
            items.append(_summarize_probe(raw))
        else:
            items.append(
                {
                    "id": probe_id,
                    "name": probe_id,
                    "status": "unknown",
                    "title": "در فهرست پایش فعلی نیست",
                    "latency_ms": None,
                    "checked_at": None,
                    "history": [],
                }
            )
    return items


def shape_gapgpt_status(payload: dict[str, Any]) -> dict[str, Any]:
    probes = {
        str(key): value
        for key, value in dict(payload.get("probes") or {}).items()
        if isinstance(value, dict)
    }
    used: set[str] = set()
    groups = []
    for group in GAPGPT_GROUPS:
        children = []
        child_statuses: list[str] = []
        if group.get("children"):
            for child in group["children"]:
                items = _attach_probes(list(child.get("probes") or []), probes, used)
                status = _worst_status([item["status"] for item in items])
                child_statuses.append(status)
                children.append(
                    {
                        "id": child["id"],
                        "name": child["name"],
                        "description": child.get("description") or "",
                        "status": status,
                        "probes": items,
                    }
                )
            group_items: list[dict[str, Any]] = []
            group_status = _worst_status(child_statuses)
        else:
            group_items = _attach_probes(list(group.get("probes") or []), probes, used)
            group_status = _worst_status([item["status"] for item in group_items])
            children = []
        groups.append(
            {
                "id": group["id"],
                "name": group["name"],
                "description": group.get("description") or "",
                "status": group_status,
                "children": children,
                "probes": group_items if not children else [],
            }
        )
    leftover_ids = [key for key in probes if key not in used]
    if leftover_ids:
        leftover = _attach_probes(leftover_ids, probes, used)
        groups.append(
            {
                "id": "other",
                "name": "سایر سرویس‌ها",
                "description": "پایش‌هایی که در فهرست اصلی صفحه وضعیت نبودند",
                "status": _worst_status([item["status"] for item in leftover]),
                "children": [],
                "probes": leftover,
            }
        )
    overall = _worst_status([group["status"] for group in groups])
    return {
        "ok": True,
        "source": GAPGPT_STATUS_PAGE,
        "generated_at": payload.get("generated_at"),
        "window_days": payload.get("window_days") or GAPGPT_HISTORY_DAYS,
        "overall_status": overall,
        "groups": groups,
    }


async def fetch_gapgpt_status() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
            response = await client.get(
                GAPGPT_PROBES_URL,
                headers={"User-Agent": "GarayeStatusMonitor/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "source": GAPGPT_STATUS_PAGE,
            "error": str(exc),
            "overall_status": "unknown",
            "groups": [],
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "source": GAPGPT_STATUS_PAGE,
            "error": "پاسخ صفحه وضعیت قابل‌خواندن نبود.",
            "overall_status": "unknown",
            "groups": [],
        }
    return shape_gapgpt_status(payload)
