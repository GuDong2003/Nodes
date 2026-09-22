"""Public deployment boundaries: no network calls or real registrations."""

import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app as target


class PublicDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("ACCOUNT_DIR", "NODE_DIR", "WEB_DATA_DIR"):
            directory = self.root / name.lower()
            directory.mkdir()
            self.enterContext(patch.object(target, name, directory))
        self.enterContext(patch.dict(target.app.config, TESTING=True, SESSION_COOKIE_SECURE=False))
        self.enterContext(patch.object(target, "CONFIG_FILE", self.root / "config.local.json"))
        self.client = target.app.test_client()

    def authenticate(self):
        with self.client.session_transaction() as session:
            session.update(authenticated=True, username=target.WEB_USERNAME, csrf_token="csrf")

    def seed_account(self):
        email = "owner@example.com/api/export/clash"
        record = {"email": email, "password": "private-fixture", "ts": 1}
        (target.ACCOUNT_DIR / "accounts_test.jsonl").write_text(json.dumps(record) + "\n")
        return email

    def test_export_substring_does_not_bypass_account_login(self):
        email = self.seed_account()
        response = self.client.get("/api/accounts/" + email)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("private-fixture", response.get_data(as_text=True))

    def test_export_substring_does_not_bypass_csrf(self):
        email = self.seed_account()
        self.authenticate()
        response = self.client.put("/api/accounts/" + email, json={"password": "changed"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(target._find_account(email)["password"], "private-fixture")

    def test_subscription_internal_base_can_use_compose_service(self):
        target.CONFIG_FILE.write_text(json.dumps({
            "export_token": "fixture-token",
            "internal_base_url": "http://dashboard:8080/nodes/",
        }))
        self.authenticate()
        response = self.client.get("/api/pool")
        self.assertEqual(response.status_code, 200)
        data = response.json["pool"]
        self.assertEqual(data["subscription_url"],
                         "http://dashboard:8080/nodes/api/export/live-proxies?token=fixture-token")
        self.assertEqual(data["subscription_url_public"],
                         "http://localhost/api/export/live-proxies?token=fixture-token")

    def test_directory_mounted_config_supports_runtime_saves(self):
        self.authenticate()
        target.CONFIG_FILE.write_text(json.dumps({
            "captcha_provider": "browser", "mail_provider": "yyds",
            "pool_auto_register": False, "export_token": "unchanged-token",
        }))
        response = self.client.put("/api/settings", json={"yyds_domain": "example.com"},
                                   headers={"X-CSRF-Token": "csrf"})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(target.CONFIG_FILE.read_text())
        self.assertEqual(saved["yyds_domain"], "example.com")
        self.assertEqual(saved["export_token"], "unchanged-token")
        self.assertEqual(target.CONFIG_FILE.stat().st_mode & 0o777, 0o600)


class InitializeDeploymentTests(unittest.TestCase):
    def test_initialization_is_private_separate_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(__file__).parent / "deploy" / "initialize.py"
            command = [sys.executable, str(script), "--root", directory,
                       "--domain", "ps.example.com"]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            config_dir = Path(directory) / "data" / "config"
            access = json.loads((config_dir / "access.json").read_text())
            config = json.loads((config_dir / "config.local.json").read_text())
            self.assertFalse(config["pool_auto_register"])
            self.assertEqual(config["resin_gateway_host"], "ps.example.com")
            self.assertEqual(len({access["nodes_password"], access["resin_admin_token"],
                                  access["resin_proxy_token"], config["export_token"]}), 4)
            for name in ("config.local.json", "resin.env", "access.json"):
                self.assertEqual((config_dir / name).stat().st_mode & 0o777, 0o600)
            self.assertNotIn(access["nodes_password"], first.stdout + first.stderr)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads((config_dir / "access.json").read_text()), access)

    def test_initialization_refuses_partial_existing_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "data" / "config" / "config.local.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"fixture":"preserve"}')
            result = subprocess.run([sys.executable,
                                     str(Path(__file__).parent / "deploy" / "initialize.py"),
                                     "--root", directory, "--domain", "ps.example.com"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("partial", result.stderr.lower())
            self.assertEqual(config.read_text(), '{"fixture":"preserve"}')


if __name__ == "__main__":
    unittest.main()
