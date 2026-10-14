// Shared SSE progress panel for settings sync jobs (Overseerr sync, Plex re-scan).
function byId(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element #${id}`);
  return element;
}
function progressNumbers(data) {
  const serverPercent = Number(data.overall_percent ?? data.progress_percent);
  const current = Number(data.overall_current ?? data.current ?? data.completed);
  const total = Number(data.overall_total ?? data.total);
  if (!Number.isFinite(total) || total <= 0) {
    return { percent: Number.isFinite(serverPercent) ? serverPercent : null, label: 'Working…' };
  }
  const safeCurrent = Number.isFinite(current) ? Math.max(0, Math.min(current, total)) : 0;
  const percent = Number.isFinite(serverPercent) ? serverPercent : Math.round((safeCurrent / total) * 100);
  return { percent, label: `${safeCurrent}/${total}` };
}
export function applyProgress(bar, label, data, options = {}) {
  const maximumPercent = options.final === true ? 100 : 99;
  const numbers = progressNumbers(data);
  const percent = Math.max(
    options.minimumPercent ?? 0,
    Math.min(numbers.percent ?? options.fallbackPercent ?? 5, maximumPercent),
  );
  bar.style.width = `${percent}%`;
  label.textContent = numbers.label;
}
export function renderActiveItems(listElement, wrapperElement, items) {
  listElement.innerHTML = '';
  const values = items.slice(0, 5);
  if (!values.length) {
    wrapperElement.classList.add('hidden');
    return;
  }
  for (const item of values) {
    const li = document.createElement('li');
    li.className = 'rounded-lg bg-surface-850 px-2.5 py-1 text-xs text-gray-300 truncate';
    li.textContent = item;
    listElement.appendChild(li);
  }
  if (items.length > values.length) {
    const li = document.createElement('li');
    li.className = 'px-2.5 py-0.5 text-[11px] text-gray-500';
    li.textContent = `+${items.length - values.length} more`;
    listElement.appendChild(li);
  }
  wrapperElement.classList.remove('hidden');
}
/** Open an SSE progress stream and render it into the panel described by `ids`. */
export function startProgressStream(options) {
  const { ids } = options;
  const bar = byId(ids.bar);
  const progressText = byId(ids.progressText);
  const status = byId(ids.status);
  const closeBtn = byId(ids.closeBtn);
  const activeWrap = byId(ids.activeWrap);
  const activeList = byId(ids.activeList);
  bar.style.width = '0%';
  bar.classList.remove('bg-red-500');
  bar.classList.add('bg-brand-500');
  if (ids.title && options.title) byId(ids.title).textContent = options.title;
  progressText.textContent = 'Preparing…';
  status.textContent = options.initialStatus;
  status.classList.remove('text-red-400', 'text-emerald-400');
  status.classList.add('text-gray-400');
  renderActiveItems(activeList, activeWrap, []);
  closeBtn.disabled = true;
  closeBtn.classList.add('hidden');
  byId(ids.panel).classList.remove('hidden');
  let source = new EventSource(options.url);
  const close = () => {
    source?.close();
    source = null;
  };
  const finish = (tone) => {
    status.classList.remove('text-gray-400');
    status.classList.add(tone);
    closeBtn.disabled = false;
    closeBtn.classList.remove('hidden');
    close();
  };
  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const phase = data.phase ?? '';
    if (phase === 'complete') {
      applyProgress(bar, progressText, data, { final: true, fallbackPercent: 100 });
      status.textContent = data.message ?? '';
      renderActiveItems(activeList, activeWrap, data.active || []);
      finish('text-emerald-400');
      return;
    }
    if (phase === 'error') {
      applyProgress(bar, progressText, data, { final: true, fallbackPercent: 100 });
      bar.classList.remove('bg-brand-500');
      bar.classList.add('bg-red-500');
      status.textContent = data.message ?? '';
      renderActiveItems(activeList, activeWrap, data.active || []);
      finish('text-red-400');
      return;
    }
    const spec = options.phases[phase];
    if (!spec) return;
    applyProgress(bar, progressText, data, spec);
    const statusText = spec.status?.(data);
    if (statusText !== undefined) status.textContent = statusText;
    if (spec.active) renderActiveItems(activeList, activeWrap, spec.active(data));
  };
  source.onerror = () => {
    applyProgress(bar, progressText, {}, { final: true, fallbackPercent: 100 });
    bar.classList.remove('bg-brand-500');
    bar.classList.add('bg-red-500');
    status.textContent = options.errorMessage;
    finish('text-red-400');
  };
  return { close };
}
