"""The pool switch selects an authenticated internal proxy without altering manual settings."""

import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import getproxies_environment, proxy_bypass_environment

import requests
import proxyscrape_register as worker
import web_app as web


class ProxyReuseEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "NODES_RESIN_PROXY_URL": "http://resin:8970",
            "RESIN_ENV_FILE": "/nonexistent-resin-fixture",
        }, clear=True))
        # On macOS urllib otherwise falls back to the user's system-wide proxy.
        self.enterContext(patch.object(requests.utils, "getproxies", getproxies_environment))
        self.enterContext(patch.object(requests.utils, "proxy_bypass", proxy_bypass_environment))
        self.config = {
            "proxy_use_pool": True, "proxy_enabled": False,
            "http_proxy": "http://manual:pw@manual.example:3128",
            "https_proxy": "http://secure:pw@manual.example:3129",
            "no_proxy": "custom.internal",
            "internal_base_url": "http://nodes-api:8080/nodes",
            "resin_gateway_host": "public.example", "resin_gateway_platform": "Nodes",
            "resin_auth_version": "V1", "resin_proxy_token": "proxy:token",
        }
        self.enterContext(patch.object(worker, "_LOCAL_CONFIG", self.config))

    def test_switch_routes_both_protocols_without_manual_enable(self):
        before = copy.deepcopy(self.config)
        worker._apply_proxy_env()
        proxies = requests.utils.get_environ_proxies("https://upstream.example")
        expected = "http://Nodes.nodes-ops:proxy%3Atoken@resin:8970"
        self.assertEqual(proxies.get("http"), expected)
        self.assertEqual(proxies.get("https"), expected)
        self.assertEqual(self.config, before)

    def test_internal_services_are_never_sent_through_pool(self):
        worker._apply_proxy_env()
        self.assertTrue(requests.utils.get_environ_proxies("https://upstream.example"))
        for url in ("http://127.0.0.1:8891/nodes", "http://localhost:9222", "http://[::1]:9222",
                    "http://dashboard:8080/nodes", "http://resin:2260/healthz",
                    "http://nodes-api:8080/nodes", "http://custom.internal"):
            with self.subTest(url=url):
                self.assertEqual(requests.utils.get_environ_proxies(url), {})

    def test_turning_off_restores_enabled_manual_proxy(self):
        self.config["proxy_enabled"] = True
        worker._apply_proxy_env()
        self.config["proxy_use_pool"] = False
        worker._apply_proxy_env()
        proxies = requests.utils.get_environ_proxies("https://upstream.example")
        self.assertEqual(proxies["http"], "http://manual:pw@manual.example:3128")
        self.assertEqual(proxies["https"], "http://secure:pw@manual.example:3129")

    def test_turning_off_without_manual_proxy_clears_generated_proxy(self):
        worker._apply_proxy_env()
        self.assertIn("HTTP_PROXY", os.environ)
        self.config["proxy_use_pool"] = False
        worker._apply_proxy_env()
        proxies = requests.utils.get_environ_proxies("https://upstream.example")
        self.assertNotIn("http", proxies)
        self.assertNotIn("https", proxies)

    def test_turning_off_restores_https_only_without_leaving_pool_http(self):
        self.config.update(proxy_enabled=True, http_proxy="")
        worker._apply_proxy_env()
        self.config["proxy_use_pool"] = False
        worker._apply_proxy_env()
        proxies = requests.utils.get_environ_proxies("https://upstream.example")
        self.assertNotIn("http", proxies)
        self.assertEqual(proxies["https"], "http://secure:pw@manual.example:3129")

    def test_missing_token_fails_instead_of_silently_using_direct_or_manual(self):
        self.config.pop("resin_proxy_token")
        with self.assertRaisesRegex(RuntimeError, "Resin"):
            worker._apply_proxy_env()


class ProxyReuseSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_file = root / "config.local.json"
        self.config = {
            "mail_provider": "yyds", "captcha_provider": "browser",
            "proxy_enabled": False, "http_proxy": "http://manual.example:3128",
            "https_proxy": "", "resin_proxy_token": "server-side-only-token",
        }
        self.config_file.write_text(json.dumps(self.config))
        account_dir = root / "account"
        node_dir = root / "node"
        account_dir.mkdir(); node_dir.mkdir()
        self.record_file = account_dir / "accounts_fixture.jsonl"
        self.record_file.write_text(json.dumps({
            "email": "owner@example.com", "proxy_username": "user", "proxy_password": "pw",
            "proxy_ips": ["10.1.1.1:80"], "expiration_time": int(time.time()) + 3600,
            "bandwidth_remaining": 1000000000,
        }) + "\n")
        for name, value in (("CONFIG_FILE", self.config_file), ("ACCOUNT_DIR", account_dir),
                            ("NODE_DIR", node_dir)):
            self.enterContext(patch.object(web, name, value))
        self.enterContext(patch.object(web.TASK_STORE, "active", return_value=None))
        # Global environment changes from reload are covered independently above.
        self.enterContext(patch.object(worker, "reload_settings"))
        self.enterContext(patch.dict(web.app.config, TESTING=True, SESSION_COOKIE_SECURE=False))
        self.enterContext(patch.dict(os.environ, {
            "NODES_RESIN_PROXY_URL": "http://resin:8970",
            "RESIN_ENV_FILE": "/nonexistent-resin-fixture",
        }, clear=True))
        self.client = web.app.test_client()
        with self.client.session_transaction() as session:
            session.update(authenticated=True, username=web.WEB_USERNAME, csrf_token="csrf")

    def toggle(self, value, csrf=True):
        return self.client.put("/api/settings", json={"proxy_use_pool": value},
                               headers={"X-CSRF-Token": "csrf"} if csrf else {})

    def test_switch_persists_without_replacing_manual_urls_or_returning_token(self):
        response = self.toggle(True)
        self.assertEqual(response.status_code, 200)
        saved = json.loads(self.config_file.read_text())
        self.assertTrue(saved["proxy_use_pool"])
        self.assertFalse(saved["proxy_enabled"])
        self.assertEqual(saved["http_proxy"], self.config["http_proxy"])
        self.assertEqual(saved["https_proxy"], "")
        self.assertNotIn("server-side-only-token", response.get_data(as_text=True))
        self.assertTrue(self.client.get("/api/settings").json["settings"]["proxy_use_pool"])
        self.assertEqual(self.client.get("/api/dashboard").json["chain"]["proxy"], "enabled")
        disabled = self.toggle(False)
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(json.loads(self.config_file.read_text())["proxy_use_pool"])

    def test_switch_requires_csrf(self):
        response = self.toggle(True, csrf=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(self.config_file.read_text()), self.config)

    def test_empty_pool_is_rejected_without_saving(self):
        self.record_file.write_text("")
        response = self.toggle(True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(self.config_file.read_text()), self.config)

    def test_missing_token_is_rejected_without_saving(self):
        self.config.pop("resin_proxy_token")
        self.config_file.write_text(json.dumps(self.config))
        response = self.toggle(True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(self.config_file.read_text()), self.config)


if __name__ == "__main__":
    unittest.main()
