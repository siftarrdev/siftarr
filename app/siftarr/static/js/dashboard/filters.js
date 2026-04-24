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
    document.querySelectorAll('#pending-requests-body tr').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.requestedby} ${row.dataset.lasterror}`;
        const textMatch = !filter || textContent.includes(filter);
        const mediaMatch = !mediaType || row.dataset.type === mediaType;
        row.style.display = (textMatch && mediaMatch) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterStagedTable() {
    const filterEl = document.getElementById('staged-filter-input');
    const statusEl = document.getElementById('staged-status-filter');
    if (!filterEl || !statusEl) return;
    const filter = filterEl.value.toLowerCase();
    const selectedStatus = statusEl.value;
    document.querySelectorAll('#staged-torrents-body tr').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.indexer} ${row.dataset.state} ${row.dataset.requeststate}`;
        const torrentState = row.dataset.state || '';
        row.style.display = textContent.includes(filter) && (!selectedStatus || torrentState === selectedStatus) ? '' : 'none';
    });
    window.refreshDetailsNavigationContext();
}

function filterFinishedTable() {
    const filterEl = document.getElementById('finished-filter-input');
    if (!filterEl) return;
    const filter = filterEl.value.toLowerCase();
    const mediaType = window.mediaFilterState['finished'] || null;
    document.querySelectorAll('#finished-requests-body tr').forEach(row => {
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
    document.querySelectorAll('#rejected-requests-body tr').forEach(row => {
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
    document.querySelectorAll('#unreleased-requests-body tr').forEach(row => {
        const textContent = `${row.dataset.title} ${row.dataset.type} ${row.dataset.requestedby} ${row.dataset.expected}`.toLowerCase();
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
    releasesContainer.innerHTML = window.currentReleases
        .filter(r => !filter || r.title.toLowerCase().includes(filter))
        .map(release => window.renderReleaseCard(release, window.currentRequestId))
        .join('');
}

function sortTable(tableName, sortKey) {
    const tableIdMap = {
        active: 'active-requests-table',
        pending: 'pending-requests-table',
        unreleased: 'unreleased-requests-table',
        staged: 'staged-torrents-table',
        finished: 'finished-requests-table',
        rejected: 'rejected-requests-table',
    };
    const bodyIdMap = {
        active: 'active-requests-body',
        pending: 'pending-requests-body',
        unreleased: 'unreleased-requests-body',
        staged: 'staged-torrents-body',
        finished: 'finished-requests-body',
        rejected: 'rejected-requests-body',
    };
    const numericKeys = new Set(['ovrank', 'retrycount', 'size', 'score']);
    const state = window.tableSortState[tableName];
    const tbody = document.getElementById(bodyIdMap[tableName]);
    const table = document.getElementById(tableIdMap[tableName]);
    if (!tbody || !table || !state) return;

    if (state.column === sortKey) {
        state.direction = state.direction === 'asc' ? 'desc' : 'asc';
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
    window.refreshDetailsNavigationContext();
}

// Export functions to window for HTML onclick handlers
window.toggleMediaFilter = toggleMediaFilter;
window.filterTable = filterTable;
window.filterPendingTable = filterPendingTable;
window.filterStagedTable = filterStagedTable;
window.filterFinishedTable = filterFinishedTable;
window.filterRejectedTable = filterRejectedTable;
window.filterUnreleasedTable = filterUnreleasedTable;
window.filterReleaseCards = filterReleaseCards;
window.sortTable = sortTable;
