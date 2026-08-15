// Dashboard Details Module - Request details modal and timeline
// =============================================================

window.detailsControlState = window.detailsControlState || {};
window.detailsControlHandlersReady = false;
window.detailsControlDebounce = null;
window.detailsAutoSearchStarted = window.detailsAutoSearchStarted || {};
window.liveDetailsRefresh = window.liveDetailsRefresh || {
  timer: null,
  inFlight: false,
  pending: false,
  requestId: null,
};

function cancelLiveDetailsRefresh(requestId = null) {
  const state = window.liveDetailsRefresh;
  if (requestId !== null && state.requestId !== requestId) return;
  clearTimeout(state.timer);
  state.timer = null;
  state.pending = false;
  state.requestId = null;
}

// SSE can report several committed result batches in quick succession.  Keep
// the DB-backed details render responsive without replacing the modal DOM for
// every batch.
function scheduleLiveDetailsRefresh(requestId) {
  const modal = document.getElementById('request-details-modal');
  if (!modal || modal.classList.contains('hidden') || window.activeDetailsRequestId !== requestId) return;
  const state = window.liveDetailsRefresh;
  state.requestId = requestId;
  state.pending = true;
  if (state.timer || state.inFlight) return;
  state.timer = setTimeout(async function () {
    state.timer = null;
    if (!state.pending || modal.classList.contains('hidden') || window.activeDetailsRequestId !== requestId) return;
    state.pending = false;
    state.inFlight = true;
    const scrollTop = modal.scrollTop;
    try {
      await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
      if (!modal.classList.contains('hidden') && window.activeDetailsRequestId === requestId)
        modal.scrollTop = scrollTop;
    } finally {
      state.inFlight = false;
      if (state.pending) scheduleLiveDetailsRefresh(requestId);
    }
  }, 1250);
}

async function reloadOpenDetailsIfActive(requestId) {
  if (window.activeDetailsRequestId !== requestId) return;
  cancelLiveDetailsRefresh(requestId);
  await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
}

function defaultDetailsControls() {
  // `scope` is client-only (TV accordion: 'all' | 'season_packs' | 'complete_series').
  // It is deliberately NOT sent in `buildDetailsUrl` — the backend filter/sort
  // contract is unchanged; scope chips re-render client-side via
  // `applyLocalReleaseSort`.
  return { title: '', resolution: 'all', sort: 'score', direction: 'desc', scope: 'all' };
}

function getDetailsControls(requestId) {
  if (!window.detailsControlState[requestId]) {
    window.detailsControlState[requestId] = defaultDetailsControls();
  }
  return window.detailsControlState[requestId];
}

function resetDetailsControls(requestId, options = {}) {
  window.detailsControlState[requestId] = defaultDetailsControls();
  if (options.updateInputs) window.applyDetailsControls(window.detailsControlState[requestId]);
}

function buildDetailsUrl(requestId) {
  const controls = getDetailsControls(requestId);
  const params = new URLSearchParams();
  if (controls.title) params.set('title', controls.title);
  if (controls.resolution && controls.resolution !== 'all') params.set('resolution', controls.resolution);
  if (controls.sort && controls.sort !== 'score') params.set('sort', controls.sort);
  if (controls.direction && controls.direction !== 'desc') params.set('direction', controls.direction);
  const suffix = params.toString();
  return `/requests/${requestId}/details${suffix ? '?' + suffix : ''}`;
}

function applyDetailsControls(controls) {
  const filterInput = document.getElementById('release-filter-input');
  const resolutionSelect = document.getElementById('release-resolution-filter');
  const sortSelect = document.getElementById('release-sort-key');
  const directionBtn = document.getElementById('release-sort-direction');
  if (filterInput) filterInput.value = controls.title || '';
  if (resolutionSelect) resolutionSelect.value = controls.resolution === '2160p' ? '4k' : controls.resolution || 'all';
  if (sortSelect) sortSelect.value = controls.sort || 'score';
  if (directionBtn) {
    const direction = controls.direction || 'desc';
    directionBtn.dataset.direction = direction;
    directionBtn.textContent = direction === 'asc' ? 'Asc' : 'Desc';
  }
}

function updateReleaseCountText(data) {
  const countEl = document.getElementById('release-results-count');
  if (!countEl) return;
  const filtered = data.filtered_total_releases ?? (data.releases || []).length;
  const total = data.total_releases ?? filtered;
  countEl.textContent = filtered === total ? `${filtered} results` : `${filtered} of ${total} results`;
}

function normalizeReleaseSortValue(value) {
  if (typeof value === 'string') return value.toLocaleLowerCase();
  if (typeof value === 'number') return value;
  return value || '';
}

function releaseSortValue(release, sortKey) {
  if (sortKey === 'published') return release.publish_date || '';
  if (sortKey === 'size') return release.size_bytes || 0;
  if (sortKey === 'seeders') return release.seeders || 0;
  if (sortKey === 'title') return release.title || '';
  if (sortKey === 'indexer') return release.indexer || '';
  return release.score || 0;
}

function compareReleasesForDetails(a, b, controls) {
  const direction = controls.direction === 'asc' ? 1 : -1;
  const primaryA = normalizeReleaseSortValue(releaseSortValue(a, controls.sort || 'score'));
  const primaryB = normalizeReleaseSortValue(releaseSortValue(b, controls.sort || 'score'));
  if (primaryA < primaryB) return -1 * direction;
  if (primaryA > primaryB) return 1 * direction;
  return String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' });
}

function applyLocalReleaseSort() {
  if (!window.currentRequestId) return false;
  const releasesEl = document.getElementById('request-details-releases');
  if (!releasesEl) return false;
  const controls = getDetailsControls(window.currentRequestId);
  const sortReleases = (releases) => (releases || []).slice().sort((a, b) => compareReleasesForDetails(a, b, controls));
  if (window.currentRequestMediaType === 'tv' && window.currentDetailsData?.tv_info) {
    const data = JSON.parse(JSON.stringify(window.currentDetailsData));
    data.tv_info.releases_by_season = data.tv_info.releases_by_season || {};
    data.tv_info.releases_by_episode = data.tv_info.releases_by_episode || {};
    Object.keys(data.tv_info.releases_by_season).forEach((key) => {
      data.tv_info.releases_by_season[key] = sortReleases(data.tv_info.releases_by_season[key]);
    });
    Object.keys(data.tv_info.releases_by_episode).forEach((key) => {
      data.tv_info.releases_by_episode[key] = sortReleases(data.tv_info.releases_by_episode[key]);
    });
    data.releases = sortReleases(data.releases);
    const preservedDetailsState = window.captureDetailsAccordionState ? window.captureDetailsAccordionState() : null;
    releasesEl.innerHTML = window.renderSeasonAccordion(data);
    if (preservedDetailsState && window.restoreDetailsAccordionState) {
      window.restoreDetailsAccordionState(preservedDetailsState);
    }
    if (window.updateTvAccordionControls) window.updateTvAccordionControls();
    window.currentDetailsData = data;
    window.currentReleases = data.releases || [];
    return true;
  }
  if (Array.isArray(window.currentReleases)) {
    window.currentReleases = sortReleases(window.currentReleases);
    releasesEl.innerHTML =
      '<ul class="divide-y divide-gray-700/40">' +
      window.currentReleases.map((release) => window.renderReleaseCard(release, window.currentRequestId)).join('') +
      '</ul>';
    if (window.currentDetailsData) window.currentDetailsData.releases = window.currentReleases;
    return true;
  }
  return false;
}

function reloadDetailsWithControls(debounceMs = 0) {
  if (!window.currentRequestId) return;
  const run = () =>
    window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, {
      preserveUiState: true,
    });
  clearTimeout(window.detailsControlDebounce);
  if (debounceMs > 0) {
    window.detailsControlDebounce = setTimeout(run, debounceMs);
  } else {
    run();
  }
}

// Client-only TV scope-chip handler. Switches the TV accordion between
// "All results" (Season → Episode), "Season packs", and "Complete series"
// without a backend reload — `applyLocalReleaseSort` re-renders the TV branch
// from the cached `currentDetailsData`. No backend contract change.
function setDetailsScope(requestId, scope) {
  const controls = getDetailsControls(requestId);
  controls.scope = scope;
  applyLocalReleaseSort();
}

function ensureDetailsControlHandlers() {
  if (window.detailsControlHandlersReady) return;
  window.detailsControlHandlersReady = true;
  const filterInput = document.getElementById('release-filter-input');
  const resolutionSelect = document.getElementById('release-resolution-filter');
  const sortSelect = document.getElementById('release-sort-key');
  const directionBtn = document.getElementById('release-sort-direction');
  const resetBtn = document.getElementById('release-controls-reset');
  if (filterInput)
    filterInput.addEventListener('input', () => {
      const controls = getDetailsControls(window.currentRequestId);
      controls.title = filterInput.value.trim();
      reloadDetailsWithControls(300);
    });
  if (resolutionSelect)
    resolutionSelect.addEventListener('change', () => {
      const controls = getDetailsControls(window.currentRequestId);
      controls.resolution = resolutionSelect.value;
      reloadDetailsWithControls();
    });
  if (sortSelect)
    sortSelect.addEventListener('change', () => {
      const controls = getDetailsControls(window.currentRequestId);
      controls.sort = sortSelect.value;
      applyLocalReleaseSort();
    });
  if (directionBtn)
    directionBtn.addEventListener('click', () => {
      const controls = getDetailsControls(window.currentRequestId);
      controls.direction = (controls.direction || 'desc') === 'desc' ? 'asc' : 'desc';
      applyDetailsControls(controls);
      applyLocalReleaseSort();
    });
  if (resetBtn)
    resetBtn.addEventListener('click', () => {
      resetDetailsControls(window.currentRequestId, { updateInputs: true });
      reloadDetailsWithControls();
    });
}

async function openRequestDetails(requestId, explicitIndex = null, options = {}) {
  if (window.activeDetailsRequestId && window.activeDetailsRequestId !== requestId) {
    cancelLiveDetailsRefresh(window.activeDetailsRequestId);
  }
  window.activeDetailsRequestId = requestId;
  const loadToken = (window.detailsLoadToken || 0) + 1;
  window.detailsLoadToken = loadToken;
  const preserveUiState = !!options.preserveUiState;
  const focusTvScope = options.focusTvScope || null;
  const modal = document.getElementById('request-details-modal');
  const title = document.getElementById('request-details-title');
  const meta = document.getElementById('request-details-meta');
  const overview = document.getElementById('request-details-overview');
  const releases = document.getElementById('request-details-releases');
  const overseerrLink = document.getElementById('request-details-overseerr-link');
  const refreshPlexBtn = document.getElementById('request-details-refresh-plex');
  const searchBtn = document.getElementById('request-details-search-btn');
  const tvSearchBtn = document.getElementById('request-details-tv-search-btn');
  const tvFullSearchBtn = document.getElementById('request-details-tv-full-search-btn');
  window.ensureDetailsControlHandlers();

  // Build navigation context from currently visible rows
  if (explicitIndex !== null) {
    window.currentDetailsIndex = explicitIndex;
  } else {
    window.visibleRequests = window.getVisibleRequests();
    window.currentDetailsIndex = window.visibleRequests.findIndex((r) => r.id === requestId);
    if (window.currentDetailsIndex === -1) {
      window.currentDetailsIndex = 0;
    }
  }
  window.updateNavigationButtons();

  const timelineWasOpen = preserveUiState ? !!document.getElementById('request-details-timeline')?.open : false;
  const preservedDetailsState =
    preserveUiState && window.captureDetailsAccordionState ? window.captureDetailsAccordionState() : null;

  if (!preserveUiState) {
    title.textContent = 'Loading...';
    meta.textContent = '';
    overview.textContent = '';
    const mobileOverview = document.querySelector('[data-mobile-overview]');
    if (mobileOverview) mobileOverview.textContent = '';
    const statsEl = document.getElementById('request-details-stats');
    if (statsEl) statsEl.innerHTML = '';
  }
  if (overseerrLink) {
    overseerrLink.classList.add('hidden');
    overseerrLink.removeAttribute('href');
  }
  if (refreshPlexBtn) {
    refreshPlexBtn.classList.add('hidden');
  }
  if (searchBtn) {
    searchBtn.classList.add('hidden');
  }
  if (tvSearchBtn) {
    tvSearchBtn.classList.add('hidden');
  }
  if (tvFullSearchBtn) {
    tvFullSearchBtn.classList.add('hidden');
  }
  window.currentTvSeasons = [];
  window.setPoster(null, 'Loading poster');
  // #release-results-header and #release-controls are hidden on mobile by
  // default (the mobile filter chip toggles #release-controls) and shown on
  // desktop via lg:flex. Avoid removing `hidden` here — that would surface
  // them on <lg. Desktop visibility is handled by the lg:flex classes.
  document.getElementById('release-controls').classList.add('hidden');
  // Reset the mobile filter chip to collapsed whenever the modal (re)opens so
  // the <lg view starts with the controls hidden behind the single chip.
  const mobileFilterChip = document.querySelector('[onclick="toggleMobileFilter()"]');
  if (mobileFilterChip) {
    mobileFilterChip.setAttribute('aria-expanded', 'false');
  }
  if (!preserveUiState) {
    releases.innerHTML = '<div class="text-gray-500 text-sm">Loading search results...</div>';
  }
  const cacheIndicatorInit = document.getElementById('release-cache-indicator');
  if (cacheIndicatorInit) cacheIndicatorInit.classList.add('hidden');
  if (!preserveUiState) {
    window.resetDetailsControls(requestId, { updateInputs: true });
    delete window.detailsAutoSearchStarted[requestId];
  }
  // The Activity overlay always starts closed on a fresh modal open. Open
  // state does not persist across reopens, but prev/next navigation reuses an
  // already-visible modal and should not close a panel the user just opened.
  if (modal.classList.contains('hidden') && window.closeActivityPanel) {
    window.closeActivityPanel();
  }
  modal.classList.remove('hidden');

  try {
    const response = await fetch(window.buildDetailsUrl(requestId));
    if (!response.ok) {
      throw new Error(`Server error: ${response.status}`);
    }
    const data = await response.json();
    // A navigation or a newer live-refresh request won the race.
    if (window.detailsLoadToken !== loadToken) return;

    title.textContent = data.request.title;
    // Mobile subtitle in the header card: a compact "year · type · status"
    // line. The details API only exposes type + status today (year/age/by
    // are absent), so only non-empty segments are joined to avoid stray
    // separators.
    const subtitleSegments = [
      data.request.year ? window.escapeHtml(String(data.request.year)) : '',
      data.request.media_type ? window.escapeHtml(String(data.request.media_type)) : '',
      data.request.status ? window.escapeHtml(String(data.request.status).replace(/_/g, ' ')) : '',
    ].filter(Boolean);
    meta.textContent = subtitleSegments.join(' · ');
    overview.textContent = data.overseerr?.overview || 'No synopsis available.';
    // Mirror the synopsis into the mobile synopsis <details> body so the
    // collapsed rail synopsis (desktop-only) and the mobile synopsis stay
    // in sync without duplicating the #request-details-overview ID.
    const mobileOverview = document.querySelector('[data-mobile-overview]');
    if (mobileOverview) mobileOverview.textContent = overview.textContent;
    const metaRow = document.getElementById('request-details-meta-row');
    if (metaRow) {
      const items = [];
      if (data.request.year)
        items.push(`<span class="badge badge-gray">Year ${window.escapeHtml(String(data.request.year))}</span>`);
      if (data.request.media_type)
        items.push(
          `<span class="badge badge-gray">${window.escapeHtml(String(data.request.media_type).toUpperCase())}</span>`,
        );
      if (data.request.status)
        items.push(
          `<span class="badge badge-gray">${window.escapeHtml(String(data.request.status).replace(/_/g, ' '))}</span>`,
        );
      metaRow.innerHTML = items.join('');
    }
    const releaseDateEl = document.getElementById('request-details-release-date');
    if (releaseDateEl) {
      const releaseDate = data.overseerr?.release_date;
      if (releaseDate) {
        releaseDateEl.textContent = 'Release: ' + releaseDate;
        releaseDateEl.classList.remove('hidden');
      } else {
        releaseDateEl.classList.add('hidden');
      }
    }
    window.setPoster(data.overseerr?.poster, data.request.title);
    if (overseerrLink && data.overseerr?.url) {
      overseerrLink.href = data.overseerr.url;
      overseerrLink.classList.remove('hidden');
    }

    if (data.request.media_type === 'tv' && refreshPlexBtn) {
      refreshPlexBtn.classList.remove('hidden');
    }

    if (data.request.media_type === 'movie' && searchBtn) {
      searchBtn.classList.remove('hidden');
    }

    if (data.request.media_type === 'tv' && tvSearchBtn) {
      tvSearchBtn.classList.remove('hidden');
    }
    if (data.request.media_type === 'tv' && tvFullSearchBtn) {
      tvFullSearchBtn.classList.remove('hidden');
    }

    window.currentReleases = data.releases || [];
    window.currentDetailsData = data;
    window.currentRequestId = data.request.id;
    window.currentRequestMediaType = data.request.media_type || 'movie';
    window.loadSearchHistory();

    const cacheIndicator = document.getElementById('release-cache-indicator');
    const cacheIndicatorText = document.getElementById('release-cache-indicator-text');

    if (data.request.media_type === 'tv' && data.tv_info) {
      window.currentTvSeasons = data.tv_info.seasons || [];
      if (cacheIndicator) cacheIndicator.classList.add('hidden');
      releases.innerHTML = window.renderSeasonAccordion(data);
      if (preservedDetailsState && window.restoreDetailsAccordionState) {
        window.restoreDetailsAccordionState(preservedDetailsState);
      }
      if (focusTvScope && window.focusStagedTvScope) {
        window.focusStagedTvScope(requestId, focusTvScope);
      }
      if (window.updateTvAccordionControls) window.updateTvAccordionControls();
    } else {
      if (window.currentReleases.length > 0) {
        releases.innerHTML =
          '<ul class="divide-y divide-gray-700/40">' +
          window.currentReleases.map((release) => window.renderReleaseCard(release, window.currentRequestId)).join('') +
          '</ul>';
        if (cacheIndicator && cacheIndicatorText) {
          cacheIndicatorText.textContent = 'Showing cached results';
          cacheIndicator.classList.remove('hidden');
        }
      } else {
        if (cacheIndicator) cacheIndicator.classList.add('hidden');
        releases.innerHTML =
          '<div class="text-gray-500 text-sm">No cached search results yet. Use Refresh Search to search indexers.</div>';
      }
    }
    window.applyDetailsControls(data.release_controls || {});
    window.updateReleaseCountText(data);

    window.currentRequestTimeline = data.timeline || [];
    renderTimeline(window.currentRequestTimeline, { open: timelineWasOpen });
    renderDetailsStats(data);

    if (data.auto_search_eligible && !window.detailsAutoSearchStarted[requestId]) {
      window.detailsAutoSearchStarted[requestId] = true;
      if (data.request.media_type === 'tv') {
        // The season accordion is useful before the first result arrives:
        // it exposes episode names, air dates, and status while the SSE
        // progress toast reports the search state. Do not replace it.
        window.searchTvRequestNew({ auto: true });
      } else {
        releases.innerHTML = window.renderMovieSearchLoadingState();
        window.searchRequestFromDetails({ auto: true });
      }
    }
  } catch (err) {
    if (window.detailsLoadToken !== loadToken) return;
    title.textContent = 'Error loading details';
    meta.textContent = err.message || 'Unknown error';
    overview.textContent = '';
    window.setPoster(null, 'Poster unavailable');
    releases.innerHTML =
      '<div class="text-red-400 text-sm">Failed to load request details. Check that Overseerr is reachable.</div>';
  }
}

window.scheduleLiveDetailsRefresh = scheduleLiveDetailsRefresh;
window.cancelLiveDetailsRefresh = cancelLiveDetailsRefresh;
window.reloadOpenDetailsIfActive = reloadOpenDetailsIfActive;

// Render the desktop left-rail stat list (`#request-details-stats`):
// Cached results / Passed / Rejected / Staged / Last search. Desktop-only
// (the element is `hidden lg:block`); the mobile header card omits stats.
// Field names come from `serialize_request_details_response`:
//   - data.total_releases          -> Cached results
//   - data.releases[].passed       -> Passed (truthy) / Rejected (falsy)
//   - data.active_staged_torrents  -> Staged count (plural list length when
//                                     present; falls back to the singular
//                                     `active_staged_torrent` flag)
//   - data.timeline[].created_at   -> latest entry timestamp -> "Last search"
function renderDetailsStats(data) {
  const statsEl = document.getElementById('request-details-stats');
  if (!statsEl) return;
  const releases = Array.isArray(data && data.releases) ? data.releases : [];
  const passed = releases.filter((r) => r && r.passed).length;
  const rejected = releases.filter((r) => r && !r.passed).length;
  const stagedCount =
    Array.isArray(data.active_staged_torrents) && data.active_staged_torrents.length
      ? data.active_staged_torrents.length
      : data.active_staged_torrent
        ? 1
        : 0;
  const cachedResults = data.total_releases != null ? data.total_releases : releases.length;
  const lastSearch = formatDetailsLastSearch(data && data.timeline);
  statsEl.innerHTML = [
    detailsStatRow('Cached results', 'text-gray-300 tabular-nums', String(cachedResults)),
    detailsStatRow('Passed', 'text-emerald-400 tabular-nums', String(passed)),
    detailsStatRow('Rejected', 'text-red-400 tabular-nums', String(rejected)),
    detailsStatRow('Staged', 'text-cyan-300 tabular-nums', String(stagedCount)),
    detailsStatRow('Last search', 'text-gray-300', lastSearch),
  ].join('');
}

function detailsStatRow(label, ddClass, value) {
  return `<div class="flex justify-between gap-3"><dt class="text-gray-500">${window.escapeHtml(label)}</dt><dd class="${ddClass}">${window.escapeHtml(value)}</dd></div>`;
}

// Derive a relative "Last search" string from the latest timeline entry's
// `created_at` timestamp. Reuses the repo's `formatRelativePublishAge` helper
// (exported from releases.js) when present; falls back to "—" when there is no
// timeline, no parseable timestamp, or the formatter yields an empty string.
function formatDetailsLastSearch(timeline) {
  if (!Array.isArray(timeline) || timeline.length === 0) return '—';
  let latestMs = null;
  let latestCreatedAt = null;
  for (const entry of timeline) {
    if (!entry || !entry.created_at) continue;
    const ts = new Date(entry.created_at);
    if (Number.isNaN(ts.getTime())) continue;
    if (latestMs === null || ts.getTime() > latestMs) {
      latestMs = ts.getTime();
      latestCreatedAt = entry.created_at;
    }
  }
  if (latestCreatedAt === null) return '—';
  const relative = window.formatRelativePublishAge ? window.formatRelativePublishAge(latestCreatedAt) : '';
  return relative || '—';
}

function renderRuleEvidence(evidence) {
  const matches = (evidence && evidence.matches) || [];
  if (!matches.length) return '<span class="text-gray-500">No rule details</span>';
  return matches
    .slice(0, 8)
    .map((m) => {
      const ok = m.matched ? 'badge-green' : 'badge-red';
      const delta = Number(m.score_delta || 0);
      return `<span class="badge ${ok}" title="${window.escapeHtml(m.rule_type || m.effect || '')}">${window.escapeHtml(m.rule_name || 'Rule')} ${delta ? `(${delta > 0 ? '+' : ''}${delta})` : ''}</span>`;
    })
    .join(' ');
}

async function loadSearchHistory() {
  if (!window.currentRequestId) return;
  const container = document.getElementById('request-details-search-history');
  if (!container) return;
  container.innerHTML = '<div class="text-gray-500">Loading search history...</div>';
  let runsCount = 0;
  try {
    const response = await fetch(`/requests/${window.currentRequestId}/search-history?limit=5`);
    if (!response.ok) throw new Error(`Server error: ${response.status}`);
    const data = await response.json();
    const runs = data.runs || [];
    runsCount = runs.length;
    if (!runs.length) {
      container.innerHTML = '<div class="text-gray-500">No search history yet.</div>';
      updateActivityCount(null, runsCount);
      return;
    }
    container.innerHTML = runs
      .map((run) => {
        const counts = run.counts || {};
        const candidates = (run.candidates || [])
          .slice(0, 5)
          .map(
            (c) =>
              `<details class="mt-2 rounded-lg border border-gray-700/50 bg-surface-900/60 p-2"><summary class="cursor-pointer text-gray-200">${window.escapeHtml(c.title || 'Candidate')} <span class="text-emerald-400">Score ${window.escapeHtml(String(c.score ?? 0))}</span> <span class="text-gray-500">${window.escapeHtml(c.status || '')}</span></summary><div class="mt-2 flex flex-wrap gap-1">${renderRuleEvidence(c.rule_evidence || {})}</div>${c.rejection_reason ? `<div class="mt-2 text-red-300">${window.escapeHtml(c.rejection_reason)}</div>` : ''}</details>`,
          )
          .join('');
        return `<div class="mb-3 rounded-lg border border-gray-700/60 bg-surface-900/70 p-3"><div class="flex flex-wrap items-center gap-2"><span class="badge badge-gray">${window.escapeHtml(run.status || 'unknown')}</span><span class="badge badge-gray">${window.escapeHtml(run.outcome || '')}</span><span>${window.escapeHtml(run.started_at || '')}</span><span>${window.escapeHtml(run.source || '')}</span><span>${window.escapeHtml(run.search_mode || '')}</span></div><div class="mt-2 text-xs text-gray-400">Total ${counts.total ?? 0} · Passed ${counts.passed ?? 0} · Rejected ${counts.rejected ?? 0} · Staged ${counts.staged ?? 0} · Sent ${counts.sent ?? 0}</div>${run.error ? `<div class="mt-2 text-red-300">${window.escapeHtml(run.error)}</div>` : ''}${candidates}</div>`;
      })
      .join('');
  } catch (err) {
    container.innerHTML = `<div class="text-red-400">Failed to load search history: ${window.escapeHtml(err.message || 'Unknown error')}</div>`;
  }
  updateActivityCount(null, runsCount);
}

// Update the "Activity · N" count shown on the modal-header toggle button.
// `renderTimeline` and `loadSearchHistory` each contribute their segment count;
// the running total is cached on the dataset of #activity-count so
// independent loaders can collaborate without one clobbering the other.
function updateActivityCount(timelineDelta, runsDelta) {
  const el = document.getElementById('activity-count');
  if (!el) return;
  if (timelineDelta != null) el.dataset.timeline = String(timelineDelta);
  if (runsDelta != null) el.dataset.runs = String(runsDelta);
  const timeline = Number(el.dataset.timeline || 0);
  const runs = Number(el.dataset.runs || 0);
  el.textContent = String(timeline + runs);
}

window.loadSearchHistory = loadSearchHistory;
window.renderRuleEvidence = renderRuleEvidence;
window.renderDetailsStats = renderDetailsStats;

function countUnavailableTvSeasons(data = window.currentDetailsData) {
  const seasons = data?.tv_info?.seasons || [];
  return seasons.filter(function (season) {
    return (season.episodes || []).some(function (episode) {
      return !window.isEpisodeComplete(episode);
    });
  }).length;
}

async function confirmLargeTvSearch(requestId = window.currentRequestId) {
  let data = window.currentDetailsData;
  if (!data || data.request?.id !== requestId) {
    const response = await fetch(window.buildDetailsUrl(requestId));
    if (!response.ok) throw new Error('Could not inspect TV seasons: HTTP ' + response.status);
    data = await response.json();
  }
  const unavailableSeasonCount = countUnavailableTvSeasons(data);
  if (unavailableSeasonCount < 5) return true;
  return window.confirm(
    unavailableSeasonCount +
      ' seasons still have unavailable episodes. ' +
      'This whole-show search may take a while. Continue?\n\n' +
      'You can cancel after it starts or use the per-season and per-episode searches instead.',
  );
}

async function searchTvRequest(mode = 'new', _options = {}) {
  if (!window.currentRequestId) return;
  const requestId = window.currentRequestId;
  if (!(await confirmLargeTvSearch(requestId))) return;
  const fullSearch = mode === 'full';
  const btn = document.getElementById(
    fullSearch ? 'request-details-tv-full-search-btn' : 'request-details-tv-search-btn',
  );
  const otherBtn = document.getElementById(
    fullSearch ? 'request-details-tv-search-btn' : 'request-details-tv-full-search-btn',
  );
  const originalText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Searching...';
  }
  if (otherBtn) otherBtn.disabled = true;
  const modeLabel = fullSearch ? 'Full search' : 'Search for new';
  const fallbackLabel = fullSearch ? 'Full search' : 'Search for new';
  const detailsTitle = document.getElementById('request-details-title')?.textContent?.trim() || modeLabel;
  const streamUrl =
    '/requests/' + requestId + '/search/stream?search_mode=' + encodeURIComponent(fullSearch ? 'full' : 'new');
  window.startTvSearchProgress(
    streamUrl,
    modeLabel + ': ' + detailsTitle,
    async function (data) {
      if (!data || data.reload_details !== false) {
        if (window.activeDetailsRequestId === requestId) {
          await reloadOpenDetailsIfActive(requestId);
        }
      }
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = originalText || fallbackLabel;
      }
      if (otherBtn) otherBtn.disabled = false;
    },
    function () {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = originalText || fallbackLabel;
      }
      if (otherBtn) otherBtn.disabled = false;
    },
    '/requests/' + requestId + '/search/cancel',
  );
}

async function searchTvRequestNew(options = {}) {
  return searchTvRequest('new', options);
}

async function searchTvRequestFull(options = {}) {
  return searchTvRequest('full', options);
}

async function searchTvRequestAll() {
  return searchTvRequestNew();
}

async function refreshPlexAndReload() {
  if (!window.currentRequestId) return;
  const btn = document.getElementById('request-details-refresh-plex');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
  }
  try {
    const response = await fetch('/requests/' + window.currentRequestId + '/refresh-plex', { method: 'POST' });
    if (!response.ok) {
      throw new Error('Server error: ' + response.status);
    }
    await openRequestDetails(window.currentRequestId, window.currentDetailsIndex);
  } catch (err) {
    console.error('Plex refresh failed:', err);
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Refresh Plex';
    }
  }
}

async function searchRequestFromDetails() {
  if (!window.currentRequestId) return;
  const btn = document.getElementById('request-details-search-btn');
  const originalText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Searching...';
  }
  const detailsTitle = document.getElementById('request-details-title')?.textContent?.trim();
  const releasesContainer = document.getElementById('request-details-releases');
  if (releasesContainer) {
    releasesContainer.innerHTML = window.renderMovieSearchLoadingState();
  }
  const cacheInd = document.getElementById('release-cache-indicator');
  if (cacheInd) {
    cacheInd.classList.add('hidden');
  }
  window.startSearchProgress(window.currentRequestId, detailsTitle, async function () {
    await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true });
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalText || 'Refresh Search';
    }
  });
}

function renderTimeline(timelineData, options = {}) {
  const container = document.getElementById('request-details-timeline');
  const entries = document.getElementById('timeline-entries');
  const count = document.getElementById('timeline-count');
  if (!container || !entries) return;
  const timelineLength = Array.isArray(timelineData) ? timelineData.length : 0;
  if (!timelineData || timelineLength === 0) {
    container.classList.add('hidden');
    container.open = false;
    if (count) count.textContent = '';
    updateActivityCount(timelineLength);
    return;
  }
  container.classList.remove('hidden');
  container.open = !!options.open;
  if (count) count.textContent = timelineLength + ' event' + (timelineLength === 1 ? '' : 's');
  updateActivityCount(timelineLength);
  const colorMap = {
    search_started: 'bg-blue-500',
    search_completed: 'bg-blue-500',
    rule_evaluation: 'bg-yellow-500',
    release_staged: 'bg-orange-500',
    release_approved: 'bg-orange-500',
    download_started: 'bg-purple-500',
    download_completed: 'bg-purple-500',
    plex_available: 'bg-green-500',
    episode_marked_available: 'bg-green-500',
    error: 'bg-red-500',
    request_status_changed: 'bg-gray-500',
  };
  const labelMap = {
    search_started: 'Search started',
    search_completed: 'Search completed',
    rule_evaluation: 'Rules evaluated',
    release_staged: 'Release staged',
    release_approved: 'Release approved',
    download_started: 'Download started',
    download_completed: 'Download completed',
    plex_available: 'Available on Plex',
    episode_marked_available: 'Episode marked available',
    error: 'Error',
    request_status_changed: 'Status changed',
  };
  function formatTimelineTimestamp(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const sameYear = date.getFullYear() === now.getFullYear();
    return date.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      ...(sameYear ? {} : { year: 'numeric' }),
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function timelineDetail(entry) {
    if (!entry.details) return '';
    const d = entry.details;
    if (entry.event_type === 'request_status_changed' && d.old_status && d.new_status) {
      return `${d.old_status} → ${d.new_status}`;
    }
    if (entry.event_type === 'search_started') {
      return '';
    }
    if (entry.event_type === 'search_completed') {
      if (d.result_count !== undefined) return `${d.result_count} result${Number(d.result_count) === 1 ? '' : 's'}`;
      if (typeof d.message === 'string') {
        if (d.message.includes('No releases found')) return 'No releases found';
        return d.message;
      }
      return '';
    }
    if (entry.event_type === 'release_staged' && d.title) return d.title;
    if (entry.event_type === 'error' && d.error) return d.error;
    if (entry.event_type === 'episode_marked_available' && d.episode) {
      return `S${String(d.season || '?').padStart(2, '0')}E${String(d.episode).padStart(2, '0')}`;
    }
    if (entry.event_type === 'rule_evaluation') {
      const parts = [];
      if (d.passed !== undefined) parts.push(`${d.passed} passed`);
      if (d.failed !== undefined) parts.push(`${d.failed} failed`);
      if (d.title) parts.push(d.title);
      return parts.join(', ');
    }
    return '';
  }

  entries.innerHTML = timelineData
    .map((entry) => {
      const dot = colorMap[entry.event_type] || 'bg-gray-500';
      const label = labelMap[entry.event_type] || entry.event_type.replace(/_/g, ' ');
      const detail = timelineDetail(entry);
      const ts = formatTimelineTimestamp(entry.created_at);
      const safeLabel = window.escapeHtml(label);
      const safeTs = window.escapeHtml(ts);
      const safeDetail = detail ? window.escapeHtml(String(detail)) : '';
      return `<div class="relative flex items-start gap-3 -ml-[1.15rem]">
            <div class="w-2.5 h-2.5 rounded-full ${dot} mt-1.5 shrink-0 ring-2 ring-surface-900"></div>
            <div class="min-w-0 flex-1">
                <div class="flex items-baseline gap-2 flex-wrap">
                    <span class="text-sm font-medium text-gray-200">${safeLabel}</span>
                    <span class="text-xs text-gray-500">${safeTs}</span>
                </div>
                ${safeDetail ? `<p class="text-xs text-gray-400 mt-0.5 break-words">${safeDetail}</p>` : ''}
            </div>
        </div>`;
    })
    .join('');
}

// Activity panel: an overlay popup pinned to the right third of the details
// modal body (full width <lg), opened from the "Activity · N" button in the
// modal header. It is closed on every breakpoint by default and on every fresh
// modal open; `#activity-backdrop` dims the rest of the body and closes the
// panel on click.
function isActivityPanelOpen() {
  const panel = document.getElementById('activity-panel');
  return !!panel && !panel.classList.contains('hidden');
}

function setActivityPanelOpen(open) {
  const panel = document.getElementById('activity-panel');
  const backdrop = document.getElementById('activity-backdrop');
  const toggle = document.getElementById('activity-toggle');
  if (!panel) return;
  const wasOpen = !panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !open);
  if (backdrop) backdrop.classList.toggle('hidden', !open);
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  // Move focus into the overlay on open and hand it back to the header
  // trigger on close, so keyboard users are not left on hidden content.
  if (open && !wasOpen) {
    panel.querySelector('button')?.focus();
  } else if (!open && wasOpen && toggle) {
    toggle.focus();
  }
}

function openActivityPanel() {
  setActivityPanelOpen(true);
}

function closeActivityPanel() {
  setActivityPanelOpen(false);
}

function toggleActivityPanel() {
  setActivityPanelOpen(!isActivityPanelOpen());
}

window.isActivityPanelOpen = isActivityPanelOpen;
window.openActivityPanel = openActivityPanel;
window.closeActivityPanel = closeActivityPanel;
window.toggleActivityPanel = toggleActivityPanel;

// Mobile filter chip toggle: expands/collapses #release-controls on <lg.
// #release-controls is `hidden lg:flex` in the template, so toggling `hidden`
// only affects mobile — on desktop `lg:flex` overrides `hidden` regardless.
function toggleMobileFilter() {
  const controls = document.getElementById('release-controls');
  if (!controls) return;
  const chip = document.querySelector('[onclick="toggleMobileFilter()"]');
  const willShow = controls.classList.contains('hidden');
  controls.classList.toggle('hidden', !willShow);
  if (chip) chip.setAttribute('aria-expanded', willShow ? 'true' : 'false');
}
window.toggleMobileFilter = toggleMobileFilter;

// Export functions to window for HTML onclick handlers
window.openRequestDetails = openRequestDetails;
window.ensureDetailsControlHandlers = ensureDetailsControlHandlers;
window.resetDetailsControls = resetDetailsControls;
window.buildDetailsUrl = buildDetailsUrl;
window.applyDetailsControls = applyDetailsControls;
window.updateReleaseCountText = updateReleaseCountText;
window.applyLocalReleaseSort = applyLocalReleaseSort;
window.setDetailsScope = setDetailsScope;
window.refreshPlexAndReload = refreshPlexAndReload;
window.searchRequestFromDetails = searchRequestFromDetails;
window.searchTvRequest = searchTvRequest;
window.searchTvRequestNew = searchTvRequestNew;
window.searchTvRequestFull = searchTvRequestFull;
window.confirmLargeTvSearch = confirmLargeTvSearch;
window.searchTvRequestAll = searchTvRequestAll;
