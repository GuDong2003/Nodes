(function attachTaskDialog(global) {
  function create({ document, state, api, toast, escapeHtml, statusLabel }) {
    let taskId = null;
    let refreshing = false;
    const dialog = document.getElementById('taskDialog');

    function clearTask() {
      taskId = null;
      state.openTaskId = null;
    }

    function render(task) {
      document.getElementById('dialogTaskId').textContent = task.id;
      document.getElementById('dialogStats').innerHTML = `
        <div><span>进度</span><strong>${task.completed}/${task.requested}</strong></div>
        <div><span>成功</span><strong>${task.successes}</strong></div>
        <div><span>代理</span><strong>${task.proxy_count || 0}</strong></div>
        <div><span>状态</span><strong>${escapeHtml(statusLabel(task.status))}</strong></div>`;
      document.getElementById('dialogLogs').innerHTML = (task.logs || [])
        .map(log => `<div class="log-entry">${escapeHtml(log)}</div>`).join('');
    }

    async function fetchAndRender() {
      if (!taskId || !dialog.open || refreshing) return;
      refreshing = true;
      const requestedId = taskId;
      try {
        const { task } = await api(`/api/tasks/${encodeURIComponent(requestedId)}`);
        if (taskId === requestedId && dialog.open) render(task);
      } catch (error) {
        // The initial open reports errors; background refresh waits for the next tick.
      } finally {
        refreshing = false;
      }
    }

    async function open(requestedId) {
      taskId = requestedId;
      state.openTaskId = requestedId;
      try {
        const { task } = await api(`/api/tasks/${encodeURIComponent(requestedId)}`);
        if (taskId !== requestedId) return;
        render(task);
        dialog.showModal();
      } catch (error) {
        taskId = null;
        state.openTaskId = null;
        toast(error.message, true);
      }
    }

    function close() {
      clearTask();
      if (dialog.open) dialog.close();
    }

    dialog.addEventListener?.('close', clearTask);

    return { open, close, refresh: fetchAndRender };
  }

  global.NodesTaskDialog = { create };
}(window));
