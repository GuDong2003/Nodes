// The independently served Resin UI only needs a plain external navigation link.
(() => {
  function addNodesLink() {
    const nav = document.querySelector('.nav-list');
    if (!nav || nav.querySelector('#open-nodes-ops')) return;
    const link = document.createElement('a');
    link.id = 'open-nodes-ops';
    link.className = 'nav-item';
    link.href = 'https://ps.gudong226.com/nodes/';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = '打开 Nodes Ops ↗';
    nav.append(link);
  }

  // Resin mounts its sidebar after login; restore the link if React remounts it.
  new MutationObserver(addNodesLink).observe(document.body, { childList: true, subtree: true });
  addNodesLink();
})();
