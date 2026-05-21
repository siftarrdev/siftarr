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

const checkboxRangeAnchors = new Map();

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
    const content = document.getElementById('content-' + tabName);
    const tab = document.getElementById('tab-' + tabName);
    if (!content || !tab) {
        showTab('pending');
        return;
    }
    closeRequestDetails();
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-button').forEach(el => {
        el.classList.remove('border-brand-500', 'text-brand-400');
        el.classList.add('border-transparent', 'text-gray-500');
    });
    content.classList.remove('hidden');
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
    const isDisplayed = (element) => {
        if (element.style.display === 'none') return false;
        if (window.getComputedStyle) {
            let current = element;
            while (current && current !== activeTabContent.parentElement) {
                if (window.getComputedStyle(current).display === 'none') return false;
                current = current.parentElement;
            }
        }
        return true;
    };
    const seen = new Set();
    return Array.from(activeTabContent.querySelectorAll('[data-request-id]'))
        .filter(item => isDisplayed(item))
        .map(item => ({
            id: parseInt(item.getAttribute('data-request-id')),
            title: item.querySelector('[data-card-title]')?.textContent?.trim() || item.querySelector('td:nth-child(2), td:first-child')?.textContent?.trim() || item.dataset.title || 'Unknown'
        }))
        .filter(item => {
            if (!item.id || seen.has(item.id)) return false;
            seen.add(item.id);
            return true;
        });
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

function isRangeSelectableCheckboxVisible(checkbox) {
    const item = checkbox.closest('tr, [data-request-id], [data-torrent-id]');
    if (!item || item.style.display === 'none') return !item;
    if (!window.getComputedStyle) return true;
    let current = item;
    while (current && current !== document.body.parentElement) {
        if (window.getComputedStyle(current).display === 'none') return false;
        current = current.parentElement;
    }
    return true;
}

function getCheckboxRangeKey(checkbox, selector) {
    const scope = checkbox.closest('table') || checkbox.closest('form') || document;
    if (scope.id) return `${selector}:${scope.id}`;
    return selector;
}

function getVisibleCheckboxRange(checkbox, selector) {
    const scope = checkbox.closest('table') || checkbox.closest('form') || document;
    return Array.from(scope.querySelectorAll(selector)).filter(isRangeSelectableCheckboxVisible);
}

function bindCheckboxRangeSelection(selector) {
    document.querySelectorAll(selector).forEach(checkbox => {
        if (checkbox.dataset.checkboxRangeBound === 'true') return;
        checkbox.dataset.checkboxRangeBound = 'true';
        checkbox.addEventListener('click', event => {
            event.stopPropagation();

            const current = event.currentTarget;
            const key = getCheckboxRangeKey(current, selector);
            const visibleCheckboxes = getVisibleCheckboxRange(current, selector);
            const currentIndex = visibleCheckboxes.indexOf(current);
            const anchor = checkboxRangeAnchors.get(key);
            const anchorIndex = anchor ? visibleCheckboxes.indexOf(anchor) : -1;

            if (event.shiftKey && currentIndex >= 0 && anchorIndex >= 0) {
                const start = Math.min(anchorIndex, currentIndex);
                const end = Math.max(anchorIndex, currentIndex);
                visibleCheckboxes.slice(start, end + 1).forEach(rangeCheckbox => {
                    rangeCheckbox.checked = current.checked;
                });
            }

            checkboxRangeAnchors.set(key, current);
        });
    });
}

/**
 * Refresh the currently visible tab's content by fetching fresh HTML from the server.
 * Preserves text filter, media filter, and sort state.
 * Also updates stat cards.
 */
async function refreshCurrentTabContent() {
    const activeTabEl = document.querySelector('.tab-content:not(.hidden)');
    if (!activeTabEl) return;
    const tabName = activeTabEl.id.replace('content-', '');

    const restore = window.saveTabState(tabName);
    try {
        const response = await fetch(window.location.pathname, { headers: { 'Accept': 'text/html' } });
        if (!response.ok) return;
        const html = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');

        // Update tab content
        const newTabContent = doc.getElementById('content-' + tabName);
        if (newTabContent) {
            activeTabEl.innerHTML = newTabContent.innerHTML;
        }

        // Update stat cards
        const statsContainer = document.querySelector('.grid.grid-cols-2.md\\:grid-cols-7');
        const newStatsContainer = doc.querySelector('.grid.grid-cols-2.md\\:grid-cols-7');
        if (statsContainer && newStatsContainer) {
            statsContainer.innerHTML = newStatsContainer.innerHTML;
        }

        restore();

        // Re-bind selection handlers if applicable
        if (tabName === 'staged') {
            if (window.bindStagedSelectionHandlers) {
                window.bindStagedSelectionHandlers();
            }
            const stagedSelectAll = document.getElementById('staged-select-all');
            if (stagedSelectAll && window.bindSelectAll) {
                window.bindSelectAll(stagedSelectAll, '.staged-torrent-checkbox');
            }
        }
        if (tabName === 'active' || tabName === 'pending') {
            const selectAll = document.getElementById(tabName === 'active' ? 'active-select-all' : 'pending-select-all');
            const checkboxSelector = tabName === 'active' ? '.active-request-checkbox' : '.pending-request-checkbox';
            if (selectAll && window.bindSelectAll) {
                window.bindSelectAll(selectAll, checkboxSelector);
            }
            if (tabName === 'pending' && window.bindCheckboxRangeSelection) {
                window.bindCheckboxRangeSelection(checkboxSelector);
            }
        }
    } catch (err) {
        console.error('Failed to refresh tab content:', err);
    }
}

// Export functions to window for HTML onclick handlers
window.showTab = showTab;
window.closeRequestDetails = closeRequestDetails;
window.navigateDetails = navigateDetails;
window.escapeHtml = escapeHtml;
window.setActiveTab = setActiveTab;
window.setPoster = setPoster;
window.getVisibleRequests = getVisibleRequests;
window.refreshDetailsNavigationContext = refreshDetailsNavigationContext;
window.updateNavigationButtons = updateNavigationButtons;
window.refreshCurrentTabContent = refreshCurrentTabContent;
window.bindCheckboxRangeSelection = bindCheckboxRangeSelection;
