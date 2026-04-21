// Dashboard Core Module - Tab navigation, utilities, and global state
// ====================================================================

// Global state attached to window so all modules can reference it
window.tableSortState = {
    active: { column: null, direction: 'asc' },
    pending: { column: null, direction: 'asc' },
    unreleased: { column: null, direction: 'asc' },
    staged: { column: null, direction: 'asc' },
    finished: { column: null, direction: 'asc' },
    rejected: { column: null, direction: 'asc' },
};

window.mediaFilterState = {};
window.showUnreleasedActive = false;

// Navigation state for prev/next in details modal
window.visibleRequests = [];
window.currentDetailsIndex = -1;

window.currentReleases = [];
window.currentRequestId = null;
window.currentTvSeasons = [];
window.currentActiveStagedTorrent = null;
window.currentRequestTimeline = [];

// Utility functions
function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function setActiveTab(tabName) {
    const url = new URL(window.location);
    url.searchParams.set('tab', tabName);
    window.history.replaceState({}, '', url);
}

function showTab(tabName) {
    closeRequestDetails();
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-button').forEach(el => {
        el.classList.remove('border-brand-500', 'text-brand-400');
        el.classList.add('border-transparent', 'text-gray-500');
    });
    document.getElementById('content-' + tabName).classList.remove('hidden');
    const tab = document.getElementById('tab-' + tabName);
    tab.classList.remove('border-transparent', 'text-gray-500');
    tab.classList.add('border-brand-500', 'text-brand-400');
    setActiveTab(tabName);
    if (tabName === 'staged') {
        if (window.refreshStagedTabData) window.refreshStagedTabData();
        if (window._startStagedStatusPoll) window._startStagedStatusPoll();
    } else {
        if (window._stopStagedStatusPoll) window._stopStagedStatusPoll();
    }
}

function setPoster(posterUrl, titleText) {
    const poster = document.getElementById('request-details-poster');
    const fallback = document.getElementById('request-details-poster-fallback');
    if (!poster || !fallback) return;

    poster.onerror = () => {
        poster.classList.add('hidden');
        poster.removeAttribute('src');
        fallback.textContent = 'Poster could not be loaded';
        fallback.classList.remove('hidden');
    };

    if (posterUrl) {
        poster.src = posterUrl;
        poster.alt = titleText;
        poster.className = 'w-full rounded-xl bg-surface-800 border border-gray-700/60 shadow-lg';
        poster.classList.remove('hidden');
        fallback.classList.add('hidden');
        return;
    }

    poster.classList.add('hidden');
    poster.removeAttribute('src');
    poster.alt = 'No poster available';
    fallback.textContent = 'No poster available';
    fallback.classList.remove('hidden');
}

function getVisibleRequests() {
    const activeTabContent = document.querySelector('.tab-content:not(.hidden)');
    if (!activeTabContent) return [];
    const rows = activeTabContent.querySelectorAll('tbody tr[data-request-id]');
    return Array.from(rows).map(row => ({
        id: parseInt(row.getAttribute('data-request-id')),
        title: row.querySelector('td:nth-child(2)')?.textContent?.trim() || 'Unknown'
    })).filter(r => r.id);
}

function updateNavigationButtons() {
    const prevBtn = document.getElementById('details-prev-btn');
    const nextBtn = document.getElementById('details-next-btn');
    const position = document.getElementById('details-position');
    if (!prevBtn || !nextBtn || !position) return;

    const total = window.visibleRequests.length;
    if (total === 0) {
        position.textContent = '- of -';
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        prevBtn.title = 'No items';
        nextBtn.title = 'No items';
        return;
    }

    position.textContent = `${window.currentDetailsIndex + 1} of ${total}`;

    const prevIndex = (window.currentDetailsIndex - 1 + total) % total;
    const nextIndex = (window.currentDetailsIndex + 1) % total;

    prevBtn.disabled = false;
    nextBtn.disabled = false;
    prevBtn.title = `← ${window.visibleRequests[prevIndex].title} (wraps around)`;
    nextBtn.title = `${window.visibleRequests[nextIndex].title} → (wraps around)`;
}

function navigateDetails(direction) {
    const total = window.visibleRequests.length;
    if (total === 0) return;

    window.currentDetailsIndex = (window.currentDetailsIndex + direction + total) % total;
    const targetRequest = window.visibleRequests[window.currentDetailsIndex];
    if (targetRequest) {
        openRequestDetails(targetRequest.id, window.currentDetailsIndex);
    }
}

function closeRequestDetails() {
    document.getElementById('request-details-modal').classList.add('hidden');
}

// Export functions to window for HTML onclick handlers
window.showTab = showTab;
window.closeRequestDetails = closeRequestDetails;
window.navigateDetails = navigateDetails;
window.escapeHtml = escapeHtml;
window.setActiveTab = setActiveTab;
window.setPoster = setPoster;
window.getVisibleRequests = getVisibleRequests;
window.updateNavigationButtons = updateNavigationButtons;
