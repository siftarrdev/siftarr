// Dashboard Main Entry Point
// ==========================
// This file imports all dashboard modules and initializes the application.

const moduleVersion = encodeURIComponent(window.siftarrStaticVersion || Date.now().toString());

await import(`./dashboard/core.js?v=${moduleVersion}`);
await import(`./dashboard/releases.js?v=${moduleVersion}`);
await import(`./dashboard/filters.js?v=${moduleVersion}`);
await import(`./dashboard/details.js?v=${moduleVersion}`);
await import(`./dashboard/staged.js?v=${moduleVersion}`);
await import(`./dashboard/modals.js?v=${moduleVersion}`);
await import(`./dashboard/search_sse.js?v=${moduleVersion}`);

// Column Resizer Class - Must be defined here as it's used across modules
class ColumnResizer {
    constructor() {
        this.tables = document.querySelectorAll('table.data-resizable');
        this.storageKey = 'siftarr_col_widths';
        this.savedWidths = this.loadWidths();
        this.activeHandle = null;
        this.activeCol = null;
        this.startX = 0;
        this.startWidth = 0;
        this.minWidth = 60;
        this.init();
    }

    loadWidths() {
        try {
            return JSON.parse(localStorage.getItem(this.storageKey)) || {};
        } catch {
            return {};
        }
    }

    saveWidths() {
        localStorage.setItem(this.storageKey, JSON.stringify(this.savedWidths));
    }

    init() {
        this.tables.forEach(table => {
            const tableId = table.id;
            if (!this.savedWidths[tableId]) {
                this.savedWidths[tableId] = {};
            }
            const tableWidths = this.savedWidths[tableId];

            table.querySelectorAll('th[data-col-key]').forEach(th => {
                const colKey = th.dataset.colKey;
                const col = table.querySelector(`col[data-col-key="${colKey}"]`);
                if (!col) return;

                if (tableWidths[colKey]) {
                    col.style.width = tableWidths[colKey] + 'px';
                }
                const handle = th.querySelector('.resize-handle');
                if (handle) {
                    handle.addEventListener('mousedown', (e) => this.startResize(e, col));
                }
            });
        });

        document.addEventListener('mousemove', (e) => this.onMouseMove(e));
        document.addEventListener('mouseup', (e) => this.endResize(e));
        document.addEventListener('mouseleave', (e) => this.endResize(e));
    }

    startResize(e, col) {
        e.preventDefault();
        e.stopPropagation();
        this.activeHandle = e.target;
        this.activeCol = col;
        this.startX = e.clientX;
        this.startWidth = parseInt(col.style.width) || col.offsetWidth || 100;
        this.activeHandle.classList.add('dragging');
        document.body.classList.add('resizing');
    }

    onMouseMove(e) {
        if (!this.activeHandle || !this.activeCol) return;
        const dx = e.clientX - this.startX;
        const newWidth = Math.max(this.minWidth, this.startWidth + dx);
        this.activeCol.style.width = newWidth + 'px';
    }

    endResize(e) {
        if (!this.activeHandle || !this.activeCol) return;

        const tableId = this.activeCol.closest('table').id;
        const colKey = this.activeCol.dataset.colKey;
        const finalWidth = parseInt(this.activeCol.style.width) || this.activeCol.offsetWidth;

        if (!this.savedWidths[tableId]) {
            this.savedWidths[tableId] = {};
        }
        this.savedWidths[tableId][colKey] = finalWidth;
        this.saveWidths();

        this.activeHandle.classList.remove('dragging');
        document.body.classList.remove('resizing');
        this.activeHandle = null;
        this.activeCol = null;
    }
}

// Initialize on DOM ready
// Note: because this module uses top-level await import(), DOMContentLoaded
// may fire before the dynamic imports resolve, so we use the readyState
// guard pattern (same as modals.js).
function initDashboard() {
    // Initialize column resizer
    new ColumnResizer();

    // Set initial tab from URL
    const initialTab = new URLSearchParams(window.location.search).get('tab');
    if (initialTab && document.getElementById('content-' + initialTab) && document.getElementById('tab-' + initialTab)) {
        window.showTab(initialTab);
    }

    // Apply initial filters
    if (window.filterTable) {
        window.filterTable();
    }

    // Bind event listeners for filter inputs
    const filterInput = document.getElementById('filter-input');
    const pendingFilterInput = document.getElementById('pending-filter-input');
    const stagedFilterInput = document.getElementById('staged-filter-input');
    const downloadingFilterInput = document.getElementById('downloading-filter-input');
    const finishedFilterInput = document.getElementById('finished-filter-input');
    const rejectedFilterInput = document.getElementById('rejected-filter-input');
    const unreleasedFilterInput = document.getElementById('unreleased-filter-input');
    const activeSelectAll = document.getElementById('active-select-all');
    const pendingSelectAll = document.getElementById('pending-select-all');

    if (filterInput) filterInput.addEventListener('input', window.filterTable);
    if (pendingFilterInput) pendingFilterInput.addEventListener('input', window.filterPendingTable);
    if (downloadingFilterInput) downloadingFilterInput.addEventListener('input', window.filterDownloadingTable);
    if (finishedFilterInput) finishedFilterInput.addEventListener('input', window.filterFinishedTable);
    if (rejectedFilterInput) rejectedFilterInput.addEventListener('input', window.filterRejectedTable);
    if (unreleasedFilterInput) unreleasedFilterInput.addEventListener('input', window.filterUnreleasedTable);

    const releaseFilterInput = document.getElementById('release-filter-input');
    if (releaseFilterInput) releaseFilterInput.addEventListener('input', window.filterReleaseCards);

    document.addEventListener('input', (event) => {
        if (event.target?.id === 'staged-filter-input') {
            window.filterStagedTable();
        }
        if (event.target?.id === 'downloading-filter-input') {
            window.filterDownloadingTable();
        }
    });


    // Bind select all checkboxes
    window.bindSelectAll(activeSelectAll, '.active-request-checkbox');
    window.bindSelectAll(pendingSelectAll, '.pending-request-checkbox');
    window.bindCheckboxRangeSelection('.pending-request-checkbox');
    const stagedSelectAll = document.getElementById('staged-select-all');
    window.bindSelectAll(stagedSelectAll, '.staged-torrent-checkbox');
    window.bindStagedSelectionHandlers();

    if (document.getElementById('unreleased-requests-table')) {
        window.sortTable('unreleased', 'releasedate');
    }

    // Bind sort handlers to the full sortable header cell. This keeps sorting reliable
    // when users click header padding, labels, or sort indicators, while leaving the
    // column resize handle reserved for resizing only.
    document.addEventListener('click', (e) => {
        if (e.target.closest('.resize-handle')) return;
        const th = e.target.closest('th[data-table][data-sort]');
        if (!th) return;
        const sortKey = th.dataset.sort === 'ovstatus' ? 'ovrank' : th.dataset.sort;
        window.sortTable(th.dataset.table, sortKey);
    });

    // Keyboard navigation for details modal
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('request-details-modal');
        if (modal.classList.contains('hidden')) return;
        if (e.key === 'Escape') {
            const dropdown = document.getElementById('tv-search-scope-menu');
            if (dropdown && !dropdown.classList.contains('hidden')) {
                window.closeTvSearchScopeMenu();
                return;
            }
            window.closeRequestDetails();
        } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            window.navigateDetails(-1);
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            window.navigateDetails(1);
        }
    });

    // Close TV search dropdown on outside click
    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('tv-search-scope-menu');
        const wrapper = document.getElementById('request-details-tv-search-btn');
        if (dropdown && !dropdown.classList.contains('hidden') && wrapper && !wrapper.contains(e.target)) {
            window.closeTvSearchScopeMenu();
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDashboard);
} else {
    initDashboard();
}
