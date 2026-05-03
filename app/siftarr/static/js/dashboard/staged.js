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
            document.querySelectorAll('#downloading-torrents-body tr')
        ).filter((row) => !activeTorrentIds.has(row.dataset.torrentId || ''));
        if (staleApprovedRows.length > 0) {
            await refreshDownloadingTabData();
            return;
        }

        for (const torrent of (data.torrents || [])) {
            const row = document.querySelector(`#downloading-torrents-body tr[data-torrent-id="${torrent.id}"]`);
            if (!row) continue;

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
                const stateLabel = torrent.qbit_state || 'sent to qBittorrent';
                const done = torrent.qbit_progress !== null && torrent.qbit_progress !== undefined && torrent.qbit_progress >= 1.0;
                stateSpan.className = `badge ${done ? 'badge-green' : 'badge-blue'}`;
                stateSpan.textContent = stateLabel;
                row.dataset.state = stateLabel.toLowerCase();
            }

            const reqStateTd = row.querySelector('td:nth-child(5)');
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

    const priorFilter = document.getElementById('staged-filter-input')?.value || '';
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
        const stagedFilterInput = document.getElementById('staged-filter-input');
        if (stagedFilterInput) stagedFilterInput.value = priorFilter;
        window.filterStagedTable();
        bindStagedSelectionHandlers();
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

    const priorFilter = document.getElementById('downloading-filter-input')?.value || '';

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
        const downloadingFilterInput = document.getElementById('downloading-filter-input');
        if (downloadingFilterInput) downloadingFilterInput.value = priorFilter;
        window.filterDownloadingTable();
        await _patchStagedDownloadStatus();
    } catch (err) {
        console.error('Failed to refresh downloading tab:', err);
    } finally {
        downloadingTabRefreshInFlight = false;
    }
}

function bindStagedSelectionHandlers() {
    document.querySelectorAll('.staged-torrent-checkbox').forEach((checkbox) => {
        checkbox.addEventListener('click', (event) => event.stopPropagation());
    });
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
        await refreshStagedTabData();
        window.showToast('Staged torrent updated');
    } catch (err) {
        window.showToast('Error: ' + err.message);
    }
}

function getSelectedStagedTorrentIds() {
    return Array.from(document.querySelectorAll('.staged-torrent-checkbox:checked')).map(
        (checkbox) => checkbox.value,
    );
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
        await refreshStagedTabData();
        window.showToast(action === 'approve' ? 'Selected torrents approved' : 'Selected torrents discarded');
    } catch (err) {
        window.showToast('Error: ' + err.message);
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
