// Dashboard Core Module - Tab navigation, utilities, and global state
// ====================================================================

// Global state attached to window so all modules can reference it
window.tableSortState = {
    active: { column: null, direction: 'asc' },
    pending: { column: null, direction: 'asc' },
    unreleased: { column: null, direction: 'asc' },
    staged: { column: null, direction: 'asc' },
    downloading: { column: null, direction: 'asc' },
    finished: { column: null, direction: 'asc' },
    rejected: { column: null, direction: 'asc' },
};

window.mediaFilterState = {};

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

function ensureSearchProgressPanel() {
    let panel = document.getElementById('dashboard-search-progress-panel');
    if (panel) return panel;

    panel = document.createElement('section');
    panel.id = 'dashboard-search-progress-panel';
    panel.setAttribute('role', 'status');
    panel.setAttribute('aria-live', 'polite');
    panel.className = 'hidden dashboard-search-progress-panel fixed bottom-4 right-4 z-[100] w-[min(24rem,calc(100vw-2rem))] pointer-events-auto rounded-2xl border border-gray-700/70 bg-surface-800/95 shadow-2xl p-4';
    panel.innerHTML = '<div class="flex items-start justify-between gap-3 mb-3">' +
        '<div class="min-w-0">' +
            '<h3 id="dashboard-search-progress-title" class="text-sm font-semibold text-white">Searching requests</h3>' +
            '<p id="dashboard-search-progress-text" class="text-xs text-gray-500 mt-0.5">Working…</p>' +
        '</div>' +
    '</div>' +
    '<div class="mb-3"><div class="w-full h-2 rounded-full bg-surface-700 overflow-hidden">' +
        '<div id="dashboard-search-progress-bar" class="h-2 rounded-full bg-brand-500 transition-all duration-700" style="width: 0%"></div>' +
    '</div></div>' +
    '<p id="dashboard-search-status-text" class="text-sm text-gray-400">Preparing search…</p>' +
    '<div id="dashboard-search-active-wrap" class="hidden mt-3">' +
        '<div class="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Searching for</div>' +
        '<ul id="dashboard-search-active-list" class="space-y-1 text-gray-300 max-h-28 overflow-y-auto"></ul>' +
    '</div>';
    document.body.appendChild(panel);
    return panel;
}

function showSearchProgressPanel(title, message, items = []) {
    const panel = ensureSearchProgressPanel();
    const titleEl = document.getElementById('dashboard-search-progress-title');
    const textEl = document.getElementById('dashboard-search-progress-text');
    const statusEl = document.getElementById('dashboard-search-status-text');
    const listWrap = document.getElementById('dashboard-search-active-wrap');
    const listEl = document.getElementById('dashboard-search-active-list');
    const bar = document.getElementById('dashboard-search-progress-bar');

    if (titleEl) titleEl.textContent = title;
    if (textEl) textEl.textContent = 'Working…';
    if (statusEl) {
        statusEl.textContent = message;
        statusEl.classList.remove('text-red-400', 'text-emerald-400');
        statusEl.classList.add('text-gray-400');
    }
    if (bar) {
        bar.style.width = '15%';
        bar.classList.remove('bg-red-500', 'bg-emerald-500');
        bar.classList.add('bg-brand-500');
    }
    if (listEl && listWrap) {
        listEl.innerHTML = '';
        const values = items.slice(0, 5);
        values.forEach(item => {
            const li = document.createElement('li');
            li.className = 'rounded-lg bg-surface-850 px-2.5 py-1 text-xs text-gray-300 truncate';
            li.textContent = item;
            listEl.appendChild(li);
        });
        if (items.length > values.length) {
            const li = document.createElement('li');
            li.className = 'px-2.5 py-0.5 text-[11px] text-gray-500';
            li.textContent = '+' + (items.length - values.length) + ' more';
            listEl.appendChild(li);
        }
        listWrap.classList.toggle('hidden', items.length === 0);
    }
    panel.classList.remove('hidden');
    panel.style.zIndex = '100';
    window.setTimeout(() => { if (bar) bar.style.width = '90%'; }, 150);
}

function completeSearchProgressPanel(message, failed = false) {
    const panel = ensureSearchProgressPanel();
    const bar = document.getElementById('dashboard-search-progress-bar');
    const textEl = document.getElementById('dashboard-search-progress-text');
    const statusEl = document.getElementById('dashboard-search-status-text');
    panel.classList.remove('hidden');
    panel.style.zIndex = '100';
    if (bar) {
        bar.style.width = '100%';
        bar.classList.remove('bg-brand-500');
        bar.classList.add(failed ? 'bg-red-500' : 'bg-emerald-500');
    }
    if (textEl) textEl.textContent = failed ? 'Failed' : 'Complete';
    if (statusEl) {
        statusEl.textContent = message;
        statusEl.classList.remove('text-gray-400');
        statusEl.classList.add(failed ? 'text-red-400' : 'text-emerald-400');
    }
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
        if (window._stopStagedStatusPoll) window._stopStagedStatusPoll();
    } else if (tabName === 'downloading') {
        if (window.refreshDownloadingTabData) window.refreshDownloadingTabData();
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
    return Array.from(rows).filter(row => row.style.display !== 'none').map(row => ({
        id: parseInt(row.getAttribute('data-request-id')),
        title: row.querySelector('td:nth-child(2)')?.textContent?.trim() || 'Unknown'
    })).filter(r => r.id);
}

function refreshDetailsNavigationContext() {
    const modal = document.getElementById('request-details-modal');
    if (!modal || modal.classList.contains('hidden')) return;

    window.visibleRequests = window.getVisibleRequests();
    window.currentDetailsIndex = window.visibleRequests.findIndex(r => r.id === window.currentRequestId);
    window.updateNavigationButtons();
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

    const currentIndex = window.currentDetailsIndex >= 0 ? window.currentDetailsIndex : -1;
    position.textContent = currentIndex >= 0 ? `${currentIndex + 1} of ${total}` : `- of ${total}`;

    const prevIndex = currentIndex >= 0 ? (currentIndex - 1 + total) % total : total - 1;
    const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % total : 0;

    prevBtn.disabled = false;
    nextBtn.disabled = false;
    prevBtn.title = `← ${window.visibleRequests[prevIndex].title} (wraps around)`;
    nextBtn.title = `${window.visibleRequests[nextIndex].title} → (wraps around)`;
}

function navigateDetails(direction) {
    const total = window.visibleRequests.length;
    if (total === 0) return;

    if (window.currentDetailsIndex < 0) {
        window.currentDetailsIndex = direction < 0 ? total - 1 : 0;
    } else {
        window.currentDetailsIndex = (window.currentDetailsIndex + direction + total) % total;
    }
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
window.ensureSearchProgressPanel = ensureSearchProgressPanel;
window.showSearchProgressPanel = showSearchProgressPanel;
window.completeSearchProgressPanel = completeSearchProgressPanel;
window.setActiveTab = setActiveTab;
window.setPoster = setPoster;
window.getVisibleRequests = getVisibleRequests;
window.refreshDetailsNavigationContext = refreshDetailsNavigationContext;
window.updateNavigationButtons = updateNavigationButtons;
