const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const defaults = {
  proxy_quality_enabled: false, proxy_max_latency_ms: 3000, proxy_exclude_countries: '',
  proxy_arp_check_enabled: false, proxy_arp_probe_url: 'https://cp.cloudflare.com/generate_204',
  proxy_quality_workers: 8, proxy_quality_cache_ttl_sec: 600, proxy_quality_timeout_sec: 12,
};
const fields = {
  qualityEnable: 'proxy_quality_enabled', qualityLatency: 'proxy_max_latency_ms',
  qualityCountries: 'proxy_exclude_countries', qualityTargetEnable: 'proxy_arp_check_enabled',
  qualityTargetUrl: 'proxy_arp_probe_url', qualityWorkers: 'proxy_quality_workers',
  qualityCache: 'proxy_quality_cache_ttl_sec', qualityTimeout: 'proxy_quality_timeout_sec',
};
function deferred() { let resolve; let reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function fixture() {
  // Removing the enable/running guards, escaping, or request-generation guards must fail these tests.
  const ids = ['qualityForm', 'qualityProfileSelect', 'qualityProfileId', 'qualityProfileName',
    'qualityActiveRule', 'qualitySave', 'qualityActivate', 'qualityDryForm', 'qualityDryText',
    'qualityDryButton', 'qualityDryReport', 'qualityCheck', 'qualitySnapshot', 'qualityQualifiedUrl',
    'qualityScanned', 'qualityAccepted', 'qualityRejected', 'qualityUntested', 'qualityInventoryState',
    'qualityProgress', 'qualityResults', 'qualityHistory', 'qualityAudit', ...Object.keys(fields)];
  const elements = Object.fromEntries(ids.map(id => [id, {
    value: '', checked: false, disabled: false, textContent: '', innerHTML: '', listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    async fire(type) { return this.listeners[type]?.({ preventDefault() {}, target: this }); },
  }]));
  const document = { getElementById: id => elements[id] };
  const context = { window: {}, document };
  const script = path.join(__dirname, '../static/quality.js');
  if (fs.existsSync(script)) vm.runInNewContext(fs.readFileSync(script, 'utf8'), context);
  assert.equal(typeof context.window.NodesQuality?.create, 'function', 'quality controller should be available');
  const profile = { id: 'default', name: '默认规则', version: 0, updated_at: null, ...defaults };
  let profiles = { ok: true, active_id: 'default', export_id: 'default', profiles: [profile] };
  let inventory = { ok: true, latest: null, qualified_url: '/nodes/api/export/qualified-proxies?token=secret',
    inventory: { scanned: 3, accepted: 0, rejected: 0, skipped_unprobed: 3, enabled: false,
      profile_id: 'default', version: 0, reasons: {}, results: [
        { identity: 'host:8000', ok: null, latency_ms: null, country: '', country_code: '',
          egress_ip: '', arp_ok: null, arp_status: null, reason: 'disabled', checked_at: null },
      ] }, check: { running: false, completed: 0, total: 0, error: null } };
  const calls = [], errors = [], overrides = {};
  const state = { currentView: 'dashboard' };
  const controller = context.window.NodesQuality.create({ document, state,
    escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch])),
    formatDate: value => value ? new Date(value).toISOString() : '—', toast: (...args) => errors.push(args),
    api: async (url, options = {}) => {
      calls.push({ url, ...options });
      if (overrides[url]) return overrides[url](options);
      if (url === '/api/quality/profiles') return profiles;
      if (url === '/api/inventory') return inventory;
      if (url === '/api/inventory/history') return { ok: true, history: [] };
      if (url === '/api/audit') return { ok: true, entries: [] };
      throw Error(`Unexpected API ${url}`);
    },
  });
  return { elements, state, calls, errors, overrides, controller,
    get profiles() { return profiles; }, setProfiles(v) { profiles = v; },
    get inventory() { return inventory; }, setInventory(v) { inventory = v; },
    async show(view) { state.currentView = view; await controller.onShow(view); },
  };
}

test('disabled quality renders untested counts and prevents probing', async () => {
  const p = fixture(); await p.show('inventory');
  assert.equal(p.elements.qualityAccepted.textContent, 0);
  assert.equal(p.elements.qualityRejected.textContent, 0);
  assert.equal(p.elements.qualityUntested.textContent, 3);
  assert.equal(p.elements.qualityCheck.disabled, true);
  assert.match(p.elements.qualityResults.innerHTML, /未测/);
  assert.equal(p.elements.qualityQualifiedUrl.value, '/nodes/api/export/qualified-proxies?token=secret');
  assert.doesNotMatch(p.elements.qualityInventoryState.textContent, /secret/);
});

test('inventory escapes node, history and audit values and distinguishes pass/fail/untested', async () => {
  const p = fixture();
  p.inventory.inventory = { ...p.inventory.inventory, enabled: true, accepted: 1, rejected: 1, skipped_unprobed: 1,
    results: [{ identity: '<img onerror=x>:80', ok: true, country: '<CN>', latency_ms: 28, checked_at: 1700000000 },
      { identity: 'fail:80', ok: false, reason: '<script>x</script>' }, { identity: 'wait:80', ok: null }] };
  p.overrides['/api/audit'] = async () => ({ entries: [{ at: 1700000000, kind: 'quality', action: 'save_profile', actor: '<admin>', detail: { name: '<bad>' } }] });
  p.overrides['/api/inventory/history'] = async () => ({ history: [{ recorded_at: 1700000000, scanned: 3, accepted: 1, rejected: 1, skipped_unprobed: 1, profile_id: '<rule>', version: 2 }] });
  await p.show('inventory');
  assert.match(p.elements.qualityResults.innerHTML, /通过/); assert.match(p.elements.qualityResults.innerHTML, /未通过/); assert.match(p.elements.qualityResults.innerHTML, /未测/);
  assert.match(p.elements.qualityResults.innerHTML, /&lt;img/); assert.doesNotMatch(p.elements.qualityResults.innerHTML, /<script>|<img/);
  assert.match(p.elements.qualityHistory.innerHTML, /&lt;rule&gt;/); assert.match(p.elements.qualityAudit.innerHTML, /&lt;admin&gt;/);
});

test('a rejected check reports the error and restores the enabled button', async () => {
  const p = fixture(); p.inventory.inventory.enabled = true; await p.show('inventory');
  const req = deferred(); p.overrides['/api/quality/check'] = () => req.promise;
  const event = p.elements.qualityCheck.fire('click');
  assert.equal(p.elements.qualityCheck.disabled, true);
  req.reject(Error('检查启动失败')); await event;
  assert.equal(p.elements.qualityCheck.disabled, false);
  assert.deepEqual(p.errors.at(-1), ['检查启动失败', true]);
});

test('running check displays its own profile and progress and cannot start another', async () => {
  const p = fixture(); p.inventory.inventory.enabled = true;
  p.inventory.check = { running: true, completed: 2, total: 3, error: null, profile_id: 'previous', version: 4 };
  await p.show('inventory');
  assert.equal(p.elements.qualityCheck.disabled, true);
  assert.match(p.elements.qualityProgress.textContent, /2.*3/); assert.match(p.elements.qualityProgress.textContent, /previous.*4/);
});

test('rule save sends flat typed fields without activating and activation uses selected saved id', async () => {
  const p = fixture(); await p.show('rules');
  p.elements.qualityProfileName.value = '宽松规则'; p.elements.qualityEnable.checked = true;
  p.elements.qualityLatency.value = '2000'; p.elements.qualityWorkers.value = '4';
  p.overrides['/api/quality/profiles/default'] = async options => ({ ...p.profiles, profile: { ...p.profiles.profiles[0], ...JSON.parse(options.body), version: 1 } });
  await p.elements.qualityForm.fire('submit');
  const saved = p.calls.find(x => x.url === '/api/quality/profiles/default');
  assert.equal(saved.method, 'PUT'); assert.deepEqual(JSON.parse(saved.body), { name: '宽松规则', ...defaults, proxy_quality_enabled: true, proxy_max_latency_ms: 2000, proxy_quality_workers: 4 });
  assert.equal(p.calls.some(x => x.url === '/api/quality/profiles/activate'), false);
  p.overrides['/api/quality/profiles/activate'] = async () => p.profiles;
  await p.elements.qualityActivate.fire('click');
  assert.deepEqual(JSON.parse(p.calls.find(x => x.url === '/api/quality/profiles/activate').body), { id: 'default' });
});

test('four-second refresh only fetches visible inventory and never resets edited rule fields', async () => {
  const p = fixture(); await p.show('rules'); p.elements.qualityLatency.value = '4321';
  const count = p.calls.length; await p.controller.refresh();
  assert.equal(p.calls.length, count); assert.equal(p.elements.qualityLatency.value, '4321');
  p.state.currentView = 'inventory'; await p.controller.refresh();
  assert.equal(p.elements.qualityLatency.value, '4321');
  assert.equal(p.calls.filter(x => x.url === '/api/quality/profiles').length, 1);
});

test('overlapping inventory refreshes share work', async () => {
  const p = fixture(); p.state.currentView = 'inventory'; const req = deferred();
  p.overrides['/api/inventory'] = () => req.promise;
  const first = p.controller.refresh(), second = p.controller.refresh();
  req.resolve(p.inventory); await Promise.all([first, second]);
  assert.equal(p.calls.filter(x => x.url === '/api/inventory').length, 1);
});

test('late save response never overwrites a newly selected rule', async () => {
  const p = fixture(); p.profiles.profiles.push({ ...p.profiles.profiles[0], id: 'fast', name: '快速', proxy_max_latency_ms: 900 });
  await p.show('rules'); const req = deferred(); p.overrides['/api/quality/profiles/default'] = () => req.promise;
  const save = p.elements.qualityForm.fire('submit');
  p.elements.qualityProfileSelect.value = 'fast'; await p.elements.qualityProfileSelect.fire('change');
  req.resolve({ ...p.profiles, profile: { ...p.profiles.profiles[0], version: 1 } }); await save;
  assert.equal(p.elements.qualityProfileId.value, 'fast'); assert.equal(Number(p.elements.qualityLatency.value), 900);
});

test('dry-run uses the saved selected rule and snapshot restores button on failure', async () => {
  const p = fixture(); await p.show('rules'); p.elements.qualityDryText.value = 'https://user:password@host:80';
  p.overrides['/api/quality/dry-run'] = async () => ({ ok: true, profile_id: 'default', version: 0, report: { scanned: 1, accepted: 0, rejected: 0, skipped_unprobed: 1, enabled: false, results: [] } });
  await p.elements.qualityDryForm.fire('submit');
  assert.deepEqual(JSON.parse(p.calls.find(x => x.url === '/api/quality/dry-run').body), { text: 'https://user:password@host:80', rule: 'default' });
  assert.doesNotMatch(p.elements.qualityDryReport.textContent, /password/); assert.match(p.elements.qualityDryReport.textContent, /未测/);
  p.overrides['/api/inventory/snapshot'] = async () => { throw Error('无法保存'); };
  await p.elements.qualitySnapshot.fire('click'); assert.equal(p.elements.qualitySnapshot.disabled, false); assert.deepEqual(p.errors.at(-1), ['无法保存', true]);
});

test('persisted history and audit ISO timestamps render without breaking inventory refresh', async () => {
  const p = fixture();
  p.overrides['/api/inventory/history'] = async () => ({ history: [{ recorded_at: '2026-10-04T12:00:00+00:00', scanned: 3, accepted: 1, rejected: 1, skipped_unprobed: 1, profile_id: 'default', version: 0 }] });
  p.overrides['/api/audit'] = async () => ({ entries: [{ at: '2026-10-04T12:00:00+00:00', kind: 'quality', action: 'check_complete', detail: {}, actor: 'web' }] });
  await p.show('inventory');
  assert.match(p.elements.qualityHistory.innerHTML, /2026-10-04T12:00:00/);
  assert.match(p.elements.qualityAudit.innerHTML, /2026-10-04T12:00:00/);
  assert.equal(p.errors.length, 0);
});

test('activation serializes profile writes until its response arrives', async () => {
  const p = fixture(); await p.show('rules'); const req = deferred();
  p.overrides['/api/quality/profiles/activate'] = () => req.promise;
  const activation = p.elements.qualityActivate.fire('click');
  assert.equal(p.elements.qualitySave.disabled, true);
  await p.elements.qualityForm.fire('submit');
  assert.equal(p.calls.some(x => x.url === '/api/quality/profiles/default'), false);
  req.resolve(p.profiles); await activation;
  assert.equal(p.elements.qualitySave.disabled, false);
});
