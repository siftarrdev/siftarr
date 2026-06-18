// Dashboard Filters Module - Table filtering and sorting
// ======================================================

function toggleMediaFilter(tabName, mediaType) {
    if (!window.mediaFilterState[tabName]) window.mediaFilterState[tabName] = null;

    if (window.mediaFilterState[tabName] === mediaType) {
        window.mediaFilterState[tabName] = null;
    } else {
        window.mediaFilterState[tabName] = mediaType;
    }

    const tvBtn = document.getElementById('media-filter-' + tabName + '-tv');
    const movieBtn = document.getElementById('media-filter-' + tabName + '-movie');

    [tvBtn, movieBtn].forEach(btn => {
        if (!btn) return;
        btn.classList.remove('border-brand-500', 'text-brand-400', 'bg-brand-500/10');
        btn.classList.add('border-gray-700/60', 'text-gray-500');
    });

    const activeBtn = window.mediaFilterState[tabName] === 'tv' ? tvBtn : window.mediaFilterState[tabName] === 'movie' ? movieBtn : null;
    if (activeBtn) {
        activeBtn.classList.remove('border-gray-700/60', 'text-gray-500');
        activeBtn.classList.add('border-brand-500', 'text-brand-400', 'bg-brand-500/10');
    }

    applyAllFilters(tabName);
}

function applyAllFilters(tabName) {
    const filterMap = {
        active: filterTable,
        pending: filterPendingTable,
        unreleased: filterUnreleasedTable,
        staged: filterStagedTable,
        downloading: filterDownloadingTable,
        finished: filterFinishedTable,
        rejected: filterRejectedTable,
    };
    if (filterMap[tabName]) filterMap[tabName]();
}

function filterTable() {
    const filterEl = document.getElementById('filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    const mediaType = window.mediaFilterState['active'] || null;
    document.querySelectorAll('#active-requests-body tr').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.statusLow} ${row.dataset.requestedby}`;
        const textMatch = !filter || textContent.includes(filter);
        const mediaMatch = !mediaType || row.dataset.type === mediaType;
        row.style.display = (textMatch && mediaMatch) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterPendingTable() {
    const filterEl = document.getElementById('pending-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    const mediaType = window.mediaFilterState['pending'] || null;
    document.querySelectorAll('#pending-requests-body tr, #pending-request-cards [data-request-id]').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.requestedby} ${row.dataset.status}`;
        const textMatch = !filter || textContent.includes(filter);
        const mediaMatch = !mediaType || row.dataset.type === mediaType;
        row.style.display = (textMatch && mediaMatch) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterStagedTable() {
    const filterEl = document.getElementById('staged-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    document.querySelectorAll('#staged-torrents-body tr, #staged-torrent-cards [data-torrent-id]').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.indexer} ${row.dataset.requeststate}`;
        row.style.display = textContent.includes(filter) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterDownloadingTable() {
    const filterEl = document.getElementById('downloading-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    document.querySelectorAll('#downloading-torrents-body tr, #downloading-torrent-cards [data-torrent-id]').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.indexer} ${row.dataset.state} ${row.dataset.requeststate}`;
        row.style.display = textContent.includes(filter) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterFinishedTable() {
    const filterEl = document.getElementById('finished-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    const mediaType = window.mediaFilterState['finished'] || null;
    document.querySelectorAll('#finished-requests-body tr, #finished-request-cards [data-request-id]').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.requestedby}`;
        const textMatch = !filter || textContent.includes(filter);
        const mediaMatch = !mediaType || row.dataset.type === mediaType;
        row.style.display = (textMatch && mediaMatch) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterRejectedTable() {
    const filterEl = document.getElementById('rejected-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    const mediaType = window.mediaFilterState['rejected'] || null;
    document.querySelectorAll('#rejected-requests-body tr, #rejected-request-cards [data-request-id]').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.requestedby} ${row.dataset.reason}`;
        const textMatch = !filter || textContent.includes(filter);
        const mediaMatch = !mediaType || row.dataset.type === mediaType;
        row.style.display = (textMatch && mediaMatch) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterUnreleasedTable() {
    const filterEl = document.getElementById('unreleased-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    const mediaType = window.mediaFilterState['unreleased'] || null;
    document.querySelectorAll('#unreleased-requests-body tr, #unreleased-request-cards [data-request-id]').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.requestedby} ${row.dataset.releasedate}`.toLowerCase();
        const textMatch = !filter || textContent.includes(filter);
        const mediaMatch = !mediaType || row.dataset.type === mediaType;
        row.style.display = (textMatch && mediaMatch) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterReleaseCards() {
    const filterEl = document.getElementById('release-filter-input');
    const releasesContainer = document.getElementById('request-details-releases');
    if (!filterEl || !releasesContainer) return;
    const filter = filterEl.value.toLowerCase();
    releasesContainer.innerHTML = '<ul class="divide-y divide-gray-700/40">' + window.currentReleases
        .filter(r => !filter || r.title.toLowerCase().includes(filter))
        .map(release => window.renderReleaseCard(release, window.currentRequestId))
        .join('') + '</ul>';
}

function sortTable(tableName, sortKey, preserveDirection = false, forcedDirection = null) {
    const tableIdMap = {
        active: 'active-requests-table',
        pending: 'pending-requests-table',
        unreleased: 'unreleased-requests-table',
        staged: 'staged-torrents-table',
        downloading: 'downloading-torrents-table',
        finished: 'finished-requests-table',
        rejected: 'rejected-requests-table',
    };
    const bodyIdMap = {
        active: 'active-requests-body',
        pending: 'pending-requests-body',
        unreleased: 'unreleased-requests-body',
        staged: 'staged-torrents-body',
        downloading: 'downloading-torrents-body',
        finished: 'finished-requests-body',
        rejected: 'rejected-requests-body',
    };
    const numericKeys = new Set(['ovrank', 'retrycount', 'year', 'size', 'score', 'progress', 'eta']);
    const state = window.tableSortState[tableName];
    const tbody = document.getElementById(bodyIdMap[tableName]);
    const table = document.getElementById(tableIdMap[tableName]);
    if (!tbody || !table || !state) return;

    if (forcedDirection) {
        state.column = sortKey;
        state.direction = forcedDirection;
    } else if (state.column === sortKey) {
        // preserveDirection is used by restoreTabState to re-sort rows
        // without flipping the sort direction on every tab refresh.
        if (!preserveDirection) {
            state.direction = state.direction === 'asc' ? 'desc' : 'asc';
        }
    } else {
        state.column = sortKey;
        state.direction = 'asc';
    }

    table.querySelectorAll('.sort-indicator').forEach(el => {
        el.textContent = '';
    });
    const indicator = table.querySelector(`th[data-table="${tableName}"][data-sort="${sortKey}"] .sort-indicator`);
    if (indicator) {
        indicator.textContent = state.direction === 'asc' ? ' \u25B2' : ' \u25BC';
    }

    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {
        const aVal = a.dataset[sortKey] || '';
        const bVal = b.dataset[sortKey] || '';
        if (numericKeys.has(sortKey)) {
            const left = Number(aVal);
            const right = Number(bVal);
            return state.direction === 'asc' ? left - right : right - left;
        }
        if (aVal < bVal) return state.direction === 'asc' ? -1 : 1;
        if (aVal > bVal) return state.direction === 'asc' ? 1 : -1;
        return 0;
    });
    rows.forEach(row => tbody.appendChild(row));
    const cardContainerIdMap = {
        pending: 'pending-request-cards',
        unreleased: 'unreleased-request-cards',
        staged: 'staged-torrent-cards',
        downloading: 'downloading-torrent-cards',
        finished: 'finished-request-cards',
        rejected: 'rejected-request-cards',
    };
    const cardContainer = document.getElementById(cardContainerIdMap[tableName]);
    if (cardContainer) {
        rows.forEach(row => {
            const card = row.dataset.torrentId
                ? cardContainer.querySelector(`[data-torrent-id="${row.dataset.torrentId}"]`)
                : cardContainer.querySelector(`[data-request-id="${row.dataset.requestId}"]`);
            if (card) cardContainer.appendChild(card);
        });
    }
    window.refreshDetailsNavigationContext();
}

function sortDashboardCards(tableName, encodedSort) {
    const [sortKey, direction] = String(encodedSort || '').split(':');
    if (!sortKey) return;
    sortTable(tableName, sortKey, true, direction === 'desc' ? 'desc' : 'asc');
}

/**
 * Restore tab UI state after an in-place DOM refresh.
 * Re-applies media filter button styling, re-applies the current sort,
 * and updates sort indicators in table headers.
 */
function restoreTabState(tabName) {
    // Restore media filter button styling
    const activeMediaType = window.mediaFilterState[tabName] || null;
    const tvBtn = document.getElementById('media-filter-' + tabName + '-tv');
    const movieBtn = document.getElementById('media-filter-' + tabName + '-movie');
    [tvBtn, movieBtn].forEach(btn => {
        if (!btn) return;
        btn.classList.remove('border-brand-500', 'text-brand-400', 'bg-brand-500/10');
        btn.classList.add('border-gray-700/60', 'text-gray-500');
    });
    const activeBtn = activeMediaType === 'tv' ? tvBtn : activeMediaType === 'movie' ? movieBtn : null;
    if (activeBtn) {
        activeBtn.classList.remove('border-gray-700/60', 'text-gray-500');
        activeBtn.classList.add('border-brand-500', 'text-brand-400', 'bg-brand-500/10');
    }

    // Restore text filter value
    const filterInputIdMap = {
        active: 'filter-input',
        pending: 'pending-filter-input',
        unreleased: 'unreleased-filter-input',
        staged: 'staged-filter-input',
        downloading: 'downloading-filter-input',
        finished: 'finished-filter-input',
        rejected: 'rejected-filter-input',
    };
    const filterInput = document.getElementById(filterInputIdMap[tabName]);
    if (filterInput && filterInput.dataset.priorValue !== undefined) {
        filterInput.value = filterInput.dataset.priorValue;
        delete filterInput.dataset.priorValue;
    }

    // Re-apply sorting using saved state
    const sortState = window.tableSortState[tabName];
    if (sortState && sortState.column) {
        // Update sort indicator in the header
        const tableIdMap = {
            active: 'active-requests-table',
            pending: 'pending-requests-table',
            unreleased: 'unreleased-requests-table',
            staged: 'staged-torrents-table',
            downloading: 'downloading-torrents-table',
            finished: 'finished-requests-table',
            rejected: 'rejected-requests-table',
        };
        const table = document.getElementById(tableIdMap[tabName]);
        if (table) {
            table.querySelectorAll('.sort-indicator').forEach(el => {
                el.textContent = '';
            });
            const indicator = table.querySelector(
                `th[data-table="${tabName}"][data-sort="${sortState.column}"] .sort-indicator`
            );
            if (indicator) {
                indicator.textContent = sortState.direction === 'asc' ? ' \u25B2' : ' \u25BC';
            }
        }
        // Re-sort rows without toggling direction (preserveDirection=true)
        window.sortTable(tabName, sortState.column, true);
    } else {
        // No saved sort, just re-apply filters
        applyAllFilters(tabName);
    }
}

/**
 * Save and restore tab state around an in-place refresh.
 * Call before replacing innerHTML to save state, call after to restore.
 * Usage:
 *   const cleanup = saveTabState('staged');
 *   // ... replace DOM ...
 *   cleanup();
 */
function saveTabState(tabName) {
    // Save text filter value onto the input element so restoreTabState can find it
    const filterInputIdMap = {
        active: 'filter-input',
        pending: 'pending-filter-input',
        unreleased: 'unreleased-filter-input',
        staged: 'staged-filter-input',
        downloading: 'downloading-filter-input',
        finished: 'finished-filter-input',
        rejected: 'rejected-filter-input',
    };
    const filterInput = document.getElementById(filterInputIdMap[tabName]);
    if (filterInput && filterInput.value) {
        filterInput.dataset.priorValue = filterInput.value;
    }

    // Return a cleanup function that restores state
    return function restore() {
        restoreTabState(tabName);
    };
}

// Export functions to window for HTML onclick handlers
window.toggleMediaFilter = toggleMediaFilter;
window.filterTable = filterTable;
window.filterPendingTable = filterPendingTable;
window.filterStagedTable = filterStagedTable;
window.filterDownloadingTable = filterDownloadingTable;
window.filterFinishedTable = filterFinishedTable;
window.filterRejectedTable = filterRejectedTable;
window.filterUnreleasedTable = filterUnreleasedTable;
window.filterReleaseCards = filterReleaseCards;
window.sortTable = sortTable;
window.sortDashboardCards = sortDashboardCards;
window.restoreTabState = restoreTabState;
window.saveTabState = saveTabState;
