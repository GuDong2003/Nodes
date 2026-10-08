const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

async function fixture(settings) {
  const template = fs.readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
  const elements = Object.fromEntries([...template.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', checked: false, disabled: false, textContent: '', listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; },
    async fire(type) { return this.listeners[type]?.({ target: this, preventDefault() {} }); },
  }]));
  const document = {
    getElementById: id => elements[id],
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const context = { document, window: {}, setInterval() {}, setTimeout() {}, clearTimeout() {},
    fetch: async url => {
      if (url === '/api/settings') return { ok: true, status: 200, json: async () => ({ settings }) };
      // Dashboard/export requests are unrelated to loading and changing captcha settings.
      return new Promise(() => {});
    },
  };
  vm.createContext(context);
  for (const file of ['task-dialog.js', 'quality.js', 'pool-automation.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static', file), 'utf8'), context);
  }
  await new Promise(resolve => setImmediate(resolve));
  return elements;
}

test('loading saved provider replaces a stale built-in endpoint', async () => {
  const page = await fixture({ captcha_provider: 'capmonster', captcha_api_base: 'https://api.2captcha.com' });
  assert.equal(page.captcha_api_base.value, 'https://api.capmonster.cloud');
});

test('changing provider selects its endpoint without changing the API key', async () => {
  const page = await fixture({ captcha_provider: '2captcha', captcha_api_base: 'https://api.2captcha.com', captcha_api_key: 'local-key' });
  for (const [provider, endpoint] of [
    ['yescaptcha', 'https://api.yescaptcha.com'],
    ['capmonster', 'https://api.capmonster.cloud'],
    ['2captcha', 'https://api.2captcha.com'],
    ['browser', ''],
  ]) {
    page.captcha_provider.value = provider;
    await page.captcha_provider.fire('change');
    assert.equal(page.captcha_api_base.value, endpoint);
    assert.equal(page.captcha_api_key.value, 'local-key');
  }
});

test('loading settings preserves a custom compatible endpoint', async () => {
  const page = await fixture({ captcha_provider: 'yescaptcha', captcha_api_base: 'https://solver.example.com/api/v2' });
  assert.equal(page.captcha_api_base.value, 'https://solver.example.com/api/v2');
});
