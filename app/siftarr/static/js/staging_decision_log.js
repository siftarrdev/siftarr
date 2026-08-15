(function () {
  const form = document.getElementById('decision-log-filters');
  const results = document.getElementById('decision-log-results');
  const status = document.getElementById('decision-log-status');
  const prev = document.getElementById('decision-log-prev');
  const next = document.getElementById('decision-log-next');
  const reset = document.getElementById('decision-log-reset');
  let page = Number(new URLSearchParams(window.location.search).get('page') || '1');

  function esc(value) {
    return String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  }
  function selected(entry) {
    return entry.selected_release || entry.new_torrent || {};
  }
  function localTime(value) {
    if (!value) return 'Unknown time';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
  }
  function params() {
    const p = new URLSearchParams(new FormData(form));
    [...p.keys()].forEach((k) => {
      if (!p.get(k)) p.delete(k);
    });
    p.set('page', String(page));
    p.set('page_size', '25');
    return p;
  }
  function syncUrl(p) {
    window.history.replaceState(null, '', `${window.location.pathname}?${p.toString()}`);
  }
  function fillForm() {
    const p = new URLSearchParams(window.location.search);
    for (const el of form.elements) if (el.name && p.has(el.name)) el.value = p.get(el.name);
  }
  function render(data) {
    status.textContent = data.total
      ? `Page ${data.page} · ${data.total} entries`
      : 'No decision-log entries match these filters.';
    prev.disabled = data.page <= 1;
    next.disabled = !data.has_next;
    if (!data.items.length) {
      results.innerHTML =
        '<div class="rounded-xl border border-gray-800 p-4 text-sm text-gray-400">No entries found.</div>';
      return;
    }
    results.innerHTML = data.items
      .map((entry) => {
        const req = entry.request || {};
        const rel = selected(entry);
        return `<article class="rounded-xl border border-gray-800 bg-surface-900 p-4">
        <div class="grid gap-2 md:grid-cols-[170px_1fr]">
          <div class="text-xs text-gray-500" title="${esc(entry.logged_at)}">${esc(localTime(entry.logged_at))}</div>
          <div><h2 class="font-semibold text-white">${esc(req.title || 'Unknown title')}</h2>
          <p class="mt-1 text-sm text-gray-400">${esc(req.media_type || 'unknown')} · ${esc(entry.event_type || '')} · ${esc(entry.outcome || '')} · ${esc((entry.selection || {}).selection_source || (entry.selection || {}).source || '')}</p>
          <p class="mt-1 text-sm text-gray-300">Selected: ${esc(rel.title || 'None')} ${rel.indexer ? `(${esc(rel.indexer)})` : ''}</p></div>
        </div>
        <details class="mt-3"><summary class="cursor-pointer text-sm text-brand-300">Raw JSON</summary><pre class="mt-2 max-h-96 overflow-auto rounded-lg bg-black/30 p-3 text-xs text-gray-300">${esc(JSON.stringify(entry, null, 2))}</pre></details>
      </article>`;
      })
      .join('');
  }
  async function load() {
    const p = params();
    syncUrl(p);
    status.textContent = 'Loading…';
    results.innerHTML = '';
    try {
      const r = await fetch(`/staged/decision-log?${p.toString()}`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      render(await r.json());
    } catch (e) {
      status.textContent = `Error loading decision log: ${e.message}`;
    }
  }
  fillForm();
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    page = 1;
    load();
  });
  reset.addEventListener('click', () => {
    form.reset();
    page = 1;
    load();
  });
  prev.addEventListener('click', () => {
    if (page > 1) {
      page -= 1;
      load();
    }
  });
  next.addEventListener('click', () => {
    page += 1;
    load();
  });
  load();
})();
