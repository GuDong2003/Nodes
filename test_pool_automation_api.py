import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import web_app as web

REAL_LIST_PROXY_HOSTS = web.worker.list_proxy_hosts
REAL_FETCH_OVERVIEW = web.worker.fetch_service_overview
REAL_FETCH_SUMMARY = web.worker.fetch_accounts_summary


class AutomationApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("account", "node", "web"):
            (self.root / name).mkdir()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"pool_auto_register": False, "mail_provider": "cfmail",
                                          "mail_api_key": "keep-mail-secret", "export_token": "keep-export",
                                          "proxy_quality_enabled": False}))
        self.store = web.TaskStore(self.root / "tasks.json")
        for item in (
            patch.object(web, "ACCOUNT_DIR", self.root / "account"),
            patch.object(web, "NODE_DIR", self.root / "node"),
            patch.object(web, "WEB_DATA_DIR", self.root / "web"),
            patch.object(web, "CONFIG_FILE", self.config),
            patch.object(web, "TASK_STORE", self.store),
            patch.dict(web.app.config, TESTING=True, SESSION_COOKIE_SECURE=False),
            patch.dict(web.os.environ, NODES_DISABLE_POOL_LOOP="0"),
        ):
            self.enterContext(item)
        self.register = self.enterContext(patch.object(web.worker, "register_one", return_value={"proxy_count": 100}))
        self.overview = self.enterContext(patch.object(web.worker, "fetch_service_overview", side_effect=self.fresh_overview))
        self.summary = self.enterContext(patch.object(web.worker, "fetch_accounts_summary", return_value=[]))
        self.enterContext(patch.object(web.worker, "list_proxy_hosts", return_value=["node.example.com:80"]))
        self.client = web.app.test_client()
        with self.client.session_transaction() as session:
            session.update(authenticated=True, username=web.WEB_USERNAME, csrf_token="csrf")
        self.headers = {"X-CSRF-Token": "csrf"}
        self.addCleanup(self.wait_idle)

    def wait_idle(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            running = getattr(web, "POOL_AUTOMATION", None)
            if not self.store.active() and (running is None or not running.status()["running"]):
                return
            time.sleep(0.01)
        self.fail("background work did not finish")

    def fresh_overview(self, token, account_id, **options):
        return {"bandwidth": 10_000_000_000, "bandwidth_used": 8_000_000_000,
                "services": {"datacenter_shared": {"proxy_username": "user", "proxy_password": "proxy-secret",
                "expiration_time": int(time.time()) + 7 * 86400, "proxy_amount": 1}}}

    def add_account(self):
        record = {"email": "one@example.com", "account_id": "one", "access_token": "private-access",
                  "proxy_username": "user", "proxy_password": "proxy-secret", "proxy_ips": ["old.example.com:80"],
                  "expiration_time": int(time.time()) + 86400, "bandwidth_remaining": 1_000_000_000,
                  "ts": 1, "notes": "original"}
        (self.root / "account" / "accounts.jsonl").write_text(json.dumps(record) + "\n")
        return record

    def save(self, **payload):
        return self.client.put("/api/pool/automation", json=payload, headers=self.headers)

    def check(self):
        response = self.client.post("/api/pool/automation/check", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.json)
        self.wait_idle()
        return self.client.get("/api/pool/automation").json

    def test_read_requires_login_and_never_probes_or_registers(self):
        anonymous = web.app.test_client()
        self.assertEqual(anonymous.get("/api/pool/automation").status_code, 401)
        self.add_account()
        response = self.client.get("/api/pool/automation")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["settings"]["enabled"])
        self.assertEqual(response.json["capacity"]["bandwidth_remaining"], 1_000_000_000)
        self.assertEqual(self.store.list(), [])
        self.overview.assert_not_called()
        self.assertNotIn("private-access", response.get_data(as_text=True))

    def test_settings_save_is_private_validated_and_preserves_other_configuration(self):
        response = self.save(enabled=True, target_nodes=0, target_bandwidth_gb=2.5, interval_minutes=15, max_register_per_round=2)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["settings"]["target_bandwidth_gb"], 2.5)
        self.assertIsNotNone(response.json["status"]["next_check_at"])
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["mail_api_key"], "keep-mail-secret")
        self.assertEqual(saved["export_token"], "keep-export")
        self.assertFalse(saved["proxy_quality_enabled"])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.list(), [])

    def test_bad_settings_and_missing_csrf_do_not_write(self):
        before = self.config.read_bytes()
        for payload in ({"enabled": True, "target_nodes": 0, "target_bandwidth_gb": 0},
                        {"target_nodes": -1}, {"target_nodes": 1.5}, {"target_nodes": True},
                        {"target_bandwidth_gb": "NaN"}, {"interval_minutes": 0},
                        {"max_register_per_round": 6}, {"enabled": "true"}):
            with self.subTest(payload=payload):
                response = self.save(**payload)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.client.put("/api/pool/automation", json={"enabled": False}).status_code, 403)

    def test_worker_kill_switch_cannot_silently_enable_automation(self):
        with patch.dict(web.os.environ, NODES_DISABLE_POOL_LOOP="1"):
            response = self.save(enabled=True, target_nodes=100)
            self.assertEqual(response.status_code, 409)
            self.assertFalse(self.client.get("/api/pool/automation").json["status"]["worker_enabled"])

    def test_fresh_sync_satisfies_bandwidth_before_any_registration(self):
        self.add_account()
        self.assertEqual(self.save(enabled=True, target_nodes=0, target_bandwidth_gb=1.5).status_code, 200)
        result = self.check()
        self.assertEqual(result["status"]["outcome"], "satisfied")
        self.assertEqual(result["capacity"]["bandwidth_remaining"], 2_000_000_000)
        self.assertEqual(self.store.list(), [])

    def test_bandwidth_shortage_creates_a_real_task_with_bounded_count(self):
        self.add_account()
        self.assertEqual(self.save(enabled=True, target_nodes=0, target_bandwidth_gb=3).status_code, 200)
        result = self.check()
        self.assertEqual(result["status"]["outcome"], "registration_started")
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.list()[0]["requested"], 1)

    def test_failed_sync_keeps_data_and_does_not_register(self):
        self.add_account()
        self.assertEqual(self.save(enabled=True, target_nodes=100).status_code, 200)
        self.overview.side_effect = ConnectionError("upstream unavailable")
        before = (self.root / "account" / "accounts.jsonl").read_bytes()
        result = self.check()
        self.assertEqual(result["status"]["outcome"], "sync_failed")
        self.assertEqual(self.store.list(), [])
        self.assertEqual((self.root / "account" / "accounts.jsonl").read_bytes(), before)

    def test_account_deleted_during_sync_is_not_restored(self):
        self.add_account()
        def delete_then_return(token, account_id):
            web._delete_accounts(["one@example.com"])
            return self.fresh_overview(token, account_id)
        self.overview.side_effect = delete_then_return
        response = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/accounts").json["accounts"], [])

    def test_synced_empty_node_list_does_not_reuse_historical_hosts(self):
        self.add_account()
        (self.root / "node" / "old.txt").write_text("http://user:proxy-secret@old.example.com:80\n")
        with patch.object(web.worker, "list_proxy_hosts", return_value=[]):
            response = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(web._pool_snapshot()[2]["live_slots"], 0)

    def test_real_empty_upstream_response_clears_old_nodes(self):
        self.add_account()
        (self.root / "node" / "old.txt").write_text("http://user:proxy-secret@old.example.com:80\n")
        response = Mock(text="", status_code=200)
        with patch.object(web.worker, "list_proxy_hosts", REAL_LIST_PROXY_HOSTS), \
             patch.object(web.worker.requests, "get", return_value=response):
            synced = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
        self.assertEqual(synced.status_code, 200)
        self.assertEqual(synced.json["failed"], [])
        self.assertEqual(web._pool_snapshot()[2]["live_slots"], 0)

    def test_registration_still_rejects_an_empty_upstream_node_list(self):
        with patch.object(web.worker.requests, "get", return_value=Mock(text="", status_code=200)):
            with self.assertRaisesRegex(RuntimeError, "为空"):
                REAL_LIST_PROXY_HOSTS("fixture-token", "fixture-account")

    def test_malformed_success_response_is_not_treated_as_empty_capacity(self):
        for text in ("<html>upstream unavailable</html>", '{"error":"temporarily unavailable"}'):
            with self.subTest(text=text), \
                 patch.object(web.worker.requests, "get", return_value=Mock(text=text, status_code=200)):
                with self.assertRaises(RuntimeError):
                    REAL_LIST_PROXY_HOSTS("fixture-token", "fixture-account", allow_empty=True)

    def test_edits_during_sync_are_preserved(self):
        self.add_account()
        def edit_then_return(token, account_id):
            record = web._find_account("one@example.com")
            record["notes"] = "new user edit"
            web._save_account_record(record)
            return self.fresh_overview(token, account_id)
        self.overview.side_effect = edit_then_return
        response = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        record = web._find_account("one@example.com")
        self.assertEqual(record["notes"], "new user edit")
        self.assertEqual(record["bandwidth_remaining"], 2_000_000_000)

    def test_manual_sync_and_automatic_check_do_not_overlap(self):
        self.add_account()
        entered, release = threading.Event(), threading.Event()
        def delayed(token, account_id):
            entered.set()
            release.wait(3)
            return self.fresh_overview(token, account_id)
        self.overview.side_effect = delayed
        response = self.client.post("/api/pool/automation/check", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(entered.wait(2))
        try:
            duplicate = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
            self.assertEqual(duplicate.status_code, 409)
        finally:
            release.set()

    def test_persistent_api_key_syncs_when_dashboard_login_has_expired(self):
        record = self.add_account()
        record["api_key"] = {"token": "persistent-fixture-key"}
        web._save_account_record(record)
        calls = []
        def upstream_get(url, **kwargs):
            calls.append((url, kwargs.get("headers", {})))
            response = web.requests.Response()
            response.url = url
            response.status_code = 200 if url.startswith("https://api.proxyscrape.com/v4/") else 401
            if url.endswith("services/overview"):
                data = {"data": self.fresh_overview("", "one")}
            elif url.endswith("accounts-summary"):
                data = {"success": True, "data": [{"id": "one", "status": "valid"}]}
            else:
                response._content = b"node.example.com:80\n"
                return response
            response._content = json.dumps(data).encode()
            return response
        with patch.object(web.worker, "fetch_service_overview", REAL_FETCH_OVERVIEW), \
             patch.object(web.worker, "fetch_accounts_summary", REAL_FETCH_SUMMARY), \
             patch.object(web.worker, "list_proxy_hosts", REAL_LIST_PROXY_HOSTS), \
             patch.object(web.worker.requests, "get", side_effect=upstream_get), \
             patch.object(web.worker.requests.Session, "get", side_effect=upstream_get), \
             patch.object(web.worker.time, "sleep"):
            response = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
        self.assertEqual(response.json["failed"], [])
        self.assertEqual(response.json["synced"], ["one@example.com"])
        self.assertEqual(web._find_account("one@example.com")["bandwidth_remaining"], 2_000_000_000)
        self.assertEqual(len(calls), 3)
        for url, headers in calls:
            self.assertTrue(url.startswith("https://api.proxyscrape.com/v4/"))
            self.assertEqual(headers.get("api-token"), "persistent-fixture-key")
            self.assertNotIn("Authorization", headers)

    def test_api_key_only_accounts_can_sync_without_dashboard_access_token(self):
        record = self.add_account()
        record.pop("access_token")
        record["api_key"] = {"token": "persistent-fixture-key"}
        (self.root / "account" / "accounts.jsonl").write_text(json.dumps(record) + "\n")
        response = self.client.post("/api/accounts/sync-usage", json={}, headers=self.headers)
        self.assertEqual(response.json["failed"], [])
        self.assertEqual(web._find_account("one@example.com")["bandwidth_remaining"], 2_000_000_000)


if __name__ == "__main__":
    unittest.main()
