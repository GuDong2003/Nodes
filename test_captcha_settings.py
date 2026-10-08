"""Captcha settings must keep provider endpoints aligned without losing local config."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app as target


PROVIDERS = {
    "2captcha": "https://api.2captcha.com",
    "yescaptcha": "https://api.yescaptcha.com",
    "capmonster": "https://api.capmonster.cloud",
}


class CaptchaSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config_file = Path(directory.name) / "config.local.json"
        self.initial = {
            "mail_provider": "cfmail",
            "mail_api_base": "https://mail.example.com",
            "mail_domain": "example.com",
            "mail_api_key": "private-mail-key",
            "captcha_provider": "browser",
            "captcha_api_base": "https://api.2captcha.com",
            "proxy_quality_enabled": False,
            "quality_profiles": {"custom": {"proxy_max_latency_ms": 1234}},
            "pool_expected_proxies_per_account": 100,
            "resin_gateway_platform": "Nodes",
            "export_token": "private-export-token",
        }
        self.config_file.write_text(json.dumps(self.initial), encoding="utf-8")
        for item in (
            patch.object(target, "CONFIG_FILE", self.config_file),
            patch.object(target.TASK_STORE, "active", return_value=None),
            patch.object(target.worker, "reload_settings"),
            patch.dict(target.app.config, TESTING=True, SESSION_COOKIE_SECURE=False),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.client = target.app.test_client()
        with self.client.session_transaction() as session:
            session.update(authenticated=True, username=target.WEB_USERNAME, csrf_token="test-csrf")

    def save(self, provider, base, key="test-client-key"):
        return self.client.put(
            "/api/settings",
            json={"captcha_provider": provider, "captcha_api_base": base, "captcha_api_key": key},
            headers={"X-CSRF-Token": "test-csrf"},
        )

    def test_blank_endpoint_saves_provider_default(self):
        # Missing provider validation/defaulting must reject the request or save the wrong host.
        for provider, endpoint in PROVIDERS.items():
            with self.subTest(provider=provider):
                response = self.save(provider, "")
                self.assertEqual(response.status_code, 200, response.json)
                saved = json.loads(self.config_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["captcha_provider"], provider)
                self.assertEqual(saved["captcha_api_base"], endpoint)
                self.assertEqual(response.json["settings"]["captcha_api_base"], endpoint)

    def test_cached_frontend_cannot_save_another_providers_endpoint(self):
        for provider, endpoint in PROVIDERS.items():
            for old_endpoint in PROVIDERS.values():
                if old_endpoint == endpoint:
                    continue
                with self.subTest(provider=provider, old_endpoint=old_endpoint):
                    response = self.save(provider, old_endpoint + "/")
                    self.assertEqual(response.status_code, 200, response.json)
                    self.assertEqual(response.json["settings"]["captcha_api_base"], endpoint)

    def test_custom_compatible_endpoint_is_preserved(self):
        for provider in ("yescaptcha", "capmonster"):
            with self.subTest(provider=provider):
                response = self.save(provider, "https://solver.example.com/api/v2/")
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json["settings"]["captcha_api_base"], "https://solver.example.com/api/v2")

    def test_new_providers_require_a_key_without_writing_config(self):
        before = self.config_file.read_bytes()
        for provider in ("yescaptcha", "capmonster"):
            with self.subTest(provider=provider):
                response = self.save(provider, "", key="")
                self.assertEqual(response.status_code, 400)
                self.assertIn("API Key", response.json["error"])
                self.assertEqual(self.config_file.read_bytes(), before)

    def test_switching_provider_preserves_mail_export_resin_and_quality_settings(self):
        response = self.save("capmonster", "https://api.yescaptcha.com")
        self.assertEqual(response.status_code, 200, response.json)
        saved = json.loads(self.config_file.read_text(encoding="utf-8"))
        for key, value in self.initial.items():
            if not key.startswith("captcha_"):
                self.assertEqual(saved[key], value, key)


if __name__ == "__main__":
    unittest.main()
