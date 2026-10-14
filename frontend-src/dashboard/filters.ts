// @ts-nocheck
// Dashboard Filters Module - Table filtering and sorting
// ======================================================

import { refreshDetailsNavigationContext } from '/static/js/dashboard/core.js';
import { dashboardState } from '/static/js/dashboard/core/state.js';

// Per-tab DOM ids and filter rules. `fields` lists the row dataset keys searched by
// the text filter. `media: false` skips the TV/movie filter, `lowercase` lowercases
// the searched text, and `cards` is the extra mobile card selector.
const TABS = {
  active: {
    input: 'filter-input',
    table: 'active-requests-table',
    body: 'active-requests-body',
    fields: ['title', 'type', 'statusLow', 'requestedby'],
  },
  pending: {
    input: 'pending-filter-input',
    table: 'pending-requests-table',
    body: 'pending-requests-body',
    cards: '#pending-request-cards [data-request-id]',
    cardContainer: 'pending-request-cards',
    fields: ['title', 'type', 'requestedby', 'status'],
  },
  unreleased: {
    input: 'unreleased-filter-input',
    table: 'unreleased-requests-table',
    body: 'unreleased-requests-body',
    cards: '#unreleased-request-cards [data-request-id]',
    cardContainer: 'unreleased-request-cards',
    fields: ['title', 'type', 'requestedby', 'releasedate'],
    lowercase: true,
  },
  staged: {
    input: 'staged-filter-input',
    table: 'staged-torrents-table',
    body: 'staged-torrents-body',
    cards: '#staged-torrent-cards [data-torrent-id]',
    cardContainer: 'staged-torrent-cards',
    fields: ['title', 'indexer', 'requeststate'],
    media: false,
  },
  downloading: {
    input: 'downloading-filter-input',
    table: 'downloading-torrents-table',
    body: 'downloading-torrents-body',
    cards: '#downloading-torrent-cards [data-torrent-id]',
    cardContainer: 'downloading-torrent-cards',
    fields: ['title', 'indexer', 'state', 'requeststate'],
    media: false,
  },
  finished: {
    input: 'finished-filter-input',
    table: 'finished-requests-table',
    body: 'finished-requests-body',
    cards: '#finished-request-cards [data-request-id]',
    cardContainer: 'finished-request-cards',
    fields: ['title', 'type', 'requestedby'],
  },
  rejected: {
    input: 'rejected-filter-input',
    table: 'rejected-requests-table',
    body: 'rejected-requests-body',
    cards: '#rejected-request-cards [data-request-id]',
    cardContainer: 'rejected-request-cards',
    fields: ['title', 'type', 'requestedby', 'reason'],
  },
};

const MEDIA_BUTTON_ACTIVE = ['border-brand-500', 'text-brand-400', 'bg-brand-500/10'];
const MEDIA_BUTTON_IDLE = ['border-gray-700/60', 'text-gray-500'];

function paintMediaButtons(tabName) {
  const mediaType = dashboardState.mediaFilterState[tabName] || null;
  const tvBtn = document.getElementById('media-filter-' + tabName + '-tv');
  const movieBtn = document.getElementById('media-filter-' + tabName + '-movie');
  [tvBtn, movieBtn].forEach((btn) => {
    if (!btn) return;
    btn.classList.remove(...MEDIA_BUTTON_ACTIVE);
    btn.classList.add(...MEDIA_BUTTON_IDLE);
  });
  const activeBtn = mediaType === 'tv' ? tvBtn : mediaType === 'movie' ? movieBtn : null;
  if (activeBtn) {
    activeBtn.classList.remove(...MEDIA_BUTTON_IDLE);
    activeBtn.classList.add(...MEDIA_BUTTON_ACTIVE);
  }
}

export function toggleMediaFilter(tabName, mediaType) {
  if (!dashboardState.mediaFilterState[tabName]) dashboardState.mediaFilterState[tabName] = null;

  if (dashboardState.mediaFilterState[tabName] === mediaType) {
    dashboardState.mediaFilterState[tabName] = null;
  } else {
    dashboardState.mediaFilterState[tabName] = mediaType;
  }

  paintMediaButtons(tabName);
  applyAllFilters(tabName);
}

function applyAllFilters(tabName) {
  if (TABS[tabName]) filterTab(tabName);
}

export function filterTab(tabName) {
  const config = TABS[tabName];
  const filterEl = document.getElementById(config.input);
  if (!filterEl) return;
  const filter = filterEl.value.toLowerCase();
  const mediaType = config.media === false ? null : dashboardState.mediaFilterState[tabName] || null;
  const selector = config.cards ? `#${config.body} tr, ${config.cards}` : `#${config.body} tr`;
  document.querySelectorAll(selector).forEach((row) => {
    let textContent = config.fields.map((field) => `${row.dataset[field]}`).join(' ');
    if (config.lowercase) textContent = textContent.toLowerCase();
    const textMatch = !filter || textContent.includes(filter);
    const mediaMatch = !mediaType || row.dataset.type === mediaType;
    row.style.display = textMatch && mediaMatch ? '' : 'none';
  });
  refreshDetailsNavigationContext();
}

export function filterTable() {
  filterTab('active');
}

export function filterTabByInput(inputId) {
  const tabName = Object.keys(TABS).find((name) => TABS[name].input === inputId);
  if (tabName) filterTab(tabName);
}

export function filterReleaseCards() {
  const filterEl = document.getElementById('release-filter-input');
  const releasesContainer = document.getElementById('request-details-releases');
  if (!filterEl || !releasesContainer) return;
  const filter = filterEl.value.toLowerCase();
  releasesContainer.innerHTML =
    '<ul class="divide-y divide-gray-700/40">' +
    dashboardState.currentReleases
      .filter((r) => !filter || r.title.toLowerCase().includes(filter))
      .map((release) => window.renderReleaseCard(release, dashboardState.currentRequestId))
      .join('') +
    '</ul>';
}

export function sortTable(tableName, sortKey, preserveDirection = false, forcedDirection = null) {
  const numericKeys = new Set(['ovrank', 'retrycount', 'year', 'size', 'score', 'progress', 'eta']);
  const state = dashboardState.tableSortState[tableName];
  const tbody = document.getElementById(TABS[tableName]?.body);
  const table = document.getElementById(TABS[tableName]?.table);
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

  table.querySelectorAll('.sort-indicator').forEach((el) => {
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
  rows.forEach((row) => tbody.appendChild(row));
  const cardContainer = document.getElementById(TABS[tableName]?.cardContainer);
  if (cardContainer) {
    rows.forEach((row) => {
      const card = row.dataset.torrentId
        ? cardContainer.querySelector(`[data-torrent-id="${row.dataset.torrentId}"]`)
        : cardContainer.querySelector(`[data-request-id="${row.dataset.requestId}"]`);
      if (card) cardContainer.appendChild(card);
    });
  }
  refreshDetailsNavigationContext();
}

export function sortDashboardCards(tableName, encodedSort) {
  const [sortKey, direction] = String(encodedSort || '').split(':');
  if (!sortKey) return;
  sortTable(tableName, sortKey, true, direction === 'desc' ? 'desc' : 'asc');
}

/**
 * Restore tab UI state after an in-place DOM refresh.
 * Re-applies media filter button styling, re-applies the current sort,
 * and updates sort indicators in table headers.
 */
export function restoreTabState(tabName) {
  paintMediaButtons(tabName);

  // Restore text filter value
  const filterInput = document.getElementById(TABS[tabName]?.input);
  if (filterInput && filterInput.dataset.priorValue !== undefined) {
    filterInput.value = filterInput.dataset.priorValue;
    delete filterInput.dataset.priorValue;
  }

  // Re-apply sorting using saved state
  const sortState = dashboardState.tableSortState[tabName];
  if (sortState && sortState.column) {
    // Update sort indicator in the header
    const table = document.getElementById(TABS[tabName]?.table);
    if (table) {
      table.querySelectorAll('.sort-indicator').forEach((el) => {
        el.textContent = '';
      });
      const indicator = table.querySelector(
        `th[data-table="${tabName}"][data-sort="${sortState.column}"] .sort-indicator`,
      );
      if (indicator) {
        indicator.textContent = sortState.direction === 'asc' ? ' \u25B2' : ' \u25BC';
      }
    }
    // Re-sort rows without toggling direction (preserveDirection=true)
    sortTable(tabName, sortState.column, true);
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
export function saveTabState(tabName) {
  // Save text filter value onto the input element so restoreTabState can find it
  const filterInput = document.getElementById(TABS[tabName]?.input);
  if (filterInput && filterInput.value) {
    filterInput.dataset.priorValue = filterInput.value;
  }

  // Return a cleanup function that restores state
  return function restore() {
    restoreTabState(tabName);
  };
}

// Temporary adapter for bootstrap code and HTML event handlers.
Object.assign(window, {
  toggleMediaFilter,
  filterTable,
  filterTabByInput,
  filterReleaseCards,
  sortTable,
  sortDashboardCards,
  restoreTabState,
  saveTabState,
});
