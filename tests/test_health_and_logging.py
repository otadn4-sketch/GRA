from __future__ import annotations

import logging
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.health import health_payload, router
from app.logging_setup import SecretRedactFilter, redact_secrets


class HealthRouteTests(unittest.TestCase):
    def test_payload_is_lightweight(self) -> None:
        payload = health_payload()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["ok"])
        self.assertIn("version", payload)
        self.assertNotIn("database", payload)
        self.assertNotIn("ai_profiles", payload)

    def test_health_route_returns_200(self) -> None:
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["ok"])


class SecretRedactionTests(unittest.TestCase):
    def test_redacts_headers_tokens_and_passwords(self) -> None:
        extra = ("super-secret-token-value",)
        text = (
            "Authorization: Bearer abc.def\n"
            "Cookie: session=abc\n"
            "password=hunter2token\n"
            "token=xyz\n"
            "using super-secret-token-value in query?token=leak"
        )
        redacted = redact_secrets(text, extra)
        self.assertNotIn("abc.def", redacted)
        self.assertNotIn("session=abc", redacted)
        self.assertNotIn("super-secret-token-value", redacted)
        self.assertIn("***", redacted)

    def test_filter_scrubs_log_records(self) -> None:
        record = logging.LogRecord(
            name="prasad",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Authorization: Bearer leaked-token-value",
            args=(),
            exc_info=None,
        )
        self.assertTrue(SecretRedactFilter().filter(record))
        self.assertNotIn("leaked-token-value", record.getMessage())
        self.assertIn("***", record.getMessage())


if __name__ == "__main__":
    unittest.main()
