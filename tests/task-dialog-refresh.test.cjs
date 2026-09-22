const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture() {
  const elements = {
    taskDialog: {
      open: false,
      showModal() { this.open = true; },
      close() { this.open = false; },
      addEventListener() {},
    },
    dialogTaskId: { textContent: '' },
    dialogStats: { innerHTML: '' },
    dialogLogs: { innerHTML: '' },
  };
  const document = {
    getElementById(id) { return elements[id]; },
  };
  const context = { window: {}, document };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '../static/task-dialog.js'), 'utf8'),
    context,
  );
  let current = {
    id: 'task-1', completed: 1, requested: 1, successes: 0,
    proxy_count: 0, status: 'running', logs: ['运行中'],
  };
  const calls = [];
  const controller = context.window.NodesTaskDialog.create({
    document,
    state: {},
    escapeHtml: value => String(value),
    statusLabel: value => ({ running: '运行中', success: '成功' }[value] || value),
    api: async pathName => {
      calls.push(pathName);
      return { task: current };
    },
    toast() {},
  });
  return { elements, controller, calls, setTask(value) { current = value; } };
}

test('open task details refreshes while open and stops after close', async () => {
  const page = fixture();
  await page.controller.open('task-1');
  assert.equal(page.calls.length, 1);
  assert.match(page.elements.dialogStats.innerHTML, /运行中/);

  page.setTask({
    id: 'task-1', completed: 1, requested: 1, successes: 1,
    proxy_count: 100, status: 'success', logs: ['任务结束：1\/1 成功'],
  });
  await page.controller.refresh();
  assert.equal(page.calls.length, 2);
  assert.match(page.elements.dialogStats.innerHTML, /成功/);
  assert.match(page.elements.dialogStats.innerHTML, /100/);
  assert.match(page.elements.dialogLogs.innerHTML, /任务结束/);

  page.controller.close();
  await page.controller.refresh();
  assert.equal(page.calls.length, 2);
});
