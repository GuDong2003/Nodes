const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture({ appBase = '', csrf = '', accounts = [] } = {}) {
  let now = Date.parse('2026-10-08T00:00:00Z');
  const timers = [], requests = [], calls = [], cells = [];
  const template = fs.readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
  const elements = Object.fromEntries([...template.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', checked: false, disabled: false, textContent: '', innerHTML: '', open: false, listeners: {},
    classList: { toggle() {}, remove() {} },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    async fire(type) { return this.listeners[type]?.({ preventDefault() {}, target: this }); },
    querySelectorAll() { return []; }, querySelector() { return null; },
  }]));
  const document = {
    getElementById: id => elements[id], querySelector: selector => selector.includes('csrf-token') ? { content: csrf }
      : selector.includes('app-base') ? { content: appBase } : null,
    querySelectorAll: selector => selector.includes('[data-expires-at]') ? cells : [],
  };
  const context = { document, window: {}, Intl, setTimeout() {}, clearTimeout() {},
    setInterval(fn, delay) { timers.push({ fn, delay }); },
    Date: class extends Date { static now() { return now; } },
    fetch: async (url, options = {}) => {
      requests.push(url); calls.push({ url, ...options });
      if (url === `${appBase}/api/accounts`) return { ok: true, status: 200, json: async () => ({ accounts }) };
      if (url.startsWith(`${appBase}/api/pool/automation`)) {
        const data = {
          ok: true, settings: { enabled: false, target_nodes: 80, target_bandwidth_gb: 0, interval_minutes: 30, max_register_per_round: 5 },
          capacity: { live_accounts: 2, live_slots: 20, bandwidth_remaining: 2000000000, target_bandwidth: 0,
            shortage_slots: 60, shortage_bandwidth: 0, needed_accounts: 2, unknown_bandwidth_accounts: 0 },
          status: { running: url.endsWith('/check'), worker_enabled: true, last_check_at: null, next_check_at: null,
            outcome: 'disabled', message: '自动补号已关闭', registered_count: 0, synced: 0, failed: 0 },
        };
        return { ok: true, status: 200, json: async () => data };
      }
      return new Promise(() => {});
    },
  };
  vm.createContext(context);
  const scripts = [...template.matchAll(/<script[^>]*filename='([^']+)'/g)].map(([, name]) => name);
  for (const file of scripts) {
    const source = path.join(__dirname, '../static', file);
    vm.runInContext(fs.readFileSync(source, 'utf8'), context);
  }
  return { context, elements, timers, requests, calls,
    advance(value) { now = Date.parse(value); },
    cell(expiresAt) {
      const value = { dataset: { expiresAt }, textContent: '', expired: false,
        classList: { toggle(name, on) { if (name === 'is-expired') value.expired = on; } } };
      cells.push(value); return value;
    },
  };
}

test('expiry ignores stale cached days and uses the absolute time in Shanghai', () => {
  // Reusing days_remaining or the browser timezone must fail this assertion.
  const p = fixture();
  const output = p.context.formatExpiry({ expires_at: '2026-10-09T02:00:00Z', days_remaining: 19, expired: true });
  assert.equal(output, '2026/10/09 10:00 · 剩余 1 天 2 小时');
});

test('expiry handles sub-day boundaries and keeps the date after expiration', () => {
  const p = fixture();
  for (const [expires_at, want] of [
    ['2026-10-08T23:00:00Z', '2026/10/09 07:00 · 剩余 23 小时'],
    ['2026-10-08T00:59:59Z', '2026/10/08 08:59 · 剩余不足 1 小时'],
    ['2026-10-08T00:00:00Z', '2026/10/08 08:00 · 已过期'],
    ['2026-10-07T00:00:00Z', '2026/10/07 08:00 · 已过期'],
    ['not-a-date', '未知'], [null, '未知'], ['', '未知'], [123, '未知'], [true, '未知'],
    ['2026-10-09T00:00:00', '未知'],
  ]) assert.equal(p.context.formatExpiry({ expires_at, days_remaining: 7 }), want);
});

test('account list and detail render an expiry target that the timer can update', async () => {
  const account = { email: 'fixture@example.test', verified: true, expires_at: '2026-10-09T02:00:00Z',
    expired: false, days_remaining: 19, bandwidth_used: 0, bandwidth_total: 1000000000,
    bandwidth_remaining: 1000000000, proxy_count: 20, has_api_key: false };
  const p = fixture({ accounts: [account] });
  await p.context.loadAccounts(); p.context.fillAccount(account);
  for (const rendered of [p.elements.accountsBody.innerHTML, p.elements.accountDialogStats.innerHTML]) {
    assert.match(rendered, /data-expires-at="2026-10-09T02:00:00Z"/);
    assert.match(rendered, /2026\/10\/09 10:00 · 剩余 1 天 2 小时/);
    assert.doesNotMatch(rendered, /19天/);
  }
});

test('the existing timer updates visible expiry cells without reloading accounts or editing fields', () => {
  // A timer that reloads accounts, refills details, or omits countdown updates is a regression.
  const p = fixture();
  const row = p.cell('2026-10-08T01:00:00Z');
  const detail = p.cell('2026-10-08T00:30:00Z');
  p.elements.acc_notes.value = '尚未保存的备注';
  p.elements.accountSelectAll.checked = true;
  p.elements.accountsBody.innerHTML = '<selected-row />';
  p.elements.accountDialog.open = true;
  const timer = p.timers.find(item => item.delay === 4000);
  assert.ok(timer);
  timer.fn();
  assert.match(row.textContent, /剩余 1 小时/);
  assert.match(detail.textContent, /不足 1 小时/);
  p.advance('2026-10-08T01:00:00Z'); timer.fn();
  assert.equal(row.textContent, '2026/10/08 09:00 · 已过期');
  assert.equal(detail.expired, true);
  assert.equal(p.elements.acc_notes.value, '尚未保存的备注');
  assert.equal(p.elements.accountSelectAll.checked, true);
  assert.equal(p.elements.accountsBody.innerHTML, '<selected-row />');
  assert.equal(p.requests.some(url => url.startsWith('/api/accounts')), false);
});

test('the application loads automation and polls it only while the dashboard is visible', async () => {
  const p = fixture(); await new Promise(resolve => setImmediate(resolve));
  const count = () => p.requests.filter(url => url === '/api/pool/automation').length;
  assert.equal(count(), 1);
  const timer = p.timers.find(item => item.delay === 4000);
  timer.fn(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(count(), 2);
  vm.runInContext("state.currentView = 'accounts'", p.context);
  timer.fn(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(count(), 2);
  p.context.showView('dashboard'); await new Promise(resolve => setImmediate(resolve));
  assert.equal(count(), 3);
});

test('the manual check uses the mounted application API with CSRF and saved disabled settings', async () => {
  const p = fixture({ appBase: '/nodes', csrf: 'fixture-token' });
  await new Promise(resolve => setImmediate(resolve));
  const checking = p.elements.ensureCapacityButton.fire('click');
  const check = p.calls.find(call => call.method === 'POST');
  assert.equal(check.url, '/nodes/api/pool/automation/check');
  assert.equal(check.headers['X-CSRF-Token'], 'fixture-token');
  assert.equal(check.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(check.body), {});
  await checking;
});
