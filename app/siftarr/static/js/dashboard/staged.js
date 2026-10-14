import {
  bindCheckboxRangeSelection,
  escapeHtml,
  fetchPageDoc,
  postWithConfirm,
  promptEachWarning,
} from '/static/js/dashboard/core.js';
const ACTIVE_DOWNLOADS_POLL_INTERVAL_MS = 1000;
const COMPLETED_TORRENTS_POLL_INTERVAL_MS = 1e4;
let stagedTabRefreshInFlight = false;
let downloadingTabRefreshInFlight = false;
let _stagedStatusPollInterval = null;
let _downloadStatusPatchInFlight = false;
let _stagedStatusPollWanted = false;
let _torrentStatusView = 'downloading';
let _torrentStatusSort = 'added-desc';
function _currentPollIntervalMs() {
  return _torrentStatusView === 'completed' ? COMPLETED_TORRENTS_POLL_INTERVAL_MS : ACTIVE_DOWNLOADS_POLL_INTERVAL_MS;
}
export function _startStagedStatusPoll() {
  _stagedStatusPollWanted = true;
  _stopPollTimer();
  _patchStagedDownloadStatus();
  if (document.visibilityState === 'hidden') return;
  _stagedStatusPollInterval = setInterval(_patchStagedDownloadStatus, _currentPollIntervalMs());
}
export function showQbitView(view) {
  const downloading = document.getElementById('qbit-download-list');
  const completed = document.getElementById('qbit-completed-list');
  if (!downloading || !completed) return;
  const showCompleted = view === 'completed';
  _torrentStatusView = showCompleted ? 'completed' : 'downloading';
  downloading.classList.toggle('hidden', showCompleted);
  completed.classList.toggle('hidden', !showCompleted);
  const downloadingButton = document.getElementById('qbit-subtab-downloading');
  const completedButton = document.getElementById('qbit-subtab-completed');
  const downloadSummary = document.getElementById('qbit-download-summary');
  if (downloadSummary) downloadSummary.classList.toggle('hidden', showCompleted);
  if (downloadingButton) {
    downloadingButton.className = showCompleted ? 'btn-ghost btn-sm tap' : 'btn-primary btn-sm tap-primary';
  }
  if (completedButton) {
    completedButton.className = showCompleted ? 'btn-primary btn-sm tap-primary' : 'btn-ghost btn-sm tap';
  }
  syncQbitSortControl();
  if (_stagedStatusPollWanted) _startStagedStatusPoll();
}
function syncQbitSortControl() {
  const control = document.getElementById('qbit-torrent-sort');
  if (control) control.value = _torrentStatusSort;
}
export function setQbitTorrentSort(sort) {
  if (!['added-desc', 'eta-asc', 'added-asc'].includes(sort)) return;
  _torrentStatusSort = sort;
  syncQbitSortControl();
  _patchStagedDownloadStatus();
}
function _stopPollTimer() {
  if (_stagedStatusPollInterval !== null) {
    clearInterval(_stagedStatusPollInterval);
    _stagedStatusPollInterval = null;
  }
}
export function _stopStagedStatusPoll() {
  _stagedStatusPollWanted = false;
  _stopPollTimer();
}
document.addEventListener('visibilitychange', () => {
  if (!_stagedStatusPollWanted) return;
  if (document.visibilityState === 'hidden') {
    _stopPollTimer();
  } else if (_stagedStatusPollInterval === null) {
    _startStagedStatusPoll();
  }
});
async function _patchStagedDownloadStatus() {
  const downloadingContent = document.getElementById('content-downloading');
  if (!downloadingContent || downloadingContent.classList.contains('hidden')) return;
  if (_downloadStatusPatchInFlight) return;
  const completedView = _torrentStatusView === 'completed';
  const panel = document.getElementById(completedView ? 'qbit-completed-list' : 'qbit-download-list');
  if (!panel || panel.classList.contains('hidden')) return;
  _downloadStatusPatchInFlight = true;
  try {
    const response = await fetch(completedView ? '/api/torrents/completed' : '/api/downloads');
    if (!response.ok) return;
    const data = await response.json();
    if (data.qbit_unavailable) {
      const summary = document.getElementById('qbit-download-summary');
      if (summary) summary.textContent = 'qBittorrent unavailable';
      return;
    }
    const groups = data.groups || [];
    if (completedView) renderQbitCompleted(groups);
    else renderQbitDownloads(groups);
  } catch (_err) {
  } finally {
    _downloadStatusPatchInFlight = false;
  }
}
function qbitNumber(value, divisor = 1, digits = 1) {
  return Number.isFinite(Number(value)) ? `${(Number(value) / divisor).toFixed(digits)}` : '—';
}
function qbitManagedActions(managed, card = false) {
  if (!managed) return '<span class="text-xs text-gray-500">Not managed by Siftarr</span>';
  const requestId = Number(managed.request_id);
  const torrentId = Number(managed.id);
  if (!Number.isInteger(requestId) || !Number.isInteger(torrentId)) return '';
  const classes = card ? 'btn-ghost btn-sm' : 'text-brand-400 hover:text-brand-300 font-medium';
  const deleteClasses = card ? 'btn-danger btn-sm' : 'text-red-400 hover:text-red-300 font-medium';
  return `<button type="button" onclick="openRequestDetails(${requestId})" class="${classes}">Details</button><button type="button" onclick="checkNow(${torrentId})" class="${classes}">Check Now</button><button type="button" onclick="if (confirm('Delete this torrent and downloaded data from qBittorrent, then return the request to pending?')) postStagedAction('/staged/${torrentId}/delete-download', '/?tab=downloading')" class="${deleteClasses}">Delete</button>`;
}
function qbitBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  if (Math.abs(number) >= 1024 * 1024 * 1024) return `${(number / 1024 / 1024 / 1024).toFixed(2)} GB`;
  return `${(number / 1024 / 1024).toFixed(1)} MB`;
}
function qbitSpeed(value) {
  return `${qbitNumber(value, 1024 * 1024)} MB/s`;
}
function qbitAggregateDownloadTotals(groups) {
  return (groups || []).reduce(
    (totals, group) => {
      const groupTotals = group.totals || {};
      const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
      totals.count += number(group.count ?? (group.torrents || []).length);
      totals.dlspeed += number(groupTotals.dlspeed);
      totals.upspeed += number(groupTotals.upspeed);
      totals.downloaded += number(groupTotals.downloaded);
      totals.size += number(groupTotals.size);
      return totals;
    },
    { count: 0, dlspeed: 0, upspeed: 0, downloaded: 0, size: 0 },
  );
}
export function renderQbitDownloadSummary(groups) {
  const summary = document.getElementById('qbit-download-summary');
  if (!summary) return;
  const totals = qbitAggregateDownloadTotals(groups);
  const torrentLabel = totals.count === 1 ? 'torrent' : 'torrents';
  summary.innerHTML = `<span><span class="text-gray-500">Active</span> ${totals.count} ${torrentLabel}</span><span><span class="text-gray-500">Total download</span> ↓ ${qbitSpeed(totals.dlspeed)}</span><span><span class="text-gray-500">Total upload</span> ↑ ${qbitSpeed(totals.upspeed)}</span><span><span class="text-gray-500">Downloaded</span> ${qbitBytes(totals.downloaded)} / ${qbitBytes(totals.size)}</span>`;
}
function qbitGroupKey(group) {
  return group.request_id === null || group.request_id === undefined ? 'unmanaged' : String(group.request_id);
}
function captureQbitGroupState(prefix) {
  const state = {};
  const record = (key, open) => {
    if (key === undefined) return;
    state[key] = !!state[key] || !!open;
  };
  document.querySelectorAll(`tr[data-download-group][data-group-prefix="${prefix}"]`).forEach((el) => {
    record(el.dataset.downloadGroup, el.getAttribute('aria-expanded') === 'true');
  });
  document.querySelectorAll(`details[data-download-group][data-group-prefix="${prefix}"]`).forEach((el) => {
    record(el.dataset.downloadGroup, el.open);
  });
  return state;
}
function restoreQbitGroupState(prefix, state) {
  if (!state) return;
  document.querySelectorAll(`details[data-download-group][data-group-prefix="${prefix}"]`).forEach((el) => {
    const key = el.dataset.downloadGroup;
    if (Object.prototype.hasOwnProperty.call(state, key)) el.open = !!state[key];
  });
  document.querySelectorAll(`tr[data-download-group][data-group-prefix="${prefix}"]`).forEach((el) => {
    const key = el.dataset.downloadGroup;
    if (!Object.prototype.hasOwnProperty.call(state, key)) return;
    setQbitGroupOpen(prefix, key, !!state[key]);
  });
}
function setQbitGroupOpen(prefix, key, open) {
  const header = document.querySelector(`tr[data-download-group="${key}"][data-group-prefix="${prefix}"]`);
  if (header) {
    header.setAttribute('aria-expanded', open ? 'true' : 'false');
    const chevron = header.querySelector('[data-group-chevron]');
    if (chevron) chevron.textContent = open ? '▾' : '▸';
  }
  document
    .querySelectorAll(`tr[data-download-group-child="${key}"][data-group-prefix="${prefix}"]`)
    .forEach((row) => row.classList.toggle('hidden', !open));
  const card = document.querySelector(`details[data-download-group="${key}"][data-group-prefix="${prefix}"]`);
  if (card && card.open !== open) card.open = open;
}
export function syncQbitGroupFromCard(prefix, key, open) {
  setQbitGroupOpen(prefix, key, !!open);
}
export function toggleQbitGroup(prefix, key) {
  const header = document.querySelector(`tr[data-download-group="${key}"][data-group-prefix="${prefix}"]`);
  if (!header) return;
  setQbitGroupOpen(prefix, key, header.getAttribute('aria-expanded') !== 'true');
}
const QBIT_MATCH_LABELS = { staged: 'Staged match', title: 'Title match' };
function qbitMatchBadge(match) {
  const label = QBIT_MATCH_LABELS[match];
  return label ? `<span class="badge badge-gray">${label}</span>` : '';
}
function qbitGroupTotalsText(totals, showSpeeds) {
  const parts = [];
  if (showSpeeds) {
    parts.push(`↓ ${qbitSpeed(totals.dlspeed)}`);
    parts.push(`↑ ${qbitSpeed(totals.upspeed)}`);
  }
  parts.push(`Size ${qbitBytes(totals.size)}`);
  parts.push(`Down ${qbitBytes(totals.downloaded)}`);
  parts.push(`Up ${qbitBytes(totals.uploaded)}`);
  return parts.join(' · ');
}
function qbitTorrentSortValue(torrent) {
  if (_torrentStatusSort === 'eta-asc') {
    const rawEta = torrent.eta;
    if (rawEta === null || rawEta === undefined || rawEta === '') return 1 / 0;
    const eta = Number(rawEta);
    return Number.isFinite(eta) && eta >= 0 && eta < 8640000 ? eta : 1 / 0;
  }
  const rawAddedOn = torrent.added_on;
  if (rawAddedOn === null || rawAddedOn === undefined || rawAddedOn === '') return 1 / 0;
  const addedOn = Number(rawAddedOn);
  if (!Number.isFinite(addedOn)) return 1 / 0;
  return _torrentStatusSort === 'added-desc' ? -addedOn : addedOn;
}
function sortQbitGroups(groups) {
  const tieBreak = (left, right) =>
    String(left.name || left.hash || '').localeCompare(String(right.name || right.hash || ''));
  const sorted = (groups || []).map((group) => ({
    ...group,
    torrents: [...(group.torrents || [])].sort((left, right) => {
      const difference = qbitTorrentSortValue(left) - qbitTorrentSortValue(right);
      return difference || tieBreak(left, right);
    }),
  }));
  return sorted.sort((left, right) => {
    const difference = qbitTorrentSortValue(left.torrents[0] || {}) - qbitTorrentSortValue(right.torrents[0] || {});
    return difference || String(left.title || '').localeCompare(String(right.title || ''));
  });
}
function renderQbitGroups(groups, options) {
  const { prefix, colspan, showSpeeds, rowHtml, cardHtml, emptyText, cards, body } = options;
  if (!cards || !body) return;
  const escape = escapeHtml;
  const state = captureQbitGroupState(prefix);
  groups = sortQbitGroups(groups);
  if (!groups.length) {
    cards.innerHTML = `<p class="text-sm text-gray-500">${escape(emptyText)}</p>`;
    body.innerHTML = `<tr><td colspan="${colspan}" class="px-5 py-6 text-sm text-gray-500">${escape(emptyText)}</td></tr>`;
    return;
  }
  const tableChunks = [];
  const cardChunks = [];
  groups.forEach((group) => {
    const key = qbitGroupKey(group);
    const torrents = group.torrents || [];
    if (group.count === 1 && torrents.length === 1) {
      tableChunks.push(rowHtml(torrents[0], ''));
      cardChunks.push(cardHtml(torrents[0]));
      return;
    }
    const title = escape(group.title || 'Unknown');
    const totals = group.totals || {};
    const summary = escape(qbitGroupTotalsText(totals, showSpeeds));
    const count = Number(group.count) || torrents.length;
    tableChunks.push(
      `<tr class="bg-surface-850/70 cursor-pointer" data-download-group="${escape(key)}" data-group-prefix="${prefix}" aria-expanded="false" onclick="toggleQbitGroup('${prefix}', '${escape(key)}')"><td colspan="${colspan}" class="px-5 py-3 text-sm"><div class="flex flex-wrap items-center gap-x-3 gap-y-1"><span class="text-gray-500" data-group-chevron>▸</span><span class="overflow-wrap-anywhere mr-2 font-semibold text-white">${title}</span><span class="badge badge-gray">${count}</span>${qbitMatchBadge(group.match)}<span class="ml-auto text-xs text-gray-400 tabular-nums">${summary}</span></div></td></tr>`,
    );
    torrents.forEach((torrent) => {
      tableChunks.push(
        rowHtml(torrent, ` data-download-group-child="${escape(key)}" data-group-prefix="${prefix}"`, true),
      );
    });
    cardChunks.push(
      `<details data-download-group="${escape(key)}" data-group-prefix="${prefix}" ontoggle="syncQbitGroupFromCard('${prefix}', '${escape(key)}', this.open)" class="rounded-xl border border-gray-700/60 bg-surface-900/60"><summary class="tap cursor-pointer px-3 py-2"><div class="overflow-wrap-anywhere text-sm font-semibold text-white">${title}</div><div class="mt-1 text-xs text-gray-400 tabular-nums">${count} torrents · ${summary}</div></summary><div class="space-y-3 p-3">${torrents.map(cardHtml).join('')}</div></details>`,
    );
  });
  body.innerHTML = tableChunks.join('');
  cards.innerHTML = cardChunks.join('');
  restoreQbitGroupState(prefix, state);
}
function renderQbitDownloads(groups) {
  const cards = document.getElementById('downloading-torrent-cards');
  const body = document.getElementById('downloading-torrents-body');
  if (!cards || !body) return;
  const escape = escapeHtml;
  const view = (torrent) => {
    const name = escape(torrent.name || torrent.hash || 'Unnamed torrent');
    const hash = escape(torrent.hash || '-');
    const state = escape(torrent.state || '-');
    const progress = qbitNumber(
      torrent.progress === null || torrent.progress === undefined ? null : Number(torrent.progress) * 100,
    );
    const eta = formatEta(torrent.eta);
    const speed = `${qbitNumber(torrent.dlspeed, 1024 * 1024)} MB/s`;
    const size = `${qbitNumber(torrent.size, 1024 * 1024 * 1024, 2)} GB`;
    const managedData = torrent.managed ? ` data-torrent-id="${Number(torrent.managed.id)}"` : '';
    return { name, hash, state, progress, eta, speed, size, managedData };
  };
  const rowHtml = (torrent, groupAttrs, child = false) => {
    const { name, state, progress, eta, speed, size, managedData } = view(torrent);
    const rowClass = child ? 'hover:bg-surface-850/80 hidden' : 'hover:bg-surface-850/80';
    return `<tr class="${rowClass}"${groupAttrs}${managedData}><td class="px-5 py-3.5 text-sm font-medium text-white max-w-sm"><div class="truncate" title="${name}">${name}</div></td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums" data-download-progress>${progress}%</td><td class="px-5 py-3.5 text-sm text-gray-400">${state}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums" data-download-eta>${eta}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${speed}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${size}</td><td class="px-5 py-3.5 text-sm"><div class="flex items-center gap-3">${qbitManagedActions(torrent.managed)}</div></td></tr>`;
  };
  const cardHtml = (torrent) => {
    const { name, state, progress, eta, size, managedData } = view(torrent);
    return `<div class="rounded-xl border border-gray-700/60 bg-surface-850 p-3"${managedData}><div class="overflow-wrap-anywhere text-sm font-semibold text-white">${name}</div><div class="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400"><div><div class="text-gray-500">Progress</div><div>${progress}%</div></div><div><div class="text-gray-500">State</div><div>${state}</div></div><div><div class="text-gray-500">ETA</div><div>${eta}</div></div><div><div class="text-gray-500">Size</div><div>${size}</div></div></div><div class="mt-3 grid grid-cols-3 gap-2">${qbitManagedActions(torrent.managed, true)}</div></div>`;
  };
  renderQbitGroups(groups, {
    prefix: 'dl',
    colspan: 7,
    showSpeeds: true,
    rowHtml,
    cardHtml,
    emptyText: 'No unfinished qBittorrent torrents.',
    cards,
    body,
  });
  renderQbitDownloadSummary(groups);
  if (window.reinitColumnResizer) window.reinitColumnResizer();
}
function renderQbitCompleted(groups) {
  const cards = document.getElementById('completed-torrent-cards');
  const body = document.getElementById('completed-torrents-body');
  if (!cards || !body) return;
  const escape = escapeHtml;
  const view = (torrent) => ({
    name: escape(torrent.name || torrent.hash || 'Unnamed torrent'),
    hash: escape(torrent.hash || '-'),
    state: escape(torrent.state || '-'),
    size: qbitBytes(torrent.size),
    downloaded: qbitBytes(torrent.downloaded),
    uploaded: qbitBytes(torrent.uploaded),
    ratio: qbitNumber(torrent.ratio, 1, 2),
    managedData: torrent.managed ? ` data-torrent-id="${Number(torrent.managed.id)}"` : '',
  });
  const rowHtml = (torrent, groupAttrs, child = false) => {
    const { name, state, size, downloaded, uploaded, ratio, managedData } = view(torrent);
    const rowClass = child ? 'hover:bg-surface-850/80 hidden' : 'hover:bg-surface-850/80';
    return `<tr class="${rowClass}"${groupAttrs}${managedData}><td class="px-5 py-3.5 text-sm font-medium text-white max-w-sm"><div class="truncate" title="${name}">${name}</div></td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${size}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${downloaded}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${uploaded}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${ratio}</td><td class="px-5 py-3.5 text-sm text-gray-400">${state}</td><td class="px-5 py-3.5 text-sm"><div class="flex items-center gap-3">${qbitManagedActions(torrent.managed)}</div></td></tr>`;
  };
  const cardHtml = (torrent) => {
    const { name, state, size, downloaded, uploaded, ratio, managedData } = view(torrent);
    return `<div class="rounded-xl border border-gray-700/60 bg-surface-850 p-3"${managedData}><div class="overflow-wrap-anywhere text-sm font-semibold text-white">${name}</div><div class="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400"><div><div class="text-gray-500">Size</div><div>${size}</div></div><div><div class="text-gray-500">Ratio</div><div>${ratio}</div></div><div><div class="text-gray-500">Downloaded</div><div>${downloaded}</div></div><div><div class="text-gray-500">Uploaded</div><div>${uploaded}</div></div><div><div class="text-gray-500">State</div><div>${state}</div></div></div><div class="mt-3 grid grid-cols-3 gap-2">${qbitManagedActions(torrent.managed, true)}</div></div>`;
  };
  renderQbitGroups(groups, {
    prefix: 'cp',
    colspan: 7,
    showSpeeds: false,
    rowHtml,
    cardHtml,
    emptyText: 'No completed qBittorrent torrents.',
    cards,
    body,
  });
  if (window.reinitColumnResizer) window.reinitColumnResizer();
}
function formatEta(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '—';
  if (seconds === 0) return 'now';
  const total = Math.round(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}
export async function refreshStagedTabData() {
  if (stagedTabRefreshInFlight) return;
  const stagedContent = document.getElementById('content-staged');
  if (!stagedContent) return;
  const restore = window.saveTabState('staged');
  stagedTabRefreshInFlight = true;
  try {
    const doc = await fetchPageDoc();
    if (!doc) return;
    const newContent = doc.getElementById('content-staged');
    if (!newContent) return;
    stagedContent.innerHTML = newContent.innerHTML;
    if (window.reinitColumnResizer) window.reinitColumnResizer();
    bindStagedSelectionHandlers();
    const stagedSelectAll = document.getElementById('staged-select-all');
    if (stagedSelectAll && window.bindSelectAll) {
      window.bindSelectAll(stagedSelectAll, '.staged-torrent-checkbox');
    }
    restore();
    refreshDashboardStatCards(doc);
  } catch (err) {
    console.error('Failed to refresh staged tab:', err);
  } finally {
    stagedTabRefreshInFlight = false;
  }
}
export async function refreshDownloadingTabData() {
  if (downloadingTabRefreshInFlight) return;
  const downloadingContent = document.getElementById('content-downloading');
  if (!downloadingContent) return;
  const restore = window.saveTabState('downloading');
  downloadingTabRefreshInFlight = true;
  try {
    const doc = await fetchPageDoc();
    if (!doc) return;
    const newContent = doc.getElementById('content-downloading');
    if (!newContent) return;
    downloadingContent.innerHTML = newContent.innerHTML;
    if (window.reinitColumnResizer) window.reinitColumnResizer();
    showQbitView(_torrentStatusView);
    await _patchStagedDownloadStatus();
    await window.loadDownloadHealth?.();
    restore();
    refreshDashboardStatCards(doc);
  } catch (err) {
    console.error('Failed to refresh downloading tab:', err);
  } finally {
    downloadingTabRefreshInFlight = false;
  }
}
function getDashboardStatCardsContainer(root = document) {
  return root.querySelector('[data-dashboard-stat-cards]');
}
function refreshDashboardStatCards(doc) {
  const statsContainer = getDashboardStatCardsContainer(document);
  const newStatsContainer = getDashboardStatCardsContainer(doc);
  if (statsContainer && newStatsContainer) {
    statsContainer.innerHTML = newStatsContainer.innerHTML;
  }
}
export function bindStagedSelectionHandlers() {
  bindCheckboxRangeSelection('.staged-torrent-checkbox');
}
export async function checkNow(torrentId) {
  try {
    const response = await fetch('/staged/' + torrentId + '/check-now', { method: 'POST' });
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || 'Server error: ' + response.status);
    }
    const data = await response.json();
    let msg = 'Check complete.';
    if (data.qbit_complete) msg += ' Download done.';
    else if (data.qbit_progress !== null) msg += ' Download ' + Math.round(data.qbit_progress * 100) + '%.';
    if (data.plex_available) msg += ' Available on Plex!';
    window.showToast(msg);
    await refreshDownloadingTabData();
  } catch (err) {
    window.showToast('Error: ' + err.message);
  }
}
export async function postStagedAction(actionUrl, redirectTo = '/?tab=staged') {
  try {
    const formData = new FormData();
    formData.append('redirect_to', redirectTo);
    const { response, cancelled } = await postWithConfirm(actionUrl, formData, promptEachWarning('Approve anyway?'));
    if (cancelled) return;
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || errorData?.message || `Server error: ${response.status}`);
    }
    const data = await response.json().catch(() => ({}));
    await refreshStagedTabData();
    if ((data.refresh || []).includes('downloading')) {
      await refreshDownloadingTabData();
    }
    window.showToast(data.message || 'Staged torrent updated');
  } catch (err) {
    window.showToast('Error: ' + err.message);
  }
}
function getSelectedStagedTorrentIds() {
  return Array.from(
    new Set(
      Array.from(document.querySelectorAll('.staged-torrent-checkbox:checked')).map((checkbox) => checkbox.value),
    ),
  );
}
export async function bulkStagedAction(action) {
  const selectedIds = getSelectedStagedTorrentIds();
  if (selectedIds.length === 0) {
    window.showToast('Select one or more staged torrents first.');
    return;
  }
  try {
    const formData = new FormData();
    formData.append('action', action);
    selectedIds.forEach((id) => formData.append('torrent_ids', id));
    formData.append('redirect_to', '/?tab=staged');
    const { response, cancelled } = await postWithConfirm('/staged/bulk', formData, (warnings) => {
      const byCode = new Map();
      warnings.forEach((warning) => {
        if (!byCode.has(warning.code)) byCode.set(warning.code, []);
        byCode.get(warning.code).push(`#${warning.torrent_id} ${warning.title}: ${warning.message}`);
      });
      return Array.from(byCode, ([code, messages]) => ({
        text: `${messages.join(`
`)}

Approve these anyway?`,
        code,
      }));
    });
    if (cancelled) return;
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || errorData?.message || `Server error: ${response.status}`);
    }
    document.querySelectorAll('.staged-torrent-checkbox').forEach((checkbox) => {
      checkbox.checked = false;
    });
    const data = await response.json().catch(() => ({}));
    await refreshStagedTabData();
    if ((data.refresh || []).includes('downloading')) {
      await refreshDownloadingTabData();
    }
    window.showToast(
      data.message || (action === 'approve' ? 'Selected torrents approved' : 'Selected torrents discarded'),
    );
  } catch (err) {
    window.showToast('Error: ' + err.message);
  }
}
export function closeStagedReview() {
  document.getElementById('staged-review-modal')?.classList.add('hidden');
}
function renderAlternative(item) {
  const evidence = item.rule_evidence || {};
  const selected = item.selected ? '<span class="badge badge-yellow">Selected</span>' : '';
  const active = item.active ? '<span class="badge badge-blue">Active</span>' : '';
  const seedersClass = item.seeders != null && Number(item.seeders) === 0 ? 'font-bold text-red-400' : '';
  return `<div class="rounded-xl border border-gray-700/60 bg-surface-850 p-3"><div class="flex flex-wrap items-center gap-2"><div class="min-w-0 flex-1 font-semibold text-white break-words">${escapeHtml(item.title || 'Unknown')}</div>${selected}${active}<span class="badge badge-gray">${escapeHtml(item.source || '')}</span></div><div class="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400 md:grid-cols-6"><div>Score <span class="text-emerald-400">${escapeHtml(String(item.score ?? 0))}</span></div><div>${escapeHtml(item.indexer || '-')}</div><div>${escapeHtml(item.resolution || '-')}</div><div>${escapeHtml(item.codec || '-')}</div><div>Seeders <span class="${seedersClass}">${escapeHtml(String(item.seeders ?? '-'))}</span></div><div>${escapeHtml(item.status || '')}</div></div>${item.rejection_reason ? `<div class="mt-2 text-red-300 text-xs">${escapeHtml(item.rejection_reason)}</div>` : ''}<div class="mt-3 flex flex-wrap gap-1">${window.renderRuleEvidence ? window.renderRuleEvidence(evidence) : ''}</div></div>`;
}
export async function openStagedReview(torrentId) {
  const modal = document.getElementById('staged-review-modal');
  const content = document.getElementById('staged-review-content');
  if (!modal || !content) return;
  modal.classList.remove('hidden');
  content.innerHTML = '<div class="text-gray-500">Loading alternatives...</div>';
  try {
    const response = await fetch(`/staged/${torrentId}/alternatives`, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`Server error: ${response.status}`);
    const data = await response.json();
    const items = data.alternatives || [];
    content.innerHTML = items.length
      ? `<div class="space-y-3">${items.map(renderAlternative).join('')}</div>`
      : '<div class="text-gray-500">No alternatives found.</div>';
  } catch (err) {
    content.innerHTML = `<div class="text-red-400">Failed to load alternatives: ${escapeHtml(err.message || 'Unknown error')}</div>`;
  }
}
Object.assign(window, {
  checkNow,
  postStagedAction,
  bulkStagedAction,
  refreshStagedTabData,
  refreshDownloadingTabData,
  bindStagedSelectionHandlers,
  showQbitView,
  setQbitTorrentSort,
  toggleQbitGroup,
  syncQbitGroupFromCard,
  _startStagedStatusPoll,
  _stopStagedStatusPoll,
  openStagedReview,
  closeStagedReview,
});
