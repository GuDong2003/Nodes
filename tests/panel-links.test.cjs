const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture() {
  let nav = null;
  let onMutation;
  const document = {
    body: {},
    querySelector: selector => selector === '.nav-list' ? nav : null,
    createElement: tag => ({ tagName: tag }),
  };
  Object.defineProperty(document, 'cookie', { get() { throw Error('Cookies must not be read'); } });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/panel-links.js'), 'utf8'), {
    document,
    MutationObserver: class {
      constructor(callback) { onMutation = callback; }
      observe() {}
    },
  });
  return {
    mount() {
      nav = {
        children: [],
        querySelector(selector) { return this.children.find(child => `#${child.id}` === selector) || null; },
        append(child) { this.children.push(child); },
      };
      onMutation();
      return nav;
    },
    mutate() { onMutation(); },
  };
}

test('adds only a plain credential-free link once the Resin navigation exists', () => {
  const page = fixture();
  const nav = page.mount();
  assert.equal(nav.children.length, 1);
  const link = nav.children[0];
  assert.equal(link.tagName, 'a');
  assert.equal(link.href, 'https://ps.gudong226.com/nodes/');
  assert.equal(link.target, '_blank');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(link.textContent, '打开 Nodes Ops ↗');
});

test('React rerenders do not duplicate links and remounting restores one link', () => {
  const page = fixture();
  const first = page.mount();
  for (let n = 0; n < 5; n++) page.mutate();
  assert.equal(first.children.length, 1);
  const second = page.mount();
  assert.equal(second.children.length, 1);
  assert.equal(second.children[0].href, 'https://ps.gudong226.com/nodes/');
});
