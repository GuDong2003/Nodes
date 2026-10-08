const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const defaults = { enabled: false, target_nodes: 80, target_bandwidth_gb: 0, interval_minutes: 30, max_register_per_round: 5 };
const fields = { poolAutomationEnabled: 'enabled', poolTargetNodes: 'target_nodes',
  poolTargetBandwidth: 'target_bandwidth_gb', poolCheckInterval: 'interval_minutes', poolMaxRegistrations: 'max_register_per_round' };
function deferred() { let resolve; let reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function fixture() {
  const ids = ['poolAutomationForm', 'poolAutomationSave', 'ensureCapacityButton', 'poolAutomationStatus',
    'poolAutomationProgress', 'poolAutomationLastCheck', 'poolAutomationNextCheck', 'poolAutomationWorker',
    'poolAutomationEditHint', 'poolAccounts', 'poolSlots', 'poolBandwidth', 'poolNeeded', 'poolAuto',
    'poolCapacityHint', ...Object.keys(fields)];
  const elements = Object.fromEntries(ids.map(id => [id, {
    value: '', checked: false, disabled: false, textContent: '', innerHTML: '', className: '', title: '', listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    async fire(type) { return this.listeners[type]?.({ preventDefault() {}, target: this }); },
  }]));
  const context = { window: {} };
  const source = path.join(__dirname, '../static/pool-automation.js');
  if (fs.existsSync(source)) vm.runInNewContext(fs.readFileSync(source, 'utf8'), context);
  assert.equal(typeof context.window.NodesPoolAutomation?.create, 'function', 'pool automation controller should be available');
  let data = {
    ok: true, settings: { ...defaults },
    capacity: { live_accounts: 3, live_slots: 84, bandwidth_remaining: 6500000000, target_bandwidth: 0,
      shortage_slots: 0, shortage_bandwidth: 0, needed_accounts: 0, unknown_bandwidth_accounts: 0 },
    status: { running: false, worker_enabled: true, last_check_at: '2026-10-08T00:00:00Z', next_check_at: null,
      outcome: 'disabled', message: '自动补号未启用', registered_count: 0, synced: 3, failed: 0 },
  };
  const calls = [], toasts = [], handlers = {};
  const state = { currentView: 'dashboard' };
  const controller = context.window.NodesPoolAutomation.create({
    document: { getElementById: id => elements[id] }, state,
    formatBytes: value => value == null ? '—' : `${(value / 1e9).toFixed(2)} GB`,
    formatDate: value => value ? new Date(value).toISOString() : '—',
    toast: (...args) => toasts.push(args),
    api: async (url, options = {}) => {
      calls.push({ url, ...options });
      const method = options.method || 'GET';
      if (handlers[method]) return handlers[method](url, options);
      if (url === '/api/pool/automation' && method === 'GET') return structuredClone(data);
      if (url === '/api/pool/automation' && method === 'PUT') {
        data.settings = JSON.parse(options.body); return structuredClone(data);
      }
      if (url === '/api/pool/automation/check' && method === 'POST') {
        data.status = { ...data.status, running: true, outcome: 'running', message: '正在同步账号用量' };
        return structuredClone(data);
      }
      throw Error(`Unexpected API ${method} ${url}`);
    },
  });
  return { elements, calls, toasts, handlers, state, controller,
    get data() { return data; },
    async edit(id, value, force = false) {
      if (elements[id].disabled && !force) return false;
      if (typeof value === 'boolean') elements[id].checked = value;
      else elements[id].value = String(value);
      await elements.poolAutomationForm.fire('input'); return true;
    },
  };
}

test('initial loading prevents saves and checks until saved settings are known', async () => {
  const p = fixture(); const request = deferred(); p.handlers.GET = () => request.promise;
  const loading = p.controller.refresh();
  assert.equal(p.elements.poolAutomationEnabled.checked, false);
  for (const id of [...Object.keys(fields), 'poolAutomationSave', 'ensureCapacityButton']) assert.equal(p.elements[id].disabled, true);
  await p.elements.poolAutomationForm.fire('submit'); await p.elements.ensureCapacityButton.fire('click');
  assert.equal(p.calls.length, 1);
  assert.equal(await p.edit('poolTargetNodes', 99), false);
  request.resolve({ ...p.data, settings: { ...defaults, enabled: true, target_nodes: 120 } }); await loading;
  assert.equal(p.elements.poolAutomationEnabled.checked, true);
  assert.equal(Number(p.elements.poolTargetNodes.value), 120);
  assert.equal(p.elements.poolAutomationSave.disabled, false);
});

test('capacity displays server totals once per account and keeps unknown capacity visible', async () => {
  const p = fixture(); p.data.capacity.unknown_bandwidth_accounts = 1; await p.controller.refresh();
  assert.equal(String(p.elements.poolAccounts.textContent), '3');
  assert.match(p.elements.poolSlots.textContent, /84/);
  assert.match(p.elements.poolBandwidth.textContent, /6\.50 GB/);
  assert.match(p.elements.poolCapacityHint.textContent, /1.*未知/);
  assert.match(p.elements.poolAutomationLastCheck.textContent, /2026-10-08/);
  assert.match(p.elements.poolAutomationNextCheck.textContent, /—/);
});

test('manual checking with automation disabled only requests a check without enabling registration', async () => {
  const p = fixture(); await p.controller.refresh();
  assert.equal(p.elements.ensureCapacityButton.disabled, false);
  await p.elements.ensureCapacityButton.fire('click');
  const request = p.calls.find(item => item.method === 'POST');
  assert.equal(request.url, '/api/pool/automation/check');
  assert.deepEqual(JSON.parse(request.body), {});
  assert.equal(p.calls.some(item => item.method === 'PUT'), false);
  assert.equal(p.elements.poolAutomationEnabled.checked, false);
  assert.equal(p.elements.ensureCapacityButton.disabled, true);
  assert.match(p.elements.poolAutomationStatus.textContent, /同步/);
});

test('saving sends flat validated settings and accepts bandwidth-only replenishment', async () => {
  const p = fixture(); await p.controller.refresh();
  await p.edit('poolAutomationEnabled', true); await p.edit('poolTargetNodes', 0);
  await p.edit('poolTargetBandwidth', 12.5); await p.edit('poolCheckInterval', 15); await p.edit('poolMaxRegistrations', 2);
  assert.equal(p.elements.ensureCapacityButton.disabled, true);
  await p.elements.poolAutomationForm.fire('submit');
  const request = p.calls.find(item => item.method === 'PUT');
  assert.equal(request.url, '/api/pool/automation');
  assert.deepEqual(JSON.parse(request.body), { enabled: true, target_nodes: 0, target_bandwidth_gb: 12.5, interval_minutes: 15, max_register_per_round: 2 });
  assert.equal(p.elements.ensureCapacityButton.disabled, false);
});

test('enabled automation requires at least one positive threshold', async () => {
  const p = fixture(); await p.controller.refresh();
  await p.edit('poolAutomationEnabled', true); await p.edit('poolTargetNodes', 0); await p.edit('poolTargetBandwidth', 0);
  await p.elements.poolAutomationForm.fire('submit');
  assert.equal(p.calls.some(item => item.method === 'PUT'), false);
  assert.match(p.toasts.at(-1)[0], /至少.*阈值/);
  await p.edit('poolAutomationEnabled', false); await p.elements.poolAutomationForm.fire('submit');
  assert.equal(p.calls.filter(item => item.method === 'PUT').length, 1);
});

test('invalid ranges, fractions, empty values and non-finite traffic never reach the server', async () => {
  // Removing finite/integer/range checks must reject these user-entered values.
  for (const [id, value] of [
    ['poolTargetNodes', -1], ['poolTargetNodes', 0.5], ['poolTargetNodes', 100001], ['poolTargetNodes', ''],
    ['poolTargetBandwidth', -1], ['poolTargetBandwidth', 'Infinity'], ['poolTargetBandwidth', 100001],
    ['poolTargetBandwidth', 'NaN'], ['poolTargetBandwidth', ''], ['poolCheckInterval', 0],
    ['poolCheckInterval', 1441], ['poolCheckInterval', 1.5], ['poolMaxRegistrations', 0],
    ['poolMaxRegistrations', 6], ['poolMaxRegistrations', 1.5],
  ]) {
    const p = fixture(); await p.controller.refresh(); await p.edit(id, value);
    await p.elements.poolAutomationForm.fire('submit');
    assert.equal(p.calls.some(item => item.method === 'PUT'), false, `${id}=${value}`);
    assert.equal(p.toasts.at(-1)[1], true);
  }
});

test('dashboard polling updates status without replacing unsaved settings', async () => {
  const p = fixture(); await p.controller.refresh(); await p.edit('poolTargetNodes', 130);
  p.data.capacity.live_slots = 42; p.data.status.message = '同步失败，已跳过补号';
  p.data.status.outcome = 'sync_failed'; await p.controller.refresh();
  assert.equal(Number(p.elements.poolTargetNodes.value), 130);
  assert.match(p.elements.poolSlots.textContent, /42/);
  assert.equal(p.elements.poolAutomationStatus.textContent, '同步失败，已跳过补号');
  assert.equal(p.elements.ensureCapacityButton.disabled, true);
  assert.match(p.elements.poolAutomationEditHint.textContent, /未保存/);
  const before = p.calls.length; p.state.currentView = 'accounts'; await p.controller.refresh();
  assert.equal(p.calls.length, before);
});

test('a late initial response preserves an input event that arrived during loading', async () => {
  const p = fixture(); const request = deferred(); p.handlers.GET = () => request.promise;
  const loading = p.controller.refresh(); await p.edit('poolTargetNodes', 135, true);
  request.resolve(p.data); await loading;
  assert.equal(Number(p.elements.poolTargetNodes.value), 135);
  assert.match(p.elements.poolAutomationEditHint.textContent, /未保存/);
});

test('a stale poll cannot roll back a newer save or its capacity snapshot', async () => {
  const p = fixture(); await p.controller.refresh(); const request = deferred();
  p.handlers.GET = () => request.promise; const polling = p.controller.refresh();
  await p.edit('poolTargetNodes', 110);
  p.data.capacity.live_slots = 125; await p.elements.poolAutomationForm.fire('submit');
  request.resolve({ ...p.data, settings: defaults, capacity: { ...p.data.capacity, live_slots: 1 } }); await polling;
  assert.equal(Number(p.elements.poolTargetNodes.value), 110);
  assert.match(p.elements.poolSlots.textContent, /125/);
});

test('a late save response keeps edits made while saving available for the next save', async () => {
  const p = fixture(); await p.controller.refresh(); await p.edit('poolTargetNodes', 110);
  const request = deferred(); p.handlers.PUT = () => request.promise;
  const saving = p.elements.poolAutomationForm.fire('submit');
  await p.edit('poolTargetNodes', 140);
  request.resolve({ ...p.data, settings: { ...defaults, target_nodes: 110 } }); await saving;
  assert.equal(Number(p.elements.poolTargetNodes.value), 140);
  assert.equal(p.elements.poolAutomationSave.disabled, false);
  assert.equal(p.elements.ensureCapacityButton.disabled, true);
  delete p.handlers.PUT; await p.elements.poolAutomationForm.fire('submit');
  assert.equal(JSON.parse(p.calls.filter(item => item.method === 'PUT').at(-1).body).target_nodes, 140);
});

test('running checks prevent duplicate checks while allowing automation to be disabled and saved', async () => {
  const p = fixture(); p.data.settings.enabled = true; p.data.status.running = true;
  p.data.status.outcome = 'running'; await p.controller.refresh();
  await p.elements.ensureCapacityButton.fire('click');
  assert.equal(p.calls.some(item => item.method === 'POST'), false);
  await p.edit('poolAutomationEnabled', false); await p.elements.poolAutomationForm.fire('submit');
  assert.equal(JSON.parse(p.calls.find(item => item.method === 'PUT').body).enabled, false);
});

test('environment-disabled workers and server errors render as plain visible text', async () => {
  const p = fixture(); p.data.status.worker_enabled = false;
  p.data.status.outcome = 'error'; p.data.status.message = '<img src=x onerror=alert(1)> 同步失败';
  await p.controller.refresh();
  assert.match(p.elements.poolAutomationWorker.textContent, /环境.*停用/);
  assert.equal(p.elements.poolAutomationStatus.textContent, '<img src=x onerror=alert(1)> 同步失败');
  assert.equal(p.elements.poolAutomationStatus.innerHTML, '');
});

test('load and check failures preserve the form and allow retry', async () => {
  const p = fixture(); p.handlers.GET = async () => { throw Error('暂时无法读取'); };
  await p.controller.refresh();
  assert.equal(p.elements.poolAutomationSave.disabled, true);
  assert.match(p.elements.poolAutomationStatus.textContent, /暂时无法读取/);
  delete p.handlers.GET; await p.controller.refresh();
  p.handlers.POST = async () => { throw Error('已有同步任务运行'); };
  await p.elements.ensureCapacityButton.fire('click');
  assert.equal(p.elements.ensureCapacityButton.disabled, false);
  assert.deepEqual(p.toasts.at(-1), ['已有同步任务运行', true]);
  assert.equal(Number(p.elements.poolTargetNodes.value), 80);
});
