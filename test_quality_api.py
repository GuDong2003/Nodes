"""Selective upstream quality integration: isolated files and mocked probes only."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app as web


class QualityApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('ACCOUNT_DIR', 'NODE_DIR', 'WEB_DATA_DIR'):
            directory = self.root / name
            directory.mkdir()
            self.enterContext(patch.object(web, name, directory))
        self.config = {
            'mail_provider': 'cfmail', 'mail_domain': 'mail.example',
            'mail_api_base': 'https://temp.example', 'captcha_provider': 'browser',
            'web_password_hash': 'private-hash', 'web_session_secret': 'private-session',
            'export_token': 'fixture-export', 'resin_proxy_token': 'private-proxy',
            'pool_auto_register': False, 'pool_slots_per_account': 8,
        }
        self.config_file = self.root / 'config.json'
        self.config_file.write_text(json.dumps(self.config))
        self.enterContext(patch.object(web, 'CONFIG_FILE', self.config_file))
        self.enterContext(patch.dict(web.app.config, TESTING=True, SESSION_COOKIE_SECURE=False))
        self.enterContext(patch.object(web.TASK_STORE, 'active', return_value=None))
        self.client = web.app.test_client()
        self.headers = {'X-CSRF-Token': 'csrf'}

    def login(self):
        with self.client.session_transaction() as session:
            session.update(authenticated=True, username=web.WEB_USERNAME, csrf_token='csrf')

    def seed(self):
        account = {'email': 'fixture@example.test', 'proxy_username': 'fixture-user',
                   'proxy_password': 'fixture-password', 'ts': 1,
                   'proxy_ips': [f'192.0.2.{i}:3129' for i in range(1, 101)]}
        (web.ACCOUNT_DIR / 'accounts_fixture.jsonl').write_text(json.dumps(account)+'\n')

    def enable(self):
        response = self.client.put('/api/quality/profiles/default',
                                   json={'proxy_quality_enabled': True}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_default_profiles_and_inventory_are_neutral(self):
        self.login()
        self.seed()
        profiles = self.client.get('/api/quality/profiles')
        self.assertEqual(profiles.status_code, 200)
        default = next(p for p in profiles.json['profiles'] if p['id']=='default')
        self.assertFalse(default['proxy_quality_enabled'])
        self.assertEqual(default['proxy_exclude_countries'], '')
        inv = self.client.get('/api/inventory')
        self.assertEqual(inv.status_code, 200)
        self.assertEqual(inv.json['inventory']['scanned'], 100)
        self.assertEqual(inv.json['inventory']['skipped_unprobed'], 100)
        self.assertEqual(inv.json['inventory']['accepted'], 0)
        self.assertEqual(inv.json['inventory']['rejected'], 0)
        self.assertEqual(len(self.client.get('/api/export/live-proxies?token=fixture-export').text.splitlines()),100)

    def test_quality_routes_require_login_and_csrf(self):
        for route in ('/api/quality/profiles','/api/inventory','/api/audit','/api/export/qualified-proxies'):
            self.assertEqual(self.client.get(route).status_code, 401, route)
        self.login()
        for route in ('/api/quality/check','/api/quality/dry-run','/api/inventory/snapshot','/api/quality/profiles/activate'):
            self.assertEqual(self.client.post(route,json={}).status_code,403,route)

    def test_save_activate_version_and_audit_preserve_existing_config(self):
        self.login()
        saved=self.client.put('/api/quality/profiles/fast',json={
            'name':'低延迟', 'proxy_quality_enabled':True,'proxy_max_latency_ms':900,
            'proxy_exclude_countries':'JP,SG'},headers=self.headers)
        self.assertEqual(saved.status_code,200)
        activated=self.client.post('/api/quality/profiles/activate',json={'id':'fast'},headers=self.headers)
        self.assertEqual(activated.status_code,200)
        data=json.loads(self.config_file.read_text())
        for k,v in self.config.items(): self.assertEqual(data[k],v,k)
        self.assertEqual(data['quality_profile_id'],'fast')
        self.assertEqual(data['quality_profiles']['fast']['version'],1)
        self.assertEqual(self.config_file.stat().st_mode & 0o777,0o600)
        audit=self.client.get('/api/audit').json['entries']
        self.assertEqual(len(audit),2)
        self.assertNotIn('private-',json.dumps(audit))

    def test_invalid_profile_updates_do_not_write(self):
        self.login()
        original=self.config_file.read_bytes()
        for payload in ({'proxy_quality_workers':0},{'proxy_max_latency_ms':'NaN'},
                        {'proxy_exclude_countries':'United States'},
                        {'proxy_arp_probe_url':'file:///etc/passwd'},
                        {'proxy_quality_enabled':'true'}, {'proxy_exclude_countries':'ZZ'},
                        {'proxy_arp_probe_url':'https://user:password@example.test'},
                        {'proxy_quality_timeout_sec':False}, {'proxy_quality_workers':1.5},
                        {'unexpected':'field'}, []):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.put('/api/quality/profiles/default',json=payload,
                                                 headers=self.headers).status_code,400)
                self.assertEqual(self.config_file.read_bytes(),original)
        self.assertEqual(self.client.post('/api/quality/profiles/activate',json={'id':'missing'},headers=self.headers).status_code,400)

    def test_reads_and_disabled_export_do_not_probe(self):
        self.login(); self.seed()
        with patch('proxy_quality.probe_proxy', side_effect=AssertionError('GET must not probe')):
            self.assertEqual(self.client.get('/api/inventory').status_code,200)
            exported=self.client.get('/api/export/qualified-proxies?token=fixture-export')
            self.assertEqual(exported.status_code,200)
            self.assertEqual(len(exported.text.splitlines()),100)
            self.enable()
            exported=self.client.get('/api/export/qualified-proxies?token=fixture-export')
            self.assertEqual(exported.status_code,200)
            self.assertEqual(exported.text,'')
            self.assertEqual(self.client.get('/api/export/qualified-proxies?rule=missing').status_code,400)

    def test_dry_run_does_not_write_inventory_or_expose_credentials(self):
        self.login(); self.enable()
        before={p.name:p.read_bytes() for p in web.WEB_DATA_DIR.iterdir() if p.is_file()}
        with patch('proxy_quality.probe_proxy',return_value={
                'identity':'192.0.2.1:3129','ok':True,'reason':'ok','latency_ms':12}):
            response=self.client.post('/api/quality/dry-run',json={
                'text':'http://sensitive:secret@192.0.2.1:3129'},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['report']['accepted'],1)
        self.assertNotIn('sensitive',response.text); self.assertNotIn('secret',response.text)
        after={p.name:p.read_bytes() for p in web.WEB_DATA_DIR.iterdir() if p.is_file()}
        self.assertEqual(before,after)

    def test_background_check_serializes_work_and_persists_results(self):
        self.login(); self.seed(); self.enable()
        release=threading.Event(); entered=threading.Event()
        def probe(url, settings):
            entered.set()
            if not release.wait(5): raise TimeoutError('test gate')
            return {'identity':url.rsplit('@',1)[-1], 'ok':True, 'reason':'ok',
                    'latency_ms':10, 'checked_at':time.time()}
        with patch('proxy_quality.probe_proxy',side_effect=probe):
            try:
                response=self.client.post('/api/quality/check',json={},headers=self.headers)
                self.assertEqual(response.status_code,202)
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.post('/api/quality/check',json={},headers=self.headers).status_code,409)
                self.assertEqual(self.client.get('/api/inventory').status_code,200)
            finally:
                release.set()
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    data=self.client.get('/api/inventory').json
                    if data and not data['check']['running']: break
                    time.sleep(.02)
        self.assertFalse(data['check']['running'])
        self.assertEqual(data['inventory']['accepted'],100)
        self.assertEqual(data['check']['completed'],100)
        self.assertTrue(self.client.get('/api/inventory/history').json['history'])
        self.assertNotIn('fixture-password',json.dumps(data))

    def test_cache_inventory_excludes_deleted_accounts_and_stale_rules(self):
        import proxy_quality
        self.login(); self.seed(); self.enable()
        profile=self.client.get('/api/quality/profiles').json['profiles'][0]
        urls=web._live_proxy_body().splitlines()
        def probe(url, settings):
            return {'identity':url.rsplit('@',1)[-1], 'ok':True,'reason':'ok',
                    'latency_ms':10,'checked_at':time.time()}
        with patch('proxy_quality.probe_proxy',side_effect=probe):
            proxy_quality.filter_proxies(urls,settings=profile,data_dir=web.WEB_DATA_DIR)
        self.assertEqual(self.client.get('/api/inventory').json['inventory']['accepted'],100)
        updated=self.client.put('/api/quality/profiles/default',json={'proxy_max_latency_ms':900},headers=self.headers)
        self.assertEqual(updated.json['profile']['version'],2)
        self.assertEqual(self.client.get('/api/inventory').json['inventory']['skipped_unprobed'],100)
        (web.ACCOUNT_DIR / 'accounts_fixture.jsonl').unlink()
        inventory=self.client.get('/api/inventory').json['inventory']
        self.assertEqual(inventory['scanned'],0)
        self.assertEqual(inventory['results'],[])

    def test_failed_background_check_clears_running_and_redacts_error(self):
        self.login(); self.seed(); self.enable()
        with patch('proxy_quality.filter_proxies',side_effect=RuntimeError('http://name:private-secret@host:80')):
            response=self.client.post('/api/quality/check',json={},headers=self.headers)
            self.assertEqual(response.status_code,202)
            deadline=time.monotonic()+3
            while web._quality_check_status()['running'] and time.monotonic()<deadline:
                time.sleep(.01)
        status=web._quality_check_status()
        self.assertFalse(status['running'])
        self.assertEqual(status['error'],'RuntimeError')
        audit=self.client.get('/api/audit').json['entries']
        self.assertEqual(audit[0]['action'],'check_failed')
        self.assertNotIn('private-secret',json.dumps(audit))

    def test_token_export_and_dry_run_input_limits(self):
        self.seed()
        self.assertEqual(self.client.get('/api/export/qualified-proxies?token=wrong').status_code,401)
        self.assertEqual(len(self.client.get('/api/export/qualified-proxies?token=fixture-export').text.splitlines()),100)
        self.login()
        for payload in ({'text':'file:///tmp/secret'},{'text':'http://host:99999'},
                        {'text':''},{'text':'\n'.join(f'http://host{i}:80' for i in range(51))}, []):
            self.assertEqual(self.client.post('/api/quality/dry-run',json=payload,headers=self.headers).status_code,400)
        self.assertEqual(self.client.get('/api/inventory/history?limit=oops').status_code,400)

    def test_concurrent_profile_and_settings_writes_preserve_both_changes(self):
        self.login()
        entered=threading.Event(); release=threading.Event()
        original=web.quality_rules.save_profile
        responses=[]
        def gated_save(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args)
        def save_profile():
            responses.append(self.client.put('/api/quality/profiles/fast',
                                             json={'name':'快速'},headers=self.headers).status_code)
        with patch.object(web.quality_rules,'save_profile',side_effect=gated_save):
            first=threading.Thread(target=save_profile)
            first.start()
            self.assertTrue(entered.wait(3))
            second=threading.Thread(target=lambda: web._apply_settings({'mail_domain':'changed.example'}))
            with patch.object(web.worker,'reload_settings'):
                second.start(); release.set(); first.join(3); second.join(3)
        self.assertFalse(first.is_alive()); self.assertFalse(second.is_alive())
        self.assertEqual(responses,[200])
        config=json.loads(self.config_file.read_text())
        self.assertEqual(config['mail_domain'],'changed.example')
        self.assertIn('fast',config['quality_profiles'])


if __name__=='__main__': unittest.main()
