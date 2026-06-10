// Dashboard Staged Module - Staged tab polling and bulk actions
// =============================================================

let stagedTabRefreshInFlight = false;
let downloadingTabRefreshInFlight = false;
let _stagedStatusPollInterval = null;

function _startStagedStatusPoll() {
    _stopStagedStatusPoll();
    _patchStagedDownloadStatus();
    _stagedStatusPollInterval = setInterval(_patchStagedDownloadStatus, 30000);
}

function _stopStagedStatusPoll() {
    if (_stagedStatusPollInterval !== null) {
        clearInterval(_stagedStatusPollInterval);
        _stagedStatusPollInterval = null;
    }
}

async function _patchStagedDownloadStatus() {
    try {
        const response = await fetch('/staged/download-status');
        if (!response.ok) return;
        const data = await response.json();
        if ((data.torrents || []).some((torrent) => torrent.refresh_staged_tab)) {
            await refreshDownloadingTabData();
            return;
        }
        const activeTorrentIds = new Set((data.torrents || []).map((torrent) => String(torrent.id)));
        const staleApprovedRows = Array.from(
            document.querySelectorAll('#downloading-torrents-body tr, #downloading-torrent-cards [data-torrent-id]')
        ).filter((row) => !activeTorrentIds.has(row.dataset.torrentId || ''));
        if (staleApprovedRows.length > 0) {
            await refreshDownloadingTabData();
            return;
        }

        for (const torrent of (data.torrents || [])) {
            const rows = document.querySelectorAll(`#downloading-torrents-body tr[data-torrent-id="${torrent.id}"], #downloading-torrent-cards [data-torrent-id="${torrent.id}"]`);
            if (rows.length === 0) continue;

            for (const row of rows) {
                const progress = torrent.qbit_progress_percent;
                const progressEl = row.querySelector('[data-download-progress]');
                if (progressEl) {
                    progressEl.textContent = progress === null || progress === undefined ? '—' : `${progress.toFixed(1)}%`;
                }
                row.dataset.progress = progress === null || progress === undefined ? '-1' : String(progress);

                const etaSeconds = torrent.qbit_eta_seconds;
                const etaEl = row.querySelector('[data-download-eta]');
                if (etaEl) etaEl.textContent = formatEta(etaSeconds);
                row.dataset.eta = etaSeconds === null || etaSeconds === undefined || etaSeconds < 0 ? '999999999' : String(etaSeconds);

                const stateSpan = row.querySelector('[data-download-state]');
                if (stateSpan) {
                    const waitingForPlex = !!torrent.waiting_for_plex;
                    const stateLabel = waitingForPlex
                        ? 'qBittorrent finished; waiting for Plex'
                        : (torrent.qbit_state || 'sent to qBittorrent');
                    const done = torrent.qbit_complete || (torrent.qbit_progress !== null && torrent.qbit_progress !== undefined && torrent.qbit_progress >= 1.0);
                    stateSpan.className = `badge ${waitingForPlex ? 'badge-yellow' : (done ? 'badge-green' : 'badge-blue')}`;
                    stateSpan.textContent = stateLabel;
                    row.dataset.state = waitingForPlex ? 'qbit_finished_waiting_plex' : stateLabel.toLowerCase();
                    row.dataset.qbitFinishedWaitingPlex = waitingForPlex ? 'true' : 'false';
                }

                const reqStateTd = row.querySelector('[data-request-state-cell]');
                if (reqStateTd && torrent.request_status) {
                    const span = reqStateTd.querySelector('.badge');
                    if (span) {
                        const rs = torrent.request_status;
                        const cls = rs === 'downloading' ? 'badge-blue' : rs === 'staged' ? 'badge-yellow' : 'badge-gray';
                        span.className = `badge ${cls}`;
                        span.textContent = rs;
                        row.dataset.requeststate = rs;
                    }
                }

                const moveCell = row.querySelector('[data-move-cell]');
                if (moveCell) {
                    const status = torrent.move_status || 'pending';
                    const badge = moveCell.querySelector('[data-move-status]');
                    if (badge) {
                        const cls = status === 'moved' ? 'badge-green' : status === 'error' ? 'badge-red' : 'badge-gray';
                        badge.className = `badge ${cls}`;
                        badge.textContent = status;
                    }
                    const movedPath = moveCell.querySelector('[data-moved-path]');
                    if (movedPath) {
                        movedPath.textContent = torrent.moved_path || '';
                        movedPath.title = torrent.moved_path || '';
                        movedPath.classList.toggle('hidden', !torrent.moved_path);
                    }
                    row.dataset.movestatus = status;
                }
            }
        }
    } catch (_err) {
        // silently ignore poll errors
    }
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
