import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import web_app as target


class TaskDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.account_dir = root / "account"
        self.node_dir = root / "node"
        self.account_dir.mkdir()
        self.node_dir.mkdir()
        self.path = root / "tasks.json"
        self.store = target.TaskStore(self.path)
        for item in (
            patch.object(target, "TASK_STORE", self.store),
            patch.object(target, "ACCOUNT_DIR", self.account_dir),
            patch.object(target, "NODE_DIR", self.node_dir),
            patch.object(target.worker, "log"),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError(
                "unexpected network request in diagnostic tests")),
        ):
            item.start()
            self.addCleanup(item.stop)
        target.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = target.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["username"] = target.WEB_USERNAME
            session["csrf_token"] = "test-csrf"
        self.mailboxes = []

    def mailbox(self):
        self.mailboxes.append(1)
        return "private-mail@example.com", "private-mailbox-jwt"

    def start(self, count=1, concurrency=1, **options):
        return self.client.post(
            "/api/tasks",
            json={"count": count, "concurrency": concurrency, **options},
            headers={"X-CSRF-Token": "test-csrf"},
        )

    def finish(self, response):
        self.assertEqual(response.status_code, 202)
        task_id = response.json["task"]["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.store.get(task_id)
            if any(line.startswith("任务结束：") for line in task["logs"]):
                return task
            time.sleep(0.01)
        self.fail("test task did not finish")

    def test_captcha_failure_stage_survives_store_reload(self):
        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", side_effect=TimeoutError(
                 "等待页面/turnstile API 就绪超时")):
            task = self.finish(self.start(max_attempts=1))
        persisted = target.TaskStore(self.path).get(task["id"])
        logs = "\n".join(persisted["logs"])
        self.assertIn("TimeoutError", logs)
        self.assertIn("等待页面/turnstile API 就绪超时", logs)
        self.assertIn("验证码", logs)
        self.assertRegex(logs, r"\[\d\d:\d\d:\d\d\]")
        self.assertEqual(persisted["status"], "failed")

    def test_single_attempt_does_not_create_replacement_mailboxes(self):
        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", side_effect=TimeoutError()):
            task = self.finish(self.start(max_attempts=1))
        self.assertEqual(len(self.mailboxes), 1)
        self.assertEqual(task["max_attempts"], 1)
        self.assertEqual(task["completed"], 1)

    def test_default_still_allows_three_account_attempts(self):
        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", side_effect=TimeoutError()):
            task = self.finish(self.start())
        self.assertEqual(len(self.mailboxes), 3)
        self.assertEqual(task.get("max_attempts"), 3)

    def test_single_attempt_also_bounds_mailbox_request_retries(self):
        calls = []
        response = requests.Response()
        response.status_code = 429
        response.url = "https://mail.example.com/?token=private-query-token"

        def failing_request():
            calls.append(1)
            response.raise_for_status()

        def failing_mailbox():
            return target.worker._retry(failing_request, tries=3, delay=0)

        with patch.object(target.worker, "create_mailbox", failing_mailbox):
            task = self.finish(self.start(max_attempts=1))
        self.assertEqual(len(calls), 1)
        logs = "\n".join(task["logs"])
        self.assertIn("HTTP 429", logs)
        self.assertIn("邮箱", logs)
        self.assertNotIn("private-query-token", logs)

    def test_invalid_attempt_limits_do_not_start_tasks(self):
        for value in (0, 4, -1, 1.5, True, None, "bad"):
            with self.subTest(value=value):
                response = self.start(max_attempts=value)
                if response.status_code == 202:
                    self.finish(response)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.list(), [])

    def test_unknown_exception_and_raw_worker_logs_do_not_leak(self):
        secret = "password=private-pw access_token=private-access-token 123456"

        def failing_solver(**kwargs):
            target.worker.log(secret)
            raise RuntimeError(secret)

        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", failing_solver):
            task = self.finish(self.start(max_attempts=1))
        self.assertIn("RuntimeError", "\n".join(task["logs"]))
        saved = self.path.read_text(encoding="utf-8")
        for value in ("private-pw", "private-access-token", "123456",
                      "private-mail@example.com", "private-mailbox-jwt"):
            self.assertNotIn(value, saved)

    def test_uncaught_worker_error_is_safe_and_not_a_saved_account(self):
        with patch.object(target.worker, "register_one", side_effect=RuntimeError(
                "private-unhandled-secret")):
            task = self.finish(self.start(max_attempts=1))
        logs = "\n".join(task["logs"])
        self.assertIn("RuntimeError", logs)
        self.assertNotIn("private-unhandled-secret", logs)
        self.assertNotIn("已保留诊断记录", logs)

    def test_failed_task_does_not_claim_a_saved_account(self):
        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", side_effect=TimeoutError()):
            task = self.finish(self.start(max_attempts=1))
        self.assertEqual(task["failures"], 1)
        self.assertFalse((self.account_dir / task["account_file"]).exists())
        self.assertNotIn("已保留诊断记录", "\n".join(task["logs"]))

    def test_concurrent_failures_are_attributed_to_the_right_worker(self):
        barrier = threading.Barrier(2)
        response = requests.Response()
        response.status_code = 429

        def failing_solver(**kwargs):
            barrier.wait(timeout=2)
            if target.worker._tls.tag.strip() == "#1":
                raise TimeoutError("等待 cf-turnstile-response 挂载超时")
            raise requests.HTTPError("private-response", response=response)

        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", failing_solver):
            task = self.finish(self.start(2, 2, max_attempts=1))
        first = "\n".join(line for line in task["logs"] if line.startswith("#1 "))
        second = "\n".join(line for line in task["logs"] if line.startswith("#2 "))
        self.assertIn("TimeoutError", first)
        self.assertNotIn("HTTP 429", first)
        self.assertIn("HTTP 429", second)
        self.assertNotIn("TimeoutError", second)
        self.assertEqual(task["failures"], 2)

    def test_partial_account_keeps_its_verification_failure(self):
        with patch.object(target.worker, "create_mailbox", self.mailbox), \
             patch.object(target.worker, "solve_turnstile", return_value="private-captcha"), \
             patch.object(target.worker, "register", return_value=(object(), "private-token", {})), \
             patch.object(target.worker, "resend_code", return_value=True), \
             patch.object(target.worker, "wait_mail_code", side_effect=TimeoutError(
                 "等 Cloudflare 临时邮箱验证码超时")):
            task = self.finish(self.start(max_attempts=1))
        self.assertEqual(task["status"], "partial")
        self.assertEqual(task["partials"], 1)
        self.assertIn("等 Cloudflare 临时邮箱验证码超时", "\n".join(task["logs"]))
        self.assertNotIn("private-token", self.path.read_text(encoding="utf-8"))
        account = json.loads((self.account_dir / task["account_file"]).read_text())
        self.assertFalse(account["verified"])

    def test_diagnostic_write_failure_cannot_prevent_account_save(self):
        account_path = self.account_dir / "completed.jsonl"
        record = {"proxy_count": 100, "verified": True, "email": "saved@example.com"}

        def broken_log(message):
            if "保存账号" in message:
                raise OSError("private-storage-path")

        with patch.object(target.worker, "_register_once", return_value=record), \
             target.worker.task_diagnostics(broken_log, retry_limit=1):
            try:
                target.worker.register_one(1, True, str(account_path), "unused", max_attempts=1)
            except OSError:
                pass
        self.assertTrue(account_path.exists(), "diagnostic failure prevented account save")
        self.assertEqual(json.loads(account_path.read_text())["proxy_count"], 100)

    def test_wrapped_http_failures_keep_status_without_body(self):
        response = requests.Response()
        response.status_code = 429
        response._content = b'{"error":"private-response-body"}'
        eligibility = requests.Response()
        eligibility.status_code = 200
        eligibility._content = b'{"claimed":false}'
        session = Mock()
        session.post.return_value = response
        session.get.return_value = eligibility
        operations = [
            lambda: target.worker.get_current_user(session, "private-token"),
            lambda: target.worker.ensure_premium_trial(session, "private-token"),
            lambda: target.worker._json_or_error(response, "service"),
            lambda: target.worker._captcha_post("/createTask", {}),
        ]
        for operation in operations:
            events = []
            with self.subTest(operation=operation), \
                 patch.object(target.worker.requests, "post", return_value=response), \
                 target.worker.task_diagnostics(events.append):
                with self.assertRaises(RuntimeError):
                    operation()
                self.assertIn("HTTP 429", "\n".join(events))
                self.assertNotIn("private-response-body", "\n".join(events))


if __name__ == "__main__":
    unittest.main()
