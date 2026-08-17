from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class MiniAppAuthError(ValueError):
    pass


@dataclass(frozen=True)
class MiniAppIdentity:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    auth_date: int


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age: int = 900,
    now: int | None = None,
) -> MiniAppIdentity:
    """Validate Bale/Telegram-style MiniApp init data with an HMAC signature."""
    if not init_data or not bot_token:
        raise MiniAppAuthError("اطلاعات ورود MiniApp یا توکن ربات موجود نیست.")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    supplied_hash = pairs.pop("hash", "")
    if not supplied_hash:
        raise MiniAppAuthError("امضای MiniApp موجود نیست.")
    data_check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, supplied_hash):
        raise MiniAppAuthError("امضای MiniApp نامعتبر است.")
    try:
        auth_date = int(pairs.get("auth_date") or 0)
    except ValueError as exc:
        raise MiniAppAuthError("زمان احراز هویت نامعتبر است.") from exc
    current = int(time.time()) if now is None else int(now)
    if auth_date <= 0 or current - auth_date > max_age or auth_date - current > 60:
        raise MiniAppAuthError("مهلت اطلاعات ورود MiniApp پایان یافته است.")
    try:
        user = json.loads(pairs.get("user") or "{}")
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MiniAppAuthError("شناسه کاربر MiniApp معتبر نیست.") from exc
    return MiniAppIdentity(
        user_id=user_id,
        username=str(user.get("username")) if user.get("username") else None,
        first_name=str(user.get("first_name")) if user.get("first_name") else None,
        last_name=str(user.get("last_name")) if user.get("last_name") else None,
        auth_date=auth_date,
    )
