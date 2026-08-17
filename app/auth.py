from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Final


PBKDF2_ITERATIONS: Final = 390_000
VALID_ROLES: Final = {
    "superadmin",
    "newsletter_manager",
    "editor",
    "reviewer",
    "viewer",
}
ROLE_TITLES: Final = {
    "superadmin": "مدیرکل سامانه",
    "newsletter_manager": "مدیر خبرنامه",
    "editor": "سردبیر",
    "reviewer": "ارزیاب خبر",
    "viewer": "فقط‌خواندنی",
}
ROLE_PERMISSIONS: Final = {
    "superadmin": {"*"},
    "newsletter_manager": {
        "dashboard.view",
        "messages.view",
        "messages.review",
        "editorial.manage",
        "bulletins.manage",
        "bulletins.export",
        "people.manage",
        "sources.manage",
        "settings.manage",
        "system.manage",
    },
    "editor": {
        "dashboard.view",
        "messages.view",
        "messages.review",
        "editorial.manage",
        "bulletins.manage",
        "bulletins.export",
        "people.view",
        "sources.view",
    },
    "reviewer": {
        "dashboard.view",
        "messages.view",
        "messages.review",
        "editorial.view",
        "bulletins.view",
        "people.view",
        "sources.view",
    },
    "viewer": {
        "dashboard.view",
        "messages.view",
        "editorial.view",
        "bulletins.view",
        "people.view",
        "sources.view",
    },
}


@dataclass(frozen=True)
class AdminPrincipal:
    user_id: int | None
    username: str
    full_name: str
    role: str

    @property
    def permissions(self) -> set[str]:
        return set(ROLE_PERMISSIONS.get(self.role, set()))

    def can(self, permission: str) -> bool:
        values = self.permissions
        return "*" in values or permission in values


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("رمز عبور باید حداقل ۱۰ نویسه باشد.")
    salt = os.urandom(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(raw_iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
