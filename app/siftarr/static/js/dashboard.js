import '/static/js/dashboard/core.js';
import '/static/js/dashboard/releases.js';
import '/static/js/dashboard/filters.js';
import '/static/js/dashboard/details.js';
import '/static/js/dashboard/staged.js';
import '/static/js/dashboard/download_health.js';
import '/static/js/dashboard/modals.js';
import '/static/js/dashboard/search_sse.js';
import { ColumnResizer } from '/static/js/dashboard/core/column-resizer.js';
function initDashboard() {
  window.initDownloadHealth?.();
  const columnResizer = new ColumnResizer();
  window.reinitColumnResizer = () => columnResizer.attachHandles();
  const initialTab = new URLSearchParams(window.location.search).get('tab');
  if (initialTab && document.getElementById('content-' + initialTab) && document.getElementById('tab-' + initialTab)) {
    window.showTab(initialTab);
  }
  if (window.filterTable) {
    window.filterTable();
  }
  const activeSelectAll = document.getElementById('active-select-all');
  const pendingSelectAll = document.getElementById('pending-select-all');
  document.addEventListener('input', (event) => {
    const handlers = {
      'filter-input': window.filterTable,
      'pending-filter-input': window.filterPendingTable,
      'staged-filter-input': window.filterStagedTable,
      'downloading-filter-input': window.filterDownloadingTable,
      'finished-filter-input': window.filterFinishedTable,
      'rejected-filter-input': window.filterRejectedTable,
      'unreleased-filter-input': window.filterUnreleasedTable,
      'release-filter-input': window.filterReleaseCards,
    };
    handlers[event.target?.id]?.();
  });
  window.bindSelectAll(activeSelectAll, '.active-request-checkbox');
  window.bindSelectAll(pendingSelectAll, '.pending-request-checkbox');
  window.bindCheckboxRangeSelection('.pending-request-checkbox');
  const stagedSelectAll = document.getElementById('staged-select-all');
  window.bindSelectAll(stagedSelectAll, '.staged-torrent-checkbox');
  window.bindStagedSelectionHandlers();
  if (document.getElementById('unreleased-requests-table')) {
    window.sortTable('unreleased', 'releasedate');
  }
  document.addEventListener('click', (e) => {
    if (e.target.closest('.resize-handle')) return;
    const th = e.target.closest('th[data-table][data-sort]');
    if (!th) return;
    const sortKey = th.dataset.sort === 'ovstatus' ? 'ovrank' : th.dataset.sort;
    window.sortTable(th.dataset.table, sortKey);
  });
  document.addEventListener('keydown', (e) => {
    const modal = document.getElementById('request-details-modal');
    const largeTvSearchModal = document.getElementById('large-tv-search-modal');
    if (e.key === 'Escape' && largeTvSearchModal && !largeTvSearchModal.classList.contains('hidden')) {
      window.chooseLargeTvSearch('none');
      return;
    }
    if (modal.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      const dropdown = document.getElementById('tv-search-scope-menu');
      if (dropdown && !dropdown.classList.contains('hidden')) {
        window.closeTvSearchScopeMenu();
        return;
      }
      if (window.isActivityPanelOpen && window.isActivityPanelOpen()) {
        window.closeActivityPanel();
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
