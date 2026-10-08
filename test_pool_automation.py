"""Threshold accounting and scheduler regressions; remote calls are replaced in tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pool
import web_app as web


class ExpiryCountdownTests(unittest.TestCase):
    def test_remaining_time_ignores_registration_day_cache(self):
        with patch.object(web.time, "time", return_value=1_800_000_000):
            result = web._usage_fields({"expiration_time": 1_800_284_400, "days_remaining": 6})
        self.assertEqual(result["days_remaining"], 3)
        self.assertEqual(result.get("remaining_seconds"), 284400)

    def test_expired_account_never_reports_cached_positive_days(self):
        with patch.object(web.time, "time", return_value=1_800_000_000):
            result = web._usage_fields({"expiration_time": 1_799_999_999, "days_remaining": 6})
        self.assertEqual(result["days_remaining"], 0)
        self.assertTrue(result["expired"])
        self.assertIsNotNone(result["expires_at"])

    def test_unknown_expiry_is_not_a_fabricated_countdown(self):
        result = web._usage_fields({"days_remaining": 7})
        self.assertIsNone(result["days_remaining"])
        self.assertIsNone(result.get("remaining_seconds"))


class ThresholdCapacityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.node_dir = Path(directory.name)

    def record(self, name, bandwidth, nodes=100, expiry=2000):
        return {"email": name + "@example.com", "account_id": name,
                "proxy_username": name, "proxy_password": "private",
                "proxy_ips": [f"host-{i}.example.com:80" for i in range(nodes)],
                "expiration_time": expiry, "bandwidth_remaining": bandwidth}

    def capacity(self, records, **config):
        settings = pool.pool_settings(config)
        entries = pool.live_entries(records, self.node_dir, settings, 1000)
        return pool.capacity(entries, settings)

    def test_bandwidth_counts_each_account_once_not_each_proxy(self):
        result = self.capacity([self.record("one", 2_000_000_000), self.record("two", 3_000_000_000)],
                               pool_target_slots=0, pool_target_bandwidth_gb=8)
        self.assertEqual(result.get("bandwidth_remaining"), 5_000_000_000)
        self.assertEqual(result.get("shortage_bandwidth"), 3_000_000_000)
        self.assertEqual(result["live_slots"], 200)
        self.assertEqual(result["shortage_slots"], 0)
        self.assertGreater(result["needed_accounts"], 0)

    def test_either_threshold_can_trigger_and_equality_is_satisfied(self):
        records = [self.record("one", 2_000_000_000)]
        for nodes, gb, want_needed in ((200, 1, True), (50, 3, True), (100, 2, False), (0, 2, False)):
            with self.subTest(nodes=nodes, gb=gb):
                result = self.capacity(records, pool_target_slots=nodes, pool_target_bandwidth_gb=gb)
                self.assertEqual(result["needed_accounts"] > 0, want_needed)

    def test_expired_or_depleted_accounts_do_not_inflate_capacity(self):
        result = self.capacity([self.record("old", 9_000_000_000, expiry=999),
                                self.record("empty", 0), self.record("live", 2_000_000_000)],
                               pool_target_bandwidth_gb=3)
        self.assertEqual(result["live_accounts"], 1)
        self.assertEqual(result.get("bandwidth_remaining"), 2_000_000_000)

    def test_unknown_bandwidth_is_reported_for_safe_replenishment(self):
        result = self.capacity([self.record("unknown", None)], pool_target_bandwidth_gb=2)
        self.assertEqual(result.get("unknown_bandwidth_accounts"), 1)

    def test_unconfigured_install_does_not_register_automatically(self):
        self.assertFalse(pool.pool_settings({})["auto_register"])


if __name__ == "__main__":
    unittest.main()
