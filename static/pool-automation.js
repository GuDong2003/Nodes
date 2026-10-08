(function attachPoolAutomation(global) {
  const fields = {
    poolTargetNodes: { key: 'target_nodes', value: 80, min: 0, max: 100000, integer: true, label: '节点阈值' },
    poolTargetBandwidth: { key: 'target_bandwidth_gb', value: 0, min: 0, max: 100000, integer: false, label: '流量阈值' },
    poolCheckInterval: { key: 'interval_minutes', value: 30, min: 1, max: 1440, integer: true, label: '检查间隔' },
    poolMaxRegistrations: { key: 'max_register_per_round', value: 5, min: 1, max: 5, integer: true, label: '每轮注册上限' },
  };
  const outcomes = { idle: '等待检查', disabled: '自动补号未启用', running: '正在检查',
    satisfied: '容量已满足阈值', registration_started: '已启动补号任务', busy: '已有任务运行',
    sync_failed: '用量同步失败，已跳过补号', unknown_capacity: '容量未知，已跳过补号', error: '检查失败' };

  function create({ document, state, api, toast, formatBytes, formatDate }) {
    const el = id => document.getElementById(id);
    let snapshot = null;
    let loading = null;
    let loaded = false;
    let dirty = false;
    let editRevision = 0;
    let generation = 0;
    let saving = false;
    let checking = false;

    function updateFormState() {
      el('poolAutomationEnabled').disabled = !loaded;
      Object.keys(fields).forEach(id => { el(id).disabled = !loaded; });
      el('poolAutomationSave').disabled = !loaded || saving;
      el('poolAutomationSave').textContent = saving ? '保存中…' : '保存补号设置';
      const running = checking || Boolean(snapshot?.status.running);
      el('ensureCapacityButton').disabled = !loaded || saving || running || dirty;
      el('ensureCapacityButton').textContent = running ? '检查中…' : '立即检查';
      el('poolAutomationEditHint').textContent = !loaded ? '正在读取已保存设置…' : dirty
        ? '有未保存的修改；保存后即可按新设置检查。'
        : '检查使用已保存的设置；关闭自动补号时，只同步用量和检查容量。';
    }

    function fillSettings(settings) {
      el('poolAutomationEnabled').checked = settings.enabled === true;
      Object.entries(fields).forEach(([id, field]) => { el(id).value = settings[field.key] ?? field.value; });
    }

    function render(data, fillForm) {
      if (!data.settings || !data.capacity || !data.status) throw Error('补号状态返回不完整');
      snapshot = data;
      loaded = true;
      if (fillForm) { fillSettings(data.settings); dirty = false; }
      const capacity = data.capacity;
      const status = data.status;
      el('poolAccounts').textContent = capacity.live_accounts ?? 0;
      el('poolSlots').textContent = `${capacity.live_slots ?? 0} 条`;
      el('poolBandwidth').textContent = formatBytes(capacity.bandwidth_remaining);
      el('poolNeeded').textContent = capacity.needed_accounts ?? 0;
      el('poolAuto').textContent = data.settings.enabled ? '开' : '关';
      const unknown = capacity.unknown_bandwidth_accounts || 0;
      el('poolCapacityHint').textContent = '剩余总流量仅汇总符合导出条件的账号（单账号最低剩余流量默认为 100 MiB），每个账号只计一次。'
        + (unknown ? ` ${unknown} 个账号流量未知，自动补号将跳过本轮。` : '');
      el('poolAutomationStatus').textContent = status.message || outcomes[status.outcome] || '等待检查';
      el('poolAutomationStatus').className = `pool-automation-message${['error', 'sync_failed', 'unknown_capacity'].includes(status.outcome) ? ' is-error' : ''}`;
      el('poolAutomationProgress').textContent = `已同步 ${status.synced ?? 0} 个账号 · 同步失败 ${status.failed ?? 0} · 本轮补号 ${status.registered_count ?? 0}`;
      el('poolAutomationLastCheck').textContent = formatDate(status.last_check_at);
      el('poolAutomationNextCheck').textContent = formatDate(status.next_check_at);
      el('poolAutomationWorker').textContent = status.worker_enabled === false
        ? '环境已停用后台定时检查；仍可使用「立即检查」。'
        : data.settings.enabled ? '后台定时检查已启用。' : '自动补号已关闭；手动检查只同步用量和检查容量。';
      updateFormState();
    }

    function showError(error) {
      el('poolAutomationStatus').textContent = error.message;
      el('poolAutomationStatus').className = 'pool-automation-message is-error';
    }

    function refresh() {
      if (state.currentView !== 'dashboard' || saving || checking) return Promise.resolve();
      if (loading) return loading;
      const requestedGeneration = generation;
      const revision = editRevision;
      loading = (async () => {
        try {
          const data = await api('/api/pool/automation');
          if (requestedGeneration === generation) render(data, !dirty && revision === editRevision);
        } catch (error) {
          if (requestedGeneration === generation) showError(error);
        } finally { loading = null; updateFormState(); }
      })();
      return loading;
    }

    function collectSettings() {
      const settings = { enabled: el('poolAutomationEnabled').checked };
      Object.entries(fields).forEach(([id, field]) => {
        const raw = String(el(id).value).trim();
        const value = Number(raw);
        if (!raw || !Number.isFinite(value) || value < field.min || value > field.max || (field.integer && !Number.isInteger(value))) {
          throw Error(`${field.label}须为 ${field.min}–${field.max} 之间的${field.integer ? '整数' : '数值'}`);
        }
        settings[field.key] = value;
      });
      if (settings.enabled && settings.target_nodes === 0 && settings.target_bandwidth_gb === 0) {
        throw Error('启用自动补号时，至少设置一个大于 0 的阈值');
      }
      return settings;
    }

    async function save(event) {
      event.preventDefault();
      if (!loaded || saving) return;
      let settings;
      try { settings = collectSettings(); }
      catch (error) { toast(error.message, true); return; }
      const revision = editRevision;
      const requestedGeneration = ++generation;
      saving = true; updateFormState();
      try {
        const data = await api('/api/pool/automation', { method: 'PUT', body: JSON.stringify(settings) });
        if (requestedGeneration === generation) render(data, revision === editRevision);
        toast('自动补号设置已保存');
      } catch (error) { showError(error); toast(error.message, true); }
      finally { saving = false; updateFormState(); }
    }

    async function check() {
      if (el('ensureCapacityButton').disabled) return;
      const requestedGeneration = ++generation;
      checking = true; updateFormState();
      try {
        const data = await api('/api/pool/automation/check', { method: 'POST', body: '{}' });
        if (requestedGeneration === generation) render(data, !dirty);
        toast('已开始同步用量和检查容量');
      } catch (error) {
        if (requestedGeneration === generation) showError(error);
        toast(error.message, true);
      } finally { checking = false; updateFormState(); }
    }

    function markDirty() { dirty = true; editRevision += 1; updateFormState(); }
    el('poolAutomationForm').addEventListener('input', markDirty);
    el('poolAutomationForm').addEventListener('change', markDirty);
    el('poolAutomationForm').addEventListener('submit', save);
    el('ensureCapacityButton').addEventListener('click', check);
    fillSettings({ enabled: false });
    updateFormState();
    return { refresh, onShow(view) { if (view === 'dashboard') return refresh(); } };
  }

  global.NodesPoolAutomation = { create };
}(window));
