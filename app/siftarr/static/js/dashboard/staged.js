// Dashboard Staged Module - Staged tab polling and bulk actions
// =============================================================

const DOWNLOADS_POLL_INTERVAL_MS = 2000;

let stagedTabRefreshInFlight = false;
let downloadingTabRefreshInFlight = false;
let _stagedStatusPollInterval = null;
let _downloadStatusPatchInFlight = false;
let _stagedStatusPollWanted = false;

function _startStagedStatusPoll() {
    _stagedStatusPollWanted = true;
    _stopPollTimer();
    _patchStagedDownloadStatus();
    if (document.visibilityState === 'hidden') return;
    _stagedStatusPollInterval = setInterval(_patchStagedDownloadStatus, DOWNLOADS_POLL_INTERVAL_MS);
}

function _stopPollTimer() {
    if (_stagedStatusPollInterval !== null) {
        clearInterval(_stagedStatusPollInterval);
        _stagedStatusPollInterval = null;
    }
}

function _stopStagedStatusPoll() {
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
    _downloadStatusPatchInFlight = true;
    try {
        const response = await fetch('/api/downloads');
        if (!response.ok) return;
        const data = await response.json();
        if (!data.qbit_unavailable) renderQbitDownloads(data.torrents || []);
    } catch (_err) {
        // silently ignore poll errors
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

function renderQbitDownloads(torrents) {
    const cards = document.getElementById('downloading-torrent-cards');
    const body = document.getElementById('downloading-torrents-body');
    if (!cards || !body) return;
    const escape = window.escapeHtml;
    const rows = torrents.map((torrent) => {
        const name = escape(torrent.name || torrent.hash || 'Unnamed torrent');
        const hash = escape(torrent.hash || '-');
        const state = escape(torrent.state || '-');
        const category = escape(torrent.category || '-');
        const progress = qbitNumber(
            torrent.progress === null || torrent.progress === undefined ? null : Number(torrent.progress) * 100
        );
        const eta = formatEta(torrent.eta);
        const speed = `${qbitNumber(torrent.dlspeed, 1024 * 1024)} MB/s`;
        const size = `${qbitNumber(torrent.size, 1024 * 1024 * 1024, 2)} GB`;
        const managedData = torrent.managed ? ` data-torrent-id="${Number(torrent.managed.id)}"` : '';
        return { torrent, name, hash, state, category, progress, eta, speed, size, managedData };
    });
    cards.innerHTML = rows.length ? rows.map(({ torrent, name, hash, state, category, progress, eta, size, managedData }) => `<div class="rounded-xl border border-gray-700/60 bg-surface-850 p-3"${managedData}><div class="overflow-wrap-anywhere text-sm font-semibold text-white">${name}</div><div class="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400"><div><div class="text-gray-500">Progress</div><div>${progress}%</div></div><div><div class="text-gray-500">State</div><div>${state}</div></div><div><div class="text-gray-500">ETA</div><div>${eta}</div></div><div><div class="text-gray-500">Category</div><div>${category}</div></div><div><div class="text-gray-500">Size</div><div>${size}</div></div><div class="col-span-2"><div class="text-gray-500">Hash</div><div class="truncate" title="${hash}">${hash}</div></div></div><div class="mt-3 grid grid-cols-3 gap-2">${qbitManagedActions(torrent.managed, true)}</div></div>`).join('') : '<p class="text-sm text-gray-500">No unfinished qBittorrent torrents.</p>';
    body.innerHTML = rows.length ? rows.map(({ torrent, name, hash, state, category, progress, eta, speed, size, managedData }) => `<tr class="hover:bg-surface-850/80"${managedData}><td class="px-5 py-3.5 text-sm font-medium text-white max-w-sm"><div class="truncate" title="${name}">${name}</div><div class="mt-1 truncate text-xs text-gray-500" title="${hash}">${hash}</div></td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums" data-download-progress>${progress}%</td><td class="px-5 py-3.5 text-sm text-gray-400">${state}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums" data-download-eta>${eta}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${speed}</td><td class="px-5 py-3.5 text-sm text-gray-400 tabular-nums">${size}</td><td class="px-5 py-3.5 text-sm text-gray-400">${category}</td><td class="px-5 py-3.5 text-sm"><div class="flex items-center gap-3">${qbitManagedActions(torrent.managed)}</div></td></tr>`).join('') : '<tr><td colspan="8" class="px-5 py-6 text-sm text-gray-500">No unfinished qBittorrent torrents.</td></tr>';
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

async function refreshStagedTabData() {
    if (stagedTabRefreshInFlight) return;
    const stagedContent = document.getElementById('content-staged');
    if (!stagedContent) return;

    const restore = window.saveTabState('staged');
    stagedTabRefreshInFlight = true;
    try {
        const response = await fetch(window.location.pathname, { headers: { 'Accept': 'text/html' } });
        if (!response.ok) return;
        const html = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
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

async function refreshDownloadingTabData() {
    if (downloadingTabRefreshInFlight) return;
    const downloadingContent = document.getElementById('content-downloading');
    if (!downloadingContent) return;

    const restore = window.saveTabState('downloading');
    downloadingTabRefreshInFlight = true;
    try {
        const response = await fetch(window.location.pathname, { headers: { 'Accept': 'text/html' } });
        if (!response.ok) return;
        const html = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const newContent = doc.getElementById('content-downloading');
        if (!newContent) return;

        downloadingContent.innerHTML = newContent.innerHTML;
        if (window.reinitColumnResizer) window.reinitColumnResizer();
        await _patchStagedDownloadStatus();
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

function bindStagedSelectionHandlers() {
    if (window.bindCheckboxRangeSelection) {
        window.bindCheckboxRangeSelection('.staged-torrent-checkbox');
    }
}

async function checkNow(torrentId) {
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

async function postStagedAction(actionUrl, redirectTo = '/?tab=staged') {
    try {
        const formData = new FormData();
        formData.append('redirect_to', redirectTo);
        const response = await fetch(actionUrl, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData,
        });
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
    return Array.from(new Set(Array.from(document.querySelectorAll('.staged-torrent-checkbox:checked')).map(
        (checkbox) => checkbox.value,
    )));
}

async function bulkStagedAction(action) {
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

        const response = await fetch('/staged/bulk', {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData,
        });
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
        window.showToast(data.message || (action === 'approve' ? 'Selected torrents approved' : 'Selected torrents discarded'));
    } catch (err) {
        window.showToast('Error: ' + err.message);
    }
}

function closeStagedReview() {
    document.getElementById('staged-review-modal')?.classList.add('hidden');
}

function renderAlternative(item) {
    const evidence = item.rule_evidence || {};
    const selected = item.selected ? '<span class="badge badge-yellow">Selected</span>' : '';
    const active = item.active ? '<span class="badge badge-blue">Active</span>' : '';
    return `<div class="rounded-xl border border-gray-700/60 bg-surface-850 p-3"><div class="flex flex-wrap items-center gap-2"><div class="min-w-0 flex-1 font-semibold text-white break-words">${window.escapeHtml(item.title || 'Unknown')}</div>${selected}${active}<span class="badge badge-gray">${window.escapeHtml(item.source || '')}</span></div><div class="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400 md:grid-cols-6"><div>Score <span class="text-emerald-400">${window.escapeHtml(String(item.score ?? 0))}</span></div><div>${window.escapeHtml(item.indexer || '-')}</div><div>${window.escapeHtml(item.resolution || '-')}</div><div>${window.escapeHtml(item.codec || '-')}</div><div>Seeders ${window.escapeHtml(String(item.seeders ?? '-'))}</div><div>${window.escapeHtml(item.status || '')}</div></div>${item.rejection_reason ? `<div class="mt-2 text-red-300 text-xs">${window.escapeHtml(item.rejection_reason)}</div>` : ''}<div class="mt-3 flex flex-wrap gap-1">${window.renderRuleEvidence ? window.renderRuleEvidence(evidence) : ''}</div></div>`;
}

async function openStagedReview(torrentId) {
    const modal = document.getElementById('staged-review-modal');
    const content = document.getElementById('staged-review-content');
    if (!modal || !content) return;
    modal.classList.remove('hidden');
    content.innerHTML = '<div class="text-gray-500">Loading alternatives...</div>';
    try {
        const response = await fetch(`/staged/${torrentId}/alternatives`, { headers: { 'Accept': 'application/json' } });
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const data = await response.json();
        const items = data.alternatives || [];
        content.innerHTML = items.length ? `<div class="space-y-3">${items.map(renderAlternative).join('')}</div>` : '<div class="text-gray-500">No alternatives found.</div>';
    } catch (err) {
        content.innerHTML = `<div class="text-red-400">Failed to load alternatives: ${window.escapeHtml(err.message || 'Unknown error')}</div>`;
    }
}

// Export functions to window for HTML onclick handlers
window.checkNow = checkNow;
window.postStagedAction = postStagedAction;
window.bulkStagedAction = bulkStagedAction;
window.refreshStagedTabData = refreshStagedTabData;
window.refreshDownloadingTabData = refreshDownloadingTabData;
window.bindStagedSelectionHandlers = bindStagedSelectionHandlers;
window._startStagedStatusPoll = _startStagedStatusPoll;
window._stopStagedStatusPoll = _stopStagedStatusPoll;
window.openStagedReview = openStagedReview;
window.closeStagedReview = closeStagedReview;
