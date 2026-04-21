// Dashboard Staged Module - Staged tab polling and bulk actions
// =============================================================

let stagedTabRefreshInFlight = false;
let _stagedStatusPollInterval = null;

function _startStagedStatusPoll() {
    _stopStagedStatusPoll();
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
            await refreshStagedTabData();
            return;
        }
        const activeTorrentIds = new Set((data.torrents || []).map((torrent) => String(torrent.id)));
        const staleApprovedRows = Array.from(
            document.querySelectorAll('#staged-torrents-body tr[data-state="approved"]')
        ).filter((row) => !activeTorrentIds.has(row.dataset.torrentId || ''));
        if (staleApprovedRows.length > 0) {
            await refreshStagedTabData();
            return;
        }

        for (const torrent of (data.torrents || [])) {
            const row = document.querySelector(`tr[data-torrent-id="${torrent.id}"]`);
            if (!row) continue;

            const cells = row.querySelectorAll('td');
            if (cells.length < 4) continue;

            // Torrent state cell: index 2
            const stateTd = cells[2];
            if (stateTd) {
                let stateLabel = 'sent to qBittorrent';
                let badgeClass = 'badge-blue';
                if (torrent.qbit_progress !== null && torrent.qbit_progress !== undefined) {
                    const pct = Math.round(torrent.qbit_progress * 100);
                    stateLabel = `downloading ${pct}%`;
                    if (torrent.qbit_progress >= 1.0) {
                        stateLabel = 'done';
                        badgeClass = 'badge-green';
                    }
                }
                if (torrent.qbit_state) {
                    stateLabel += ` (${torrent.qbit_state})`;
                }
                const span = stateTd.querySelector('.badge');
                if (span) {
                    span.className = `badge ${badgeClass}`;
                    span.textContent = stateLabel;
                }
            }

            // Request state cell: index 3
            const reqStateTd = cells[3];
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

async function refreshStagedTabData() {
    if (stagedTabRefreshInFlight) return;
    const stagedContent = document.getElementById('content-staged');
    if (!stagedContent) return;

    const priorFilter = document.getElementById('staged-filter-input')?.value || '';
    const priorStatus = document.getElementById('staged-status-filter')?.value || '';

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
        const stagedStatusFilter = document.getElementById('staged-status-filter');
        if (stagedFilterInput) stagedFilterInput.value = priorFilter;
        if (stagedStatusFilter) stagedStatusFilter.value = priorStatus;
        filterStagedTable();
        bindStagedSelectionHandlers();
    } catch (err) {
        console.error('Failed to refresh staged tab:', err);
    } finally {
        stagedTabRefreshInFlight = false;
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
        showToast(msg);
        await refreshStagedTabData();
    } catch (err) {
        showToast('Error: ' + err.message);
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
        showToast('Staged torrent updated');
    } catch (err) {
        showToast('Error: ' + err.message);
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
        showToast('Select one or more staged torrents first.');
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
        showToast(action === 'approve' ? 'Selected torrents approved' : 'Selected torrents discarded');
    } catch (err) {
        showToast('Error: ' + err.message);
    }
}

// Export functions to window for HTML onclick handlers
window.checkNow = checkNow;
window.postStagedAction = postStagedAction;
window.bulkStagedAction = bulkStagedAction;
window.refreshStagedTabData = refreshStagedTabData;
window.bindStagedSelectionHandlers = bindStagedSelectionHandlers;
