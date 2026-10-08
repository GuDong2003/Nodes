import json
import stat
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

import pool

try:
    import pool_automation
except ModuleNotFoundError:
    pool_automation = None


class AutomationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pool_automation, "threshold scheduler is not implemented")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "automation.json"
        self.now = 1_800_000_000
        self.config = {"pool_auto_register": True, "pool_target_slots": 200,
                       "pool_target_bandwidth_gb": 0, "pool_loop_seconds": 1800,
                       "pool_max_register_per_round": 2}
        self.entries = []
        self.sync_count = 0
        self.tasks = []
        self.active_task = None
        self.sync_result = {"synced": ["one"], "failed": [], "blocking_failed": 0}
        self.sync_action = lambda: None
        self.runner = self.make_runner()

    def make_runner(self):
        return pool_automation.Automation(
            state_file=self.path, settings=lambda: pool.pool_settings(self.config),
            synchronize=self.synchronize,
            snapshot=lambda: pool.capacity(self.entries, pool.pool_settings(self.config)),
            active=lambda: self.active_task, start_task=self.start_task,
            config_lock=threading.RLock(), clock=lambda: self.now,
        )

    def synchronize(self):
        self.sync_count += 1
        self.sync_action()
        return self.sync_result

    def start_task(self, count, concurrency):
        task = {"id": "new-task", "requested": count, "concurrency": concurrency}
        self.tasks.append(task)
        return task

    def test_disabled_periodic_checks_do_not_sync_or_register(self):
        self.config["pool_auto_register"] = False
        self.runner.poll()
        self.assertEqual(self.sync_count, 0)
        self.assertEqual(self.tasks, [])
        self.assertIsNone(self.runner.status()["next_check_at"])

    def test_manual_check_syncs_but_does_not_register_when_disabled(self):
        self.config["pool_auto_register"] = False
        self.runner.check()
        self.assertEqual(self.sync_count, 1)
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "disabled")

    def test_decision_uses_fresh_quota_after_synchronization(self):
        def replenish_data():
            self.entries = [{"slots": list(range(200)), "bandwidth_remaining": 3_000_000_000}]
        self.sync_action = replenish_data
        self.runner.check()
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "satisfied")

    def test_shortage_obeys_per_round_limit(self):
        self.config["pool_target_slots"] = 1000
        self.runner.check()
        self.assertEqual(self.tasks, [{"id": "new-task", "requested": 2, "concurrency": 1}])
        self.assertEqual(self.runner.status()["registered_count"], 2)

    def test_failed_refresh_blocks_registration_without_erasing_status(self):
        self.sync_result = {"synced": ["one"], "failed": [{"error": "unreachable"}], "blocking_failed": 1}
        self.runner.check()
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "sync_failed")
        self.assertEqual(self.runner.status()["synced"], 1)
        self.assertEqual(self.runner.status()["failed"], 1)

    def test_unknown_quota_blocks_bandwidth_based_registration(self):
        self.config.update(pool_target_slots=0, pool_target_bandwidth_gb=2)
        self.entries = [{"slots": ["node"], "bandwidth_remaining": None}]
        self.runner.check()
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "unknown_capacity")

    def test_unknown_quota_also_prevents_assuming_node_availability(self):
        self.entries = [{"slots": ["node"], "bandwidth_remaining": None}]
        self.runner.check()
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "unknown_capacity")

    def test_existing_registration_is_not_overlapped(self):
        self.active_task = {"id": "busy"}
        self.runner.check()
        self.assertEqual(self.sync_count, 0)
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "busy")

    def test_disable_during_sync_prevents_pending_registration(self):
        self.sync_action = lambda: self.config.update(pool_auto_register=False)
        self.runner.check()
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.runner.status()["outcome"], "disabled")

    def test_interval_is_persisted_and_restart_does_not_repeat_check(self):
        self.runner.configure()
        self.runner.poll()
        self.assertEqual(self.sync_count, 0)
        self.now += 1800
        self.runner.poll()
        self.assertEqual(self.sync_count, 1)
        self.assertEqual(datetime.fromisoformat(self.runner.status()["next_check_at"]).timestamp(), self.now + 1800)
        self.runner = self.make_runner()
        self.runner.poll()
        self.assertEqual(self.sync_count, 1)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertNotIn("one", self.path.read_text())

    def test_concurrent_checks_are_rejected_and_error_releases_lock(self):
        entered, release = threading.Event(), threading.Event()
        def fail_later():
            entered.set()
            release.wait(3)
            raise ValueError("broken response")
        self.sync_action = fail_later
        thread = threading.Thread(target=self.runner.check)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            self.assertTrue(self.runner.status()["running"])
            with self.assertRaises(RuntimeError):
                self.runner.check()
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(self.runner.status()["running"])
        self.assertEqual(self.runner.status()["outcome"], "error")
        self.sync_action = lambda: None
        self.runner.check()
        self.assertEqual(len(self.tasks), 1)


if __name__ == "__main__":
    unittest.main()
