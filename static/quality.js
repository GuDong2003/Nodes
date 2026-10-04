(function attachQuality(global) {
  const fields = {
    qualityEnable: ['proxy_quality_enabled', false, 'boolean'],
    qualityLatency: ['proxy_max_latency_ms', 3000, 'number'],
    qualityCountries: ['proxy_exclude_countries', '', 'string'],
    qualityTargetEnable: ['proxy_arp_check_enabled', false, 'boolean'],
    qualityTargetUrl: ['proxy_arp_probe_url', 'https://cp.cloudflare.com/generate_204', 'string'],
    qualityWorkers: ['proxy_quality_workers', 8, 'number'],
    qualityCache: ['proxy_quality_cache_ttl_sec', 600, 'number'],
    qualityTimeout: ['proxy_quality_timeout_sec', 12, 'number'],
  };
  const reasons = { disabled: '规则未启用', unprobed: '尚未检查', latency: '延迟超限',
    country: '国家被排除', arp: '目标检查未通过', probe: '探测失败' };

  function create({ document, state, api, toast, escapeHtml, formatDate }) {
    const el = id => document.getElementById(id);
    let profiles = null;
    let profileRequest = null;
    let inventoryRequest = null;
    let inventory = null;
    let editRevision = 0;
    let saving = false;
    let activating = false;
    let checking = false;
    let dryRunning = false;
    const unixDate = value => value ? formatDate(Number(value) * 1000) : '—';
    const ruleLabel = (id, version) => `${id || '—'} · v${version ?? 0}`;

    function updateFormState() {
      const selected = el('qualityProfileSelect').value;
      const saved = profiles?.profiles.some(profile => profile.id === selected);
      el('qualitySave').disabled = saving || activating || !profiles;
      el('qualityActivate').disabled = activating || saving || !saved;
      el('qualityDryButton').disabled = dryRunning || saving || !saved;
      el('qualityProfileSelect').disabled = !profiles;
      el('qualityProfileName').disabled = !profiles;
      el('qualityEnable').disabled = !profiles;
      el('qualityProfileId').disabled = !profiles || selected !== '__new__';
      const enabled = el('qualityEnable').checked;
      Object.keys(fields).filter(id => id !== 'qualityEnable').forEach(id => {
        el(id).disabled = !profiles || !enabled || (id === 'qualityTargetUrl' && !el('qualityTargetEnable').checked);
      });
    }

    function fillProfile(profile) {
      el('qualityProfileId').value = profile?.id || '';
      el('qualityProfileName').value = profile?.name || '';
      Object.entries(fields).forEach(([id, [key, fallback, type]]) => {
        if (type === 'boolean') el(id).checked = profile?.[key] ?? fallback;
        else el(id).value = profile?.[key] ?? fallback;
      });
      updateFormState();
    }

    function renderProfiles(preferred) {
      const selected = preferred || el('qualityProfileSelect').value || profiles.active_id;
      el('qualityProfileSelect').innerHTML = profiles.profiles.map(profile =>
        `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)} · ${escapeHtml(ruleLabel(profile.id, profile.version))}</option>`
      ).join('') + '<option value="__new__">新建规则…</option>';
      el('qualityProfileSelect').value = selected;
      el('qualityActiveRule').textContent = `当前启用：${ruleLabel(profiles.active_id, profiles.profiles.find(p => p.id === profiles.active_id)?.version)}；合格订阅：${profiles.export_id}`;
      updateFormState();
    }

    function loadProfiles() {
      if (profileRequest) return profileRequest;
      profileRequest = (async () => {
        try {
          profiles = await api('/api/quality/profiles');
          renderProfiles(profiles.active_id);
          fillProfile(profiles.profiles.find(p => p.id === profiles.active_id));
        } catch (error) { toast(error.message, true); }
        finally { profileRequest = null; updateFormState(); }
      })();
      return profileRequest;
    }

    function collectProfile() {
      const payload = { name: el('qualityProfileName').value.trim() };
      Object.entries(fields).forEach(([id, [key, , type]]) => {
        payload[key] = type === 'boolean' ? el(id).checked : type === 'number' ? Number(el(id).value) : el(id).value.trim();
      });
      return payload;
    }

    async function saveProfile(event) {
      event.preventDefault();
      if (saving || activating || !profiles) return;
      const id = el('qualityProfileId').value.trim();
      const revision = editRevision;
      saving = true; updateFormState();
      try {
        const data = await api(`/api/quality/profiles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(collectProfile()) });
        profiles = data;
        if (data.profile && !profiles.profiles.some(p => p.id === data.profile.id)) profiles.profiles.push(data.profile);
        if (data.profile) profiles.profiles = profiles.profiles.map(p => p.id === data.profile.id ? data.profile : p);
        renderProfiles(editRevision === revision ? id : el('qualityProfileSelect').value);
        if (editRevision === revision && el('qualityProfileSelect').value === id) fillProfile(data.profile);
        toast('规则已保存；启用后用于库存与合格订阅');
      } catch (error) { toast(error.message, true); }
      finally { saving = false; updateFormState(); }
    }

    async function activateProfile() {
      if (el('qualityActivate').disabled) return;
      const id = el('qualityProfileSelect').value;
      activating = true; updateFormState();
      try {
        profiles = await api('/api/quality/profiles/activate', { method: 'POST', body: JSON.stringify({ id }) });
        renderProfiles();
        toast('已启用保存的规则，合格订阅同步切换');
      } catch (error) { toast(error.message, true); }
      finally { activating = false; updateFormState(); }
    }

    function updateCheckState() {
      el('qualityCheck').disabled = checking || !inventory?.inventory.enabled || Boolean(inventory?.check?.running);
    }

    function renderInventory(data) {
      inventory = data;
      const report = data.inventory;
      [['qualityScanned', 'scanned'], ['qualityAccepted', 'accepted'], ['qualityRejected', 'rejected'], ['qualityUntested', 'skipped_unprobed']].forEach(([id, key]) => { el(id).textContent = report[key] ?? 0; });
      el('qualityInventoryState').textContent = `${report.enabled ? '质量规则已启用' : '质量规则未启用：节点均为未测，订阅透传全部节点'} · ${ruleLabel(report.profile_id, report.version)}`;
      el('qualityQualifiedUrl').value = data.qualified_url || '';
      const job = data.check || {};
      el('qualityProgress').textContent = job.running
        ? `正在检查 ${job.completed}/${job.total} · ${ruleLabel(job.profile_id, job.version)}`
        : job.error ? `检查失败：${job.error}` : job.total ? `检查完成 ${job.completed}/${job.total} · ${ruleLabel(job.profile_id, job.version)}` : '尚未启动检查';
      el('qualityResults').innerHTML = (report.results || []).map(row => {
        const status = row.ok === true ? ['success', '通过'] : row.ok === false ? ['failed', '未通过'] : ['queued', '未测'];
        const target = row.arp_ok === true ? '通过' : row.arp_ok === false ? '未通过' : '—';
        return `<tr><td title="${escapeHtml(row.identity)}">${escapeHtml(row.identity)}</td><td><span class="status-chip ${status[0]}">${status[1]}</span></td><td>${escapeHtml(row.country || row.country_code || '—')}</td><td>${escapeHtml(row.latency_ms == null ? '—' : `${row.latency_ms} ms`)}</td><td>${escapeHtml(row.egress_ip || '—')}</td><td>${target}${row.arp_status == null ? '' : ` · ${escapeHtml(row.arp_status)}`}</td><td title="${escapeHtml(row.reason || '')}">${escapeHtml(reasons[row.reason] || row.reason || '—')}</td><td>${escapeHtml(unixDate(row.checked_at))}</td></tr>`;
      }).join('') || '<tr><td colspan="8" class="empty-cell">暂无节点</td></tr>';
      updateCheckState();
    }

    function renderHistory(history) {
      el('qualityHistory').innerHTML = [...history].reverse().map(row => `<tr><td>${escapeHtml(formatDate(row.recorded_at))}</td><td>${escapeHtml(ruleLabel(row.profile_id, row.version))}</td><td>${escapeHtml(row.scanned)}</td><td>${escapeHtml(row.accepted)}</td><td>${escapeHtml(row.rejected)}</td><td>${escapeHtml(row.skipped_unprobed)}</td></tr>`).join('') || '<tr><td colspan="6" class="empty-cell">暂无库存快照</td></tr>';
    }

    function renderAudit(entries) {
      const actions = { save_profile: '保存规则', activate_profile: '启用规则', check_complete: '检查完成', check_failed: '检查失败' };
      el('qualityAudit').innerHTML = entries.map(row => `<tr><td>${escapeHtml(formatDate(row.at))}</td><td>${escapeHtml(actions[row.action] || row.action)}</td><td>${escapeHtml(row.actor || '—')}</td><td title="${escapeHtml(JSON.stringify(row.detail))}">${escapeHtml(typeof row.detail === 'string' ? row.detail : JSON.stringify(row.detail))}</td></tr>`).join('') || '<tr><td colspan="4" class="empty-cell">暂无审计记录</td></tr>';
    }

    function refresh() {
      if (state.currentView !== 'inventory') return Promise.resolve();
      if (inventoryRequest) return inventoryRequest;
      inventoryRequest = (async () => {
        const results = await Promise.allSettled([api('/api/inventory'), api('/api/inventory/history'), api('/api/audit')]);
        if (results[0].status === 'fulfilled') renderInventory(results[0].value);
        if (results[1].status === 'fulfilled') renderHistory(results[1].value.history || []);
        if (results[2].status === 'fulfilled') renderAudit(results[2].value.entries || []);
        const failure = results.find(result => result.status === 'rejected');
        if (failure) toast(failure.reason.message, true);
      })().finally(() => { inventoryRequest = null; });
      return inventoryRequest;
    }

    async function check() {
      if (el('qualityCheck').disabled) return;
      checking = true; updateCheckState();
      try {
        const data = await api('/api/quality/check', { method: 'POST', body: '{}' });
        inventory.check = data.check; renderInventory(inventory);
        await refresh();
      } catch (error) { toast(error.message, true); }
      finally { checking = false; updateCheckState(); }
    }

    async function dryRun(event) {
      event.preventDefault();
      if (el('qualityDryButton').disabled) return;
      const rule = el('qualityProfileSelect').value;
      dryRunning = true; updateFormState();
      try {
        const data = await api('/api/quality/dry-run', { method: 'POST', body: JSON.stringify({ text: el('qualityDryText').value, rule }) });
        const report = data.report;
        el('qualityDryReport').textContent = `${ruleLabel(data.profile_id, data.version)}：共 ${report.scanned}，通过 ${report.accepted}，未通过 ${report.rejected}，未测 ${report.skipped_unprobed}${report.enabled ? '' : '（规则未启用）'}`;
      } catch (error) { toast(error.message, true); }
      finally { dryRunning = false; updateFormState(); }
    }

    async function snapshot() {
      const button = el('qualitySnapshot');
      if (button.disabled) return;
      button.disabled = true;
      try {
        await api('/api/inventory/snapshot', { method: 'POST', body: '{}' });
        toast('库存快照已保存'); await refresh();
      } catch (error) { toast(error.message, true); }
      finally { button.disabled = false; }
    }

    el('qualityProfileSelect').addEventListener('change', () => {
      editRevision += 1;
      fillProfile(profiles?.profiles.find(p => p.id === el('qualityProfileSelect').value));
    });
    el('qualityForm').addEventListener('input', () => { editRevision += 1; updateFormState(); });
    el('qualityForm').addEventListener('change', updateFormState);
    el('qualityForm').addEventListener('submit', saveProfile);
    el('qualityActivate').addEventListener('click', activateProfile);
    el('qualityCheck').addEventListener('click', check);
    el('qualityDryForm').addEventListener('submit', dryRun);
    el('qualitySnapshot').addEventListener('click', snapshot);
    updateFormState(); updateCheckState();
    return { refresh, async onShow(view) {
      if (view === 'rules' && !profiles) await loadProfiles();
      if (view === 'inventory') await refresh();
    } };
  }
  global.NodesQuality = { create };
}(window));
