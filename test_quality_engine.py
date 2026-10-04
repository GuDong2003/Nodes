"""Offline behavior tests for selective upstream quality/storage integration."""
import importlib
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

try:
    quality = importlib.import_module('proxy_quality')
    store = importlib.import_module('platform_store')
except ModuleNotFoundError:
    quality = store = None


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload if payload is not None else {
            'status': 'success', 'country': 'United States', 'countryCode': 'US',
            'query': '203.0.113.9',
        }

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses=None, delay=0):
        self.responses = list(responses or [Response()])
        self.delay = delay
        self.closed = False

    def get(self, url, **kwargs):
        time.sleep(self.delay)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(quality, 'quality engine and private storage are not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'proxy_quality_enabled': True}
        self.url = 'http://alice:secret@proxy.example:8080'

    def check(self, urls=None, **kwargs):
        return quality.filter_proxies(urls or [self.url], settings=self.config,
                                      data_dir=self.root, **kwargs)

    def test_default_disabled_returns_unique_input_without_probing(self):
        with patch.object(quality, '_session_for_proxy', side_effect=AssertionError('network')):
            accepted, report = quality.filter_proxies([self.url, self.url], data_dir=self.root)
        self.assertEqual(accepted, [self.url])
        self.assertEqual((report['accepted'], report['rejected'], report['skipped_unprobed']), (0, 0, 1))
        self.assertIsNone(report['results'][0]['ok'])
        self.assertFalse(any(self.root.iterdir()))

    def test_default_country_exclusion_allows_us_and_no_optional_target(self):
        session = Session()
        with patch.object(quality, '_session_for_proxy', return_value=session):
            accepted, report = self.check()
        self.assertEqual(accepted, [self.url])
        self.assertEqual(report['results'][0]['country_code'], 'US')
        self.assertTrue(session.closed)

    def test_normalized_settings_are_honored(self):
        cfg = quality.quality_settings({'proxy_quality_enabled': True, 'proxy_exclude_countries': ['US']})
        with patch.object(quality, '_session_for_proxy', return_value=Session()):
            accepted, report = quality.filter_proxies([self.url], cfg, self.root)
        self.assertEqual(accepted, [])
        self.assertEqual(report['rejected'], 1)

    def test_credentials_and_rules_cannot_reuse_cached_result(self):
        with patch.object(quality, '_session_for_proxy', side_effect=lambda _: Session()):
            self.check()
            other = self.url.replace('secret', 'changed')
            accepted, report = self.check([other], probe_missing=False)
            self.assertEqual(accepted, [])
            self.assertIsNone(report['results'][0]['ok'])
            self.config['proxy_exclude_countries'] = ['US']
            accepted, report = self.check(probe_missing=False)
            self.assertEqual(accepted, [])
            self.assertEqual(report['skipped_unprobed'], 1)

    def test_memory_cache_isolated_by_data_directory(self):
        with patch.object(quality, '_session_for_proxy', return_value=Session()):
            self.check()
        with patch.object(quality, '_session_for_proxy', side_effect=AssertionError('network')):
            accepted, report = quality.filter_proxies([self.url], self.config, self.root / 'other', probe_missing=False)
        self.assertEqual(accepted, [])
        self.assertEqual(report['rejected'], 0)
        self.assertFalse((self.root / 'other').exists())

    def test_untested_and_failed_are_distinct_and_order_is_preserved(self):
        urls = [self.url, 'http://b.example:8080', 'http://c.example:8080']
        with patch.object(quality, '_session_for_proxy', return_value=Session([Response(503)])):
            self.check([urls[1]])
        accepted, report = self.check(urls, probe_missing=False)
        self.assertEqual(accepted, [])
        self.assertEqual((report['rejected'], report['skipped_unprobed']), (1, 2))
        self.assertEqual([r['identity'] for r in report['results']], ['proxy.example:8080', 'b.example:8080', 'c.example:8080'])
        self.assertEqual([r['ok'] for r in report['results']], [None, False, None])

    def test_dry_run_does_not_write_disk_or_memory(self):
        with patch.object(quality, '_session_for_proxy', side_effect=lambda _: Session()):
            accepted, report = self.check(use_cache=False)
        self.assertEqual(accepted, [self.url])
        self.assertFalse(any(self.root.iterdir()))
        accepted, report = self.check(probe_missing=False)
        self.assertEqual(report['skipped_unprobed'], 1)

    def test_dry_run_does_not_overwrite_existing_cache(self):
        with patch.object(quality, '_session_for_proxy', return_value=Session()):
            self.check()
        before = (self.root / 'proxy_quality_cache.json').read_bytes()
        with patch.object(quality, '_session_for_proxy', return_value=Session([Response(503)])):
            self.check(use_cache=False)
        self.assertEqual((self.root / 'proxy_quality_cache.json').read_bytes(), before)
        accepted, report = self.check(probe_missing=False)
        self.assertEqual(accepted, [self.url])

    def test_cache_expires_without_turning_untested_into_failed(self):
        with patch.object(quality, '_session_for_proxy', return_value=Session()):
            self.check()
        with patch.object(quality.time, 'time', return_value=time.time() + 601):
            accepted, report = self.check(probe_missing=False)
        self.assertEqual((report['rejected'], report['skipped_unprobed']), (0, 1))

    def test_http_errors_and_bad_geo_payload_are_rejected(self):
        for response in [Response(500), Response(payload=[]), Response(payload={'status': 'fail'})]:
            with self.subTest(response=response), patch.object(quality, '_session_for_proxy', return_value=Session([response])):
                accepted, report = self.check(use_cache=False)
            self.assertEqual(accepted, [])
            self.assertEqual(report['rejected'], 1)

    def test_optional_target_requires_success_status(self):
        self.config['proxy_arp_check_enabled'] = True
        for status, expected in [(204, True), (403, False), (500, False)]:
            with self.subTest(status=status), patch.object(quality, '_session_for_proxy', return_value=Session([Response(), Response(status)])):
                accepted, report = self.check(use_cache=False)
            self.assertEqual(bool(accepted), expected)
            self.assertEqual(report['results'][0]['arp_status'], status)

    def test_latency_limit_rejects_slow_geo(self):
        self.config['proxy_max_latency_ms'] = 200
        with patch.object(quality, '_session_for_proxy', return_value=Session(delay=.22)):
            accepted, report = self.check(use_cache=False)
        self.assertEqual(accepted, [])
        self.assertGreaterEqual(report['results'][0]['latency_ms'], 200)

    def test_invalid_scheme_and_port_never_start_network(self):
        urls = ['ftp://a.example:8080', 'http://a.example:0', 'http://a.example:65536', 'http://user:pass@a.example:bad']
        with patch.object(quality, '_session_for_proxy', side_effect=AssertionError('network')):
            accepted, report = self.check(urls, use_cache=False)
        self.assertEqual(accepted, [])
        self.assertEqual(report['rejected'], 4)
        self.assertNotIn('user:pass', json.dumps(report))

    def test_reports_and_cache_never_contain_credentials_or_exception_text(self):
        import requests
        exc = requests.ProxyError('failed at ' + self.url + ' token=secret') if hasattr(requests, 'ProxyError') else requests.exceptions.ProxyError('failed at ' + self.url + ' token=secret')
        with patch.object(quality, '_session_for_proxy', return_value=Session([exc])):
            accepted, report = self.check()
        encoded = json.dumps(report) + (self.root / 'proxy_quality_cache.json').read_text()
        self.assertNotIn('secret', encoded)
        self.assertNotIn('alice', encoded)
        self.assertIn('proxy.example:8080', encoded)
        self.assertEqual(os.stat(self.root / 'proxy_quality_cache.json').st_mode & 0o777, 0o600)

    def test_workers_are_bounded_and_concurrent_writes_do_not_drop_results(self):
        counter = {'active': 0, 'peak': 0}
        lock = threading.Lock()
        class TrackingSession(Session):
            def get(self, *args, **kwargs):
                with lock:
                    counter['active'] += 1
                    counter['peak'] = max(counter['peak'], counter['active'])
                try:
                    time.sleep(.01)
                    return Response()
                finally:
                    with lock:
                        counter['active'] -= 1
        self.config['proxy_quality_workers'] = 2
        urls = [f'http://p{i}.example:8080' for i in range(12)]
        with patch.object(quality, '_session_for_proxy', side_effect=lambda _: TrackingSession()):
            accepted, report = self.check(urls)
        self.assertEqual(accepted, urls)
        self.assertEqual(len(report['results']), 12)
        self.assertLessEqual(counter['peak'], 2)
        with patch.object(quality, '_session_for_proxy', side_effect=lambda _: Session()):
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda i: self.check([f'http://extra{i}.example:8080']), range(12)))
        accepted, report = self.check(urls + [f'http://extra{i}.example:8080' for i in range(12)], probe_missing=False)
        self.assertEqual(len(accepted), 24)
        self.assertEqual(len(json.loads((self.root / 'proxy_quality_cache.json').read_text())), 24)

    def test_corrupt_cache_boolean_is_not_treated_as_a_tested_observation(self):
        with patch.object(quality, '_session_for_proxy', return_value=Session()):
            self.check()
        path = self.root / 'proxy_quality_cache.json'
        payload = json.loads(path.read_text())
        next(iter(payload.values()))['ok'] = 1
        path.write_text(json.dumps(payload))
        with patch.object(quality, '_memory_cache', {}):
            accepted, report = self.check(probe_missing=False)
        self.assertEqual(accepted, [])
        self.assertEqual((report['rejected'], report['skipped_unprobed']), (0, 1))

    def test_cache_storage_never_saves_raw_proxy_fields_or_keys(self):
        quality.save_cache(self.root, {
            'a' * 64: {'identity': 'proxy.example:8080', 'proxy': self.url,
                       'ok': True, 'checked_at': time.time()},
            self.url: {'ok': True, 'checked_at': time.time()},
        })
        encoded = (self.root / 'proxy_quality_cache.json').read_text()
        self.assertNotIn('alice', encoded)
        self.assertNotIn('secret', encoded)
        self.assertEqual(len(quality.load_cache(self.root)), 1)

    def test_filter_reads_cache_once_and_writes_probe_batch_once(self):
        reads = []
        writes = []
        original_read = quality.load_cache
        original_write = quality._atomic_write
        def read(*args):
            reads.append(1)
            return original_read(*args)
        def write(*args):
            writes.append(1)
            return original_write(*args)
        urls = [f'http://batch{i}.example:8080' for i in range(12)]
        with patch.object(quality, 'load_cache', side_effect=read), patch.object(quality, '_atomic_write', side_effect=write), patch.object(quality, '_session_for_proxy', side_effect=lambda _: Session()):
            accepted, report = self.check(urls)
        self.assertEqual(accepted, urls)
        self.assertLessEqual(len(reads), 2)  # Initial read plus locked merge read.
        self.assertEqual(len(writes), 1)
        reads.clear()
        with patch.object(quality, '_memory_cache', {}), patch.object(quality, 'load_cache', side_effect=read):
            accepted, report = self.check(urls, probe_missing=False)
        self.assertEqual(accepted, urls)
        self.assertEqual(len(reads), 1)

    def test_latency_rule_respects_full_validated_api_range(self):
        self.config['proxy_max_latency_ms'] = 1
        with patch.object(quality, '_session_for_proxy', return_value=Session(delay=.01)):
            accepted, report = self.check(use_cache=False)
        self.assertEqual(accepted, [])
        self.config['proxy_max_latency_ms'] = 90000
        with patch.object(quality, '_session_for_proxy', return_value=Session()), patch.object(quality.time, 'monotonic', side_effect=[0, 70]):
            accepted, report = self.check(use_cache=False)
        self.assertEqual(accepted, [self.url])

    def test_cache_retention_keeps_recent_results_and_is_bounded(self):
        now = time.time()
        payload = {f'{index:064x}': {'identity': 'old.example:8080', 'ok': True,
                    'checked_at': now - 100} for index in range(5000)}
        quality.save_cache(self.root, payload)
        with patch.object(quality, '_session_for_proxy', return_value=Session()):
            self.check()
        saved = quality.load_cache(self.root)
        self.assertLessEqual(len(saved), 5000)
        accepted, report = self.check(probe_missing=False)
        self.assertEqual(accepted, [self.url])

    def test_proxy_schemes_and_ipv6_are_valid(self):
        urls = ['https://proxy.example:443', 'socks5://proxy.example:1080', 'socks5h://[::1]:1080']
        with patch.object(quality, '_session_for_proxy', side_effect=lambda _: Session()):
            accepted, report = self.check(urls, use_cache=False)
        self.assertEqual(accepted, urls)
        self.assertEqual(report['results'][-1]['identity'], '[::1]:1080')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(store, 'private storage is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_read_only_loaders_do_not_create_directories(self):
        missing = self.root / 'missing'
        self.assertIsNone(store.load_inventory_latest(missing))
        self.assertEqual(store.load_inventory_history(missing), [])
        self.assertEqual(store.load_audit(missing), [])
        self.assertFalse(missing.exists())

    def test_snapshots_throttle_same_observation_but_record_changed_profile_and_untested(self):
        inventory = {'scanned': 3, 'accepted': 1, 'rejected': 1, 'skipped_unprobed': 1, 'profile_id': 'default', 'version': 1}
        store.save_inventory_snapshot(self.root, inventory)
        store.save_inventory_snapshot(self.root, inventory)
        self.assertEqual(len(store.load_inventory_history(self.root)), 1)
        inventory['skipped_unprobed'] = 2
        store.save_inventory_snapshot(self.root, inventory)
        inventory['version'] = 2
        store.save_inventory_snapshot(self.root, inventory)
        self.assertEqual(len(store.load_inventory_history(self.root)), 3)
        self.assertEqual(store.load_inventory_latest(self.root)['version'], 2)
        for path in self.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_audit_and_snapshot_writes_remain_valid_and_redacted(self):
        def write(i):
            store.append_audit(self.root, 'quality', 'check', {'proxy': 'http://alice:secret@p.example:8080', 'password': 'secret', 'count': i})
            store.save_inventory_snapshot(self.root, {'scanned': i}, min_interval_sec=0)
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(30)))
        self.assertEqual(len(store.load_audit(self.root)), 30)
        self.assertEqual(len(store.load_inventory_history(self.root)), 30)
        for path in self.root.iterdir():
            text = path.read_text()
            self.assertNotIn('secret', text)
            self.assertNotIn('alice', text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_atomic_replace_preserves_old_file_and_cleans_temporary(self):
        store.save_inventory_snapshot(self.root, {'scanned': 1})
        before = (self.root / 'inventory.json').read_bytes()
        with patch.object(store.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                store.save_inventory_snapshot(self.root, {'scanned': 2})
        self.assertEqual((self.root / 'inventory.json').read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ['inventory.json', 'inventory_history.jsonl'])

    def test_snapshot_and_audit_redact_nested_credentials(self):
        payload = {'results': [{'identity': 'http://alice:secret@p.example:8080',
                               'api_token': 'hidden', 'note': 'failed https://bob:other@target.example/path'}]}
        snapshot = store.save_inventory_snapshot(self.root, payload)
        audit = store.append_audit(self.root, 'quality', 'check', payload)
        encoded = json.dumps(snapshot) + json.dumps(audit)
        for secret in ('alice', 'secret', 'hidden', 'bob', 'other'):
            self.assertNotIn(secret, encoded)
        self.assertIn('p.example:8080', encoded)

    def test_audit_filter_and_limit_and_malformed_lines(self):
        store.append_audit(self.root, 'quality', 'first')
        store.append_audit(self.root, 'rules', 'save')
        store.append_audit(self.root, 'quality', 'last')
        with (self.root / 'audit.jsonl').open('a') as handle:
            handle.write('broken\n')
        rows = store.load_audit(self.root, kind='quality', limit=1)
        self.assertEqual([row['action'] for row in rows], ['last'])


if __name__ == '__main__':
    unittest.main()
