const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
const APP_BASE = (document.querySelector('meta[name="app-base"]')?.content || '').replace(/\/$/, '');
const state = { dashboard: null, exports: null, settings: null, pool: null, currentView: 'dashboard', openTaskId: null };
const titles = {
  dashboard: ['仪表盘', '注册任务与资源状态'],
  accounts: ['账号管理', '导入删除账号，同步过期时间和剩余流量'],
  mail: ['邮箱服务', 'Cloudflare Temp Email / 云芯 / YYDS 接口，保存后同步到注册进程'],
  captcha: ['打码接口', '2Captcha / YesCaptcha / CapMonster / 浏览器打码'],
  proxies: ['代理输出', '出口代理设置与导出文件'],
  rules: ['质量规则', '保存、启用规则与小样本试测'],
  inventory: ['节点库存', '节点质量、检查进度与历史记录'],
  tasks: ['任务日志', '历史任务进度与执行结果'],
};

function appUrl(path) {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${APP_BASE}${normalized}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[ch]);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  if ((options.method || 'GET') !== 'GET') headers['X-CSRF-Token'] = csrf;
  const response = await fetch(appUrl(path), { ...options, headers, credentials: 'same-origin' });
  if (response.status === 401) {
    window.location.href = appUrl('/login');
    throw new Error('会话已过期');
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '未知';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'Asia/Shanghai', hourCycle: 'h23',
  }).format(date);
}

function statusLabel(status) {
  return ({ queued: '排队中', running: '运行中', success: '成功', partial: '部分成功', failed: '失败', interrupted: '已中断' })[status] || status;
}

function statusChip(status) {
  return `<span class="status-chip ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`;
}

function renderPool(pool) {
  if (!pool) return;
  state.pool = pool;
  const live = pool.live_slots || 0;
  const accounts = pool.live_accounts || 0;
  const slots = `${live} 条（${accounts} 个账号）`;
  document.getElementById('poolResinUrl').value = pool.subscription_url || '';
  document.getElementById('poolGptSample').value = pool.gpt_gateway_sample || '';
  document.getElementById('metricProxiesNote').textContent = `Resin 订阅 ${slots}`;
  renderGatewayFormat();
}

function renderGatewayFormat() {
  const pool = state.pool || {};
  const format = document.getElementById('poolGatewayFormat')?.value || 'http';
  const fields = {
    http: 'gpt_subscription_url',
    socks5: 'socks5_subscription_url',
    clash: 'clash_subscription_url',
    shadowrocket: 'ladder_subscription_url',
  };
  const input = document.getElementById('poolGatewayUrl');
  if (input) input.value = pool[fields[format]] || '';
  const button = document.getElementById('downloadGatewayButton');
  if (button) button.textContent = format === 'clash' ? '下载 YAML' : '打开';
}

async function copyField(id) {
  const value = document.getElementById(id)?.value || '';
  if (!value) return;
  await navigator.clipboard.writeText(value);
  toast('已复制');
}

let toastTimer;
function toast(message, error = false) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.className = 'toast'; }, 3200);
}

const taskDialog = window.NodesTaskDialog.create({
  document, state, api, toast, escapeHtml, statusLabel,
});
const quality = window.NodesQuality.create({
  document, state, api, toast, escapeHtml, formatDate,
});
const poolAutomation = window.NodesPoolAutomation.create({
  document, state, api, toast, formatBytes, formatDate,
});

function showView(view) {
  state.currentView = view;
  document.querySelectorAll('.view').forEach(item => item.classList.toggle('active', item.id === `view-${view}`));
  document.querySelectorAll('.nav-item[data-view]').forEach(item => item.classList.toggle('active', item.dataset.view === view));
  document.getElementById('pageTitle').textContent = titles[view][0];
  document.getElementById('pageSubtitle').textContent = titles[view][1];
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarScrim').classList.remove('show');
  if (view === 'accounts') loadAccounts();
  if (view === 'mail' || view === 'captcha' || view === 'proxies') loadSettings();
  if (view === 'proxies') loadExports();
  if (view === 'tasks') loadTasks();
  quality.onShow(view);
  poolAutomation.onShow(view);
}

function renderTasks(tasks, target, compact = false) {
  const body = document.getElementById(target);
  if (!tasks.length) {
    body.innerHTML = `<tr><td colspan="${compact ? 5 : 7}" class="empty-cell">暂无任务</td></tr>`;
    return;
  }
  body.innerHTML = tasks.map(task => compact ? `
    <tr class="clickable" data-task-id="${escapeHtml(task.id)}">
      <td title="${escapeHtml(task.id)}">${escapeHtml(task.id)}</td>
      <td>${task.completed}/${task.requested}</td>
      <td>${task.proxy_count || 0}</td>
      <td>${statusChip(task.status)}</td>
      <td>${formatDate(task.started_at || task.created_at)}</td>
    </tr>` : `
    <tr class="clickable" data-task-id="${escapeHtml(task.id)}">
      <td title="${escapeHtml(task.id)}">${escapeHtml(task.id)}</td>
      <td>${task.requested}</td><td>${task.completed}</td><td>${task.successes}</td>
      <td>${task.proxy_count || 0}</td><td>${statusChip(task.status)}</td>
      <td>${formatDate(task.started_at || task.created_at)}</td>
    </tr>`).join('');
  body.querySelectorAll('[data-task-id]').forEach(row => row.addEventListener('click', () => openTask(row.dataset.taskId)));
}

async function refreshDashboard() {
  try {
    const data = await api('/api/dashboard');
    state.dashboard = data;
    document.getElementById('metricAccounts').textContent = data.summary.accounts;
    document.getElementById('metricVerified').textContent = data.summary.verified;
    document.getElementById('metricProxies').textContent = data.summary.proxies;
    document.getElementById('metricAccountsNote').textContent = `${data.summary.successful_accounts} 个完整产出`;
    document.getElementById('metricVerifiedNote').textContent = `${data.summary.accounts ? Math.round(data.summary.verified / data.summary.accounts * 100) : 0}% 验证率`;
    document.getElementById('metricProxiesNote').textContent = '本地加密链接汇总';
    document.getElementById('metricChain').textContent = '已配置';
    document.getElementById('runtimeMail').textContent = data.chain.mail;
    document.getElementById('runtimeCaptcha').textContent = data.chain.captcha;
    document.getElementById('chainCaptcha').textContent = data.chain.captcha;
    document.getElementById('chainMail').textContent = data.chain.mail;
    document.getElementById('chainProxy').textContent = data.chain.proxy === 'enabled' ? '出口代理开' : (data.chain.proxy === 'configured' ? '已产出' : '等待产出');
    document.getElementById('mailProviderName') && (document.getElementById('mailProviderName').textContent = data.chain.mail);
    renderPool(data.pool);
    renderTasks(data.tasks, 'recentTasksBody', true);
    const active = Boolean(data.active_task);
    const activity = document.getElementById('activity');
    activity.classList.toggle('running', active);
    activity.querySelector('span:last-child').textContent = active ? `任务 ${data.active_task} 运行中` : '无活动任务';
    document.getElementById('startButton').disabled = active;
    document.getElementById('startButton').textContent = active ? '任务运行中' : '启动任务';
  } catch (error) {
    toast(error.message, true);
  }
}

function formatBytes(value) {
  if (value == null || value === '') return '—';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '—';
  if (amount >= 1e12) return `${(amount / 1e12).toFixed(2)} TB`;
  if (amount >= 1e9) return `${(amount / 1e9).toFixed(2)} GB`;
  if (amount >= 1e6) return `${(amount / 1e6).toFixed(1)} MB`;
  if (amount >= 1e3) return `${(amount / 1e3).toFixed(0)} KB`;
  return `${amount} B`;
}

function accountExpiryTime(account) {
  const value = account.expires_at;
  if (typeof value !== 'string' || !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return NaN;
  return new Date(value).getTime();
}

function formatExpiry(account) {
  const expiresAt = accountExpiryTime(account);
  if (!Number.isFinite(expiresAt)) return '未知';
  const date = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'Asia/Shanghai', hourCycle: 'h23',
  }).format(new Date(expiresAt));
  const remaining = expiresAt - Date.now();
  if (remaining <= 0) return `${date} · 已过期`;
  const totalHours = Math.floor(remaining / 3600000);
  if (totalHours < 1) return `${date} · 剩余不足 1 小时`;
  const days = Math.floor(totalHours / 24);
  const duration = days ? `${days} 天 ${totalHours % 24} 小时` : `${totalHours} 小时`;
  return `${date} · 剩余 ${duration}`;
}

function isAccountExpired(account) {
  return accountExpiryTime(account) <= Date.now();
}

function refreshAccountExpiries() {
  document.querySelectorAll('#view-accounts.active [data-expires-at], #accountDialog[open] [data-expires-at]').forEach(element => {
    const account = { expires_at: element.dataset.expiresAt };
    element.textContent = formatExpiry(account);
    element.classList.toggle('is-expired', isAccountExpired(account));
  });
}

function selectedAccountEmails() {
  return Array.from(document.querySelectorAll('#accountsBody .account-select:checked')).map(item => item.value);
}

function updateDeleteSelectedState() {
  const button = document.getElementById('deleteSelectedAccounts');
  const selected = selectedAccountEmails();
  button.disabled = selected.length === 0;
  button.textContent = selected.length ? `删除所选 (${selected.length})` : '删除所选';
}

async function loadAccounts() {
  try {
    const data = await api('/api/accounts');
    const body = document.getElementById('accountsBody');
    body.innerHTML = data.accounts.length ? data.accounts.map(account => `
      <tr class="clickable" data-email="${escapeHtml(account.email)}">
        <td class="check-col"><input type="checkbox" class="account-select" value="${escapeHtml(account.email)}"></td>
        <td title="${escapeHtml(account.email)}">${escapeHtml(account.email)}</td>
        <td>${account.verified ? statusChip('success') : statusChip('failed')}</td>
        <td data-expires-at="${escapeHtml(account.expires_at || '')}" class="${isAccountExpired(account) ? 'is-expired' : ''}">${escapeHtml(formatExpiry(account))}</td>
        <td title="${escapeHtml(formatBytes(account.bandwidth_used))} / ${escapeHtml(formatBytes(account.bandwidth_total))}">${escapeHtml(formatBytes(account.bandwidth_remaining))}</td>
        <td>${account.proxy_count}</td>
        <td>${account.has_api_key ? '<span class="status-chip success">已保存</span>' : '<span class="status-chip queued">未创建</span>'}</td>
        <td class="row-actions">
          <button type="button" class="text-button account-edit" data-email="${escapeHtml(account.email)}">查看</button>
          <button type="button" class="text-button danger-text account-delete" data-email="${escapeHtml(account.email)}">删除</button>
        </td>
      </tr>`).join('') : '<tr><td colspan="8" class="empty-cell">暂无账号</td></tr>';
    document.getElementById('accountSelectAll').checked = false;
    updateDeleteSelectedState();
    body.querySelectorAll('tr[data-email]').forEach(row => {
      row.addEventListener('click', event => {
        if (event.target.closest('button, input')) return;
        openAccount(row.dataset.email);
      });
    });
    body.querySelectorAll('.account-edit').forEach(button => {
      button.addEventListener('click', event => {
        event.stopPropagation();
        openAccount(button.dataset.email);
      });
    });
    body.querySelectorAll('.account-delete').forEach(button => {
      button.addEventListener('click', event => {
        event.stopPropagation();
        deleteAccounts([button.dataset.email]);
      });
    });
    body.querySelectorAll('.account-select').forEach(box => {
      box.addEventListener('click', event => event.stopPropagation());
      box.addEventListener('change', updateDeleteSelectedState);
    });
  } catch (error) { toast(error.message, true); }
}

function setField(id, value) {
  const element = document.getElementById(id);
  if (element) element.value = value ?? '';
}

function renderPermissions(catalog, selected) {
  const selectedSet = new Set(selected || []);
  const root = document.getElementById('accountPermissions');
  const structure = catalog?.structure || {};
  const groups = [];
  ['account', 'product'].forEach(kind => {
    (structure[kind]?.sub_types || []).forEach(group => groups.push(group));
  });
  if (!groups.length) {
    root.innerHTML = '<p class="empty-cell">没有权限目录</p>';
    return;
  }
  root.innerHTML = groups.map(group => `
    <article class="perm-group">
      <h4>${escapeHtml(group.name || '')}</h4>
      <p>${escapeHtml(group.description || '')}</p>
      ${(group.permissions || []).map(permission => `
        <label class="check-row">
          <input type="checkbox" name="acc_permission" value="${escapeHtml(permission.id)}" ${selectedSet.has(permission.id) ? 'checked' : ''}>
          <span>${escapeHtml(permission.name)} · ${escapeHtml(permission.id)}</span>
        </label>`).join('')}
    </article>`).join('');
}

function fillAccount(account) {
  state.account = account;
  document.getElementById('accountDialogEmail').textContent = account.email || '';
  document.getElementById('accountDialogStats').innerHTML = `
    <div><span>验证</span><strong>${account.verified ? '已验证' : '未验证'}</strong></div>
    <div><span>到期（北京时间）</span><strong data-expires-at="${escapeHtml(account.expires_at || '')}" class="${isAccountExpired(account) ? 'is-expired' : ''}">${escapeHtml(formatExpiry(account))}</strong></div>
    <div><span>剩余流量</span><strong>${escapeHtml(formatBytes(account.bandwidth_remaining))}</strong></div>
    <div><span>已用 / 总量</span><strong>${escapeHtml(formatBytes(account.bandwidth_used))} / ${escapeHtml(formatBytes(account.bandwidth_total))}</strong></div>`;
  setField('acc_email', account.email);
  setField('acc_password', account.password);
  setField('acc_account_id', account.account_id);
  setField('acc_notes', account.notes);
  setField('acc_access_token', account.access_token);
  setField('acc_proxy_username', account.proxy_username);
  setField('acc_proxy_password', account.proxy_password);
  setField('acc_proxy_url_template', account.api?.proxy_url_template || '');
  setField('acc_account_key', account.account_key);
  setField('acc_api_key_name', account.api_key_name);
  setField('acc_api_key_id', account.api_key_id);
  setField('acc_api_token', account.api_token);
  setField('acc_public_proxy_list', account.api?.public_proxy_list || '');
  setField('acc_curl_public', account.api?.curl_public_proxy_list || '');
  setField('acc_dashboard_overview', account.api?.dashboard_overview || '');
  const selected = (account.permissions && account.permissions.length)
    ? account.permissions
    : (account.permission_catalog?.allowed_permissions || []);
  renderPermissions(account.permission_catalog, selected);
}

function collectAccountPayload() {
  const permissions = Array.from(document.querySelectorAll('input[name="acc_permission"]:checked')).map(item => item.value);
  const subaccount = document.getElementById('acc_account_id').value.trim();
  return {
    password: document.getElementById('acc_password').value,
    access_token: document.getElementById('acc_access_token').value,
    account_id: subaccount,
    notes: document.getElementById('acc_notes').value,
    proxy_username: document.getElementById('acc_proxy_username').value,
    proxy_password: document.getElementById('acc_proxy_password').value,
    account_key: document.getElementById('acc_account_key').value,
    api_key_name: document.getElementById('acc_api_key_name').value,
    api_key_id: document.getElementById('acc_api_key_id').value,
    api_token: document.getElementById('acc_api_token').value,
    permissions,
    allowed_subaccounts: subaccount ? [subaccount] : [],
  };
}

async function openAccount(email) {
  try {
    const { account } = await api(`/api/accounts/${encodeURIComponent(email)}`);
    fillAccount(account);
    document.getElementById('accountDialog').showModal();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadExports() {
  try {
    const data = await api('/api/exports');
    state.exports = data;
    const list = document.getElementById('proxyFiles');
    list.innerHTML = data.proxies.length ? data.proxies.map(file => `
      <div class="file-row"><span class="file-name">${escapeHtml(file.name)}</span>
      <span class="file-meta">${file.count} 条</span><span class="file-meta">${formatDate(file.modified_at)}</span>
      <a class="download-link proxy-file-download" data-file="${escapeHtml(file.name)}" href="${appUrl('/download/proxies/' + encodeURIComponent(file.name))}">下载</a></div>`).join('') : '<div class="empty-cell">暂无代理文件</div>';
    updateProxyFileLinks();
    const latest = data.accounts[0];
    const button = document.getElementById('downloadLatestAccounts');
    button.disabled = !latest;
    button.onclick = () => { if (latest) window.location.href = appUrl(`/download/accounts/${encodeURIComponent(latest.name)}`); };
  } catch (error) { toast(error.message, true); }
}

function updateProxyFileLinks() {
  const format = document.getElementById('proxyFileFormat')?.value || 'http';
  document.querySelectorAll('.proxy-file-download').forEach(link => {
    const name = link.dataset.file || '';
    const suffix = format === 'http' ? '' : `?format=${encodeURIComponent(format)}`;
    link.href = appUrl(`/download/proxies/${encodeURIComponent(name)}${suffix}`);
    link.textContent = format === 'http' ? '下载' : `下载 ${format}`;
  });
}

async function loadTasks() {
  try {
    const data = await api('/api/tasks');
    renderTasks(data.tasks, 'allTasksBody', false);
  } catch (error) { toast(error.message, true); }
}

function openTask(taskId) { return taskDialog.open(taskId); }

document.querySelectorAll('.nav-item[data-view]').forEach(item => item.addEventListener('click', () => showView(item.dataset.view)));
document.querySelectorAll('[data-go]').forEach(item => item.addEventListener('click', () => showView(item.dataset.go)));
document.querySelectorAll('.segment-button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.segment-button').forEach(item => item.classList.toggle('active', item === button));
  const single = button.dataset.mode === 'single';
  const count = document.getElementById('countInput');
  const concurrency = document.getElementById('concurrencyInput');
  count.disabled = single; count.value = single ? 1 : Math.max(2, Number(count.value));
  concurrency.value = single ? 1 : Math.min(Number(count.value), Math.max(1, Number(concurrency.value)));
}));
document.getElementById('countInput').disabled = true;
document.getElementById('countInput').addEventListener('input', event => {
  const concurrency = document.getElementById('concurrencyInput');
  concurrency.max = Math.min(8, Number(event.target.value) || 1);
  if (Number(concurrency.value) > Number(concurrency.max)) concurrency.value = concurrency.max;
});
document.getElementById('taskForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('startButton');
  button.disabled = true;
  try {
    const payload = {
      count: Number(document.getElementById('countInput').value),
      concurrency: Number(document.getElementById('concurrencyInput').value),
    };
    const data = await api('/api/tasks', { method: 'POST', body: JSON.stringify(payload) });
    toast(`任务 ${data.task.id} 已启动`);
    await refreshDashboard();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
});
document.getElementById('logoutButton').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' });
  window.location.href = appUrl('/login');
});
document.getElementById('closeDialog').addEventListener('click', () => taskDialog.close());
document.getElementById('closeAccountDialog').addEventListener('click', () => document.getElementById('accountDialog').close());
document.getElementById('permSelectAll').addEventListener('click', () => {
  document.querySelectorAll('input[name="acc_permission"]').forEach(item => { item.checked = true; });
});
document.getElementById('permSelectNone').addEventListener('click', () => {
  document.querySelectorAll('input[name="acc_permission"]').forEach(item => { item.checked = false; });
});
document.getElementById('accountForm').addEventListener('submit', async event => {
  event.preventDefault();
  const email = document.getElementById('acc_email').value;
  const button = event.target.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try {
    const data = await api(`/api/accounts/${encodeURIComponent(email)}`, {
      method: 'PUT',
      body: JSON.stringify(collectAccountPayload()),
    });
    fillAccount(data.account);
    toast('账号已保存到后台');
    loadAccounts();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
});
document.getElementById('refreshAccountButton').addEventListener('click', async () => {
  const email = document.getElementById('acc_email').value;
  const button = document.getElementById('refreshAccountButton');
  button.disabled = true;
  try {
    const data = await api(`/api/accounts/${encodeURIComponent(email)}/refresh`, { method: 'POST', body: '{}' });
    fillAccount(data.account);
    toast('已同步到期时间和剩余流量');
    loadAccounts();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});
async function deleteAccounts(emails) {
  const list = emails.filter(Boolean);
  if (!list.length) return;
  const preview = list.length === 1 ? list[0] : `${list.length} 个账号`;
  if (!window.confirm(`确定删除 ${preview}？删除后可再导入恢复。`)) return;
  try {
    const data = await api('/api/accounts/delete', {
      method: 'POST',
      body: JSON.stringify({ emails: list }),
    });
    const dialog = document.getElementById('accountDialog');
    if (dialog.open) dialog.close();
    toast(`已删除 ${data.deleted.length} 个账号`);
    await loadAccounts();
  } catch (error) {
    toast(error.message, true);
  }
}

async function syncUsage(emails) {
  const button = document.getElementById('syncUsageButton');
  button.disabled = true;
  try {
    const payload = emails && emails.length ? { emails } : {};
    const data = await api('/api/accounts/sync-usage', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const failText = data.failed.length ? `，失败 ${data.failed.length}` : '';
    toast(`已同步 ${data.synced.length} 个账号的到期时间和剩余流量${failText}`, Boolean(data.failed.length));
    await loadAccounts();
    const openEmail = document.getElementById('acc_email').value;
    if (openEmail && document.getElementById('accountDialog').open) {
      const { account } = await api(`/api/accounts/${encodeURIComponent(openEmail)}`);
      fillAccount(account);
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

document.getElementById('syncApiKeyButton').addEventListener('click', async () => {
  const email = document.getElementById('acc_email').value;
  const button = document.getElementById('syncApiKeyButton');
  button.disabled = true;
  try {
    const data = await api(`/api/accounts/${encodeURIComponent(email)}/api-key`, {
      method: 'POST',
      body: JSON.stringify(collectAccountPayload()),
    });
    fillAccount(data.account);
    toast('API 密钥已同步到 ProxyScrape');
    loadAccounts();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});
document.getElementById('deleteAccountButton').addEventListener('click', () => {
  deleteAccounts([document.getElementById('acc_email').value]);
});
document.getElementById('deleteSelectedAccounts').addEventListener('click', () => {
  deleteAccounts(selectedAccountEmails());
});
document.getElementById('accountSelectAll').addEventListener('change', event => {
  document.querySelectorAll('#accountsBody .account-select').forEach(item => {
    item.checked = event.target.checked;
  });
  updateDeleteSelectedState();
});
document.getElementById('syncUsageButton').addEventListener('click', () => {
  syncUsage(selectedAccountEmails());
});
document.getElementById('importAccountsButton').addEventListener('click', () => {
  document.getElementById('importText').value = '';
  document.getElementById('importFile').value = '';
  document.getElementById('importDialog').showModal();
});
document.getElementById('closeImportDialog').addEventListener('click', () => document.getElementById('importDialog').close());
document.getElementById('importFile').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  document.getElementById('importText').value = await file.text();
});
document.getElementById('importForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try {
    const data = await api('/api/accounts/import', {
      method: 'POST',
      body: JSON.stringify({ text: document.getElementById('importText').value }),
    });
    document.getElementById('importDialog').close();
    const restored = data.restored ? data.restored.length : 0;
    toast(`已导入 ${data.imported} 个账号${restored ? `，恢复 ${restored} 个` : ''}`);
    await loadAccounts();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
});
document.getElementById('menuButton').addEventListener('click', () => {
  document.getElementById('sidebar').classList.add('open');
  document.getElementById('sidebarScrim').classList.add('show');
});
document.getElementById('sidebarScrim').addEventListener('click', () => {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarScrim').classList.remove('show');
});

const settingFields = {
  mail: ['mail_provider', 'mail_type', 'mail_suffix', 'mail_domain', 'mail_api_base', 'mail_api_key', 'yyds_api_key', 'yyds_domain'],
  captcha: ['captcha_provider', 'captcha_timeout', 'captcha_poll_interval', 'turnstile_extension_path', 'captcha_api_base', 'captcha_api_key'],
  proxy: ['http_proxy', 'https_proxy', 'no_proxy'],
};

const captchaApiDefaults = {
  '2captcha': 'https://api.2captcha.com',
  yescaptcha: 'https://api.yescaptcha.com',
  capmonster: 'https://api.capmonster.cloud',
  browser: '',
};

function applyCaptchaProviderDefault(force = false) {
  const provider = document.getElementById('captcha_provider');
  const apiBase = document.getElementById('captcha_api_base');
  if (!provider || !apiBase) return;
  const next = captchaApiDefaults[provider.value] ?? '';
  if (force || !apiBase.value.trim() || Object.values(captchaApiDefaults).includes(apiBase.value.trim())) {
    apiBase.value = next;
  }
  apiBase.placeholder = next || 'browser 模式无需 API 地址';
}

function fillSettings(settings) {
  state.settings = settings;
  Object.entries(settings).forEach(([key, value]) => {
    const element = document.getElementById(key);
    if (!element) return;
    if (element.type === 'checkbox') element.checked = Boolean(value);
    else element.value = value ?? '';
  });
  updateProxyMode();
  applyCaptchaProviderDefault(false);
}

function updateProxyMode() {
  const usePool = document.getElementById('proxy_use_pool').checked;
  ['proxy_enabled', 'http_proxy', 'https_proxy', 'no_proxy'].forEach(id => {
    document.getElementById(id).disabled = usePool;
  });
}

async function loadSettings() {
  try {
    const data = await api('/api/settings');
    fillSettings(data.settings);
  } catch (error) {
    toast(error.message, true);
  }
}

function collectSettings(keys) {
  const payload = {};
  keys.forEach(key => {
    const element = document.getElementById(key);
    if (!element) return;
    if (element.type === 'checkbox') payload[key] = element.checked;
    else if (element.type === 'number') payload[key] = Number(element.value);
    else payload[key] = element.value;
  });
  return payload;
}

async function saveSettings(event, keys, successText) {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try {
    const data = await api('/api/settings', { method: 'PUT', body: JSON.stringify(collectSettings(keys)) });
    fillSettings(data.settings);
    toast(successText);
    refreshDashboard();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

document.getElementById('mailSettingsForm').addEventListener('submit', event => saveSettings(event, settingFields.mail, '邮箱设置已同步'));
document.getElementById('captchaSettingsForm').addEventListener('submit', event => saveSettings(event, settingFields.captcha, '打码设置已同步'));
document.getElementById('captcha_provider')?.addEventListener('change', () => {
  applyCaptchaProviderDefault(true);
});
document.getElementById('proxySettingsForm').addEventListener('submit', event => {
  saveSettings(event, [...settingFields.proxy, 'proxy_enabled', 'proxy_use_pool'], '代理设置已同步');
});
document.getElementById('proxy_use_pool').addEventListener('change', updateProxyMode);
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', () => copyField(button.dataset.copy).catch(error => toast(error.message, true)));
});
document.getElementById('poolGatewayFormat')?.addEventListener('change', renderGatewayFormat);
document.getElementById('downloadGatewayButton')?.addEventListener('click', () => {
  const url = document.getElementById('poolGatewayUrl')?.value;
  if (url) window.location.href = url;
});
document.getElementById('proxyFileFormat')?.addEventListener('change', updateProxyFileLinks);

refreshDashboard();
poolAutomation.refresh();
loadExports();
loadSettings();
setInterval(() => {
  refreshAccountExpiries();
  refreshDashboard();
  if (state.currentView === 'tasks') loadTasks();
  taskDialog.refresh();
  quality.refresh();
  poolAutomation.refresh();
}, 4000);
