// Dashboard Details Module - Request details modal and timeline
// =============================================================

window.detailsControlState = window.detailsControlState || {};
window.detailsControlHandlersReady = false;
window.detailsControlDebounce = null;
window.detailsAutoSearchStarted = window.detailsAutoSearchStarted || {};

function defaultDetailsControls() {
    return { title: '', resolution: 'all', sort: 'score', direction: 'desc' };
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
    if (resolutionSelect) resolutionSelect.value = controls.resolution === '2160p' ? '4k' : (controls.resolution || 'all');
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
        releasesEl.innerHTML = window.currentReleases.map(release => window.renderReleaseCard(release, window.currentRequestId)).join('');
        if (window.currentDetailsData) window.currentDetailsData.releases = window.currentReleases;
        return true;
    }
    return false;
}

function reloadDetailsWithControls(debounceMs = 0) {
    if (!window.currentRequestId) return;
    const run = () => window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, {
        preserveUiState: true,
    });
    clearTimeout(window.detailsControlDebounce);
    if (debounceMs > 0) {
        window.detailsControlDebounce = setTimeout(run, debounceMs);
    } else {
        run();
    }
}

function ensureDetailsControlHandlers() {
    if (window.detailsControlHandlersReady) return;
    window.detailsControlHandlersReady = true;
    const filterInput = document.getElementById('release-filter-input');
    const resolutionSelect = document.getElementById('release-resolution-filter');
    const sortSelect = document.getElementById('release-sort-key');
    const directionBtn = document.getElementById('release-sort-direction');
    const resetBtn = document.getElementById('release-controls-reset');
    if (filterInput) filterInput.addEventListener('input', () => {
        const controls = getDetailsControls(window.currentRequestId);
        controls.title = filterInput.value.trim();
        reloadDetailsWithControls(300);
    });
    if (resolutionSelect) resolutionSelect.addEventListener('change', () => {
        const controls = getDetailsControls(window.currentRequestId);
        controls.resolution = resolutionSelect.value;
        reloadDetailsWithControls();
    });
    if (sortSelect) sortSelect.addEventListener('change', () => {
        const controls = getDetailsControls(window.currentRequestId);
        controls.sort = sortSelect.value;
        applyLocalReleaseSort();
    });
    if (directionBtn) directionBtn.addEventListener('click', () => {
        const controls = getDetailsControls(window.currentRequestId);
        controls.direction = (controls.direction || 'desc') === 'desc' ? 'asc' : 'desc';
        applyDetailsControls(controls);
        applyLocalReleaseSort();
    });
    if (resetBtn) resetBtn.addEventListener('click', () => {
        resetDetailsControls(window.currentRequestId, { updateInputs: true });
        reloadDetailsWithControls();
    });
}

async function openRequestDetails(requestId, explicitIndex = null, options = {}) {
    const preserveUiState = !!options.preserveUiState;
    const modal = document.getElementById('request-details-modal');
    const title = document.getElementById('request-details-title');
    const meta = document.getElementById('request-details-meta');
    const overview = document.getElementById('request-details-overview');
    const releases = document.getElementById('request-details-releases');
    const overseerrLink = document.getElementById('request-details-overseerr-link');
    const refreshPlexBtn = document.getElementById('request-details-refresh-plex');
    const searchBtn = document.getElementById('request-details-search-btn');
    const tvSearchBtn = document.getElementById('request-details-tv-search-btn');
    const filterInput = document.getElementById('release-filter-input');
    window.ensureDetailsControlHandlers();

    // Build navigation context from currently visible rows
    if (explicitIndex !== null) {
        window.currentDetailsIndex = explicitIndex;
    } else {
        window.visibleRequests = window.getVisibleRequests();
        window.currentDetailsIndex = window.visibleRequests.findIndex(r => r.id === requestId);
        if (window.currentDetailsIndex === -1) {
            window.currentDetailsIndex = 0;
        }
    }
    window.updateNavigationButtons();

    const preservedDetailsState = preserveUiState && window.captureDetailsAccordionState
        ? window.captureDetailsAccordionState()
        : null;

    if (!preserveUiState) {
        title.textContent = 'Loading...';
        meta.textContent = '';
        overview.textContent = '';
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
    window.currentTvSeasons = [];
    window.updateActiveStageBanner({ active_staged_torrent: null });
    window.setPoster(null, 'Loading poster');
    document.getElementById('release-results-header').classList.remove('hidden');
    document.getElementById('release-controls').classList.remove('hidden');
    if (!preserveUiState) {
        releases.innerHTML = '<div class="text-gray-500 text-sm">Loading search results...</div>';
    }
    const cacheIndicatorInit = document.getElementById('release-cache-indicator');
    if (cacheIndicatorInit) cacheIndicatorInit.classList.add('hidden');
    if (!preserveUiState) {
        window.resetDetailsControls(requestId, { updateInputs: true });
        delete window.detailsAutoSearchStarted[requestId];
    }
    modal.classList.remove('hidden');

    try {
        const response = await fetch(window.buildDetailsUrl(requestId));
        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }
        const data = await response.json();

        title.textContent = data.request.title;
        meta.textContent = data.request.title;
        overview.textContent = data.overseerr?.overview || 'No synopsis available.';
        const metaRow = document.getElementById('request-details-meta-row');
        if (metaRow) {
            const items = [];
            if (data.request.year) items.push(`<span class="badge badge-gray">Year ${window.escapeHtml(String(data.request.year))}</span>`);
            if (data.request.media_type) items.push(`<span class="badge badge-gray">${window.escapeHtml(String(data.request.media_type).toUpperCase())}</span>`);
            if (data.request.status) items.push(`<span class="badge badge-gray">${window.escapeHtml(String(data.request.status).replace(/_/g, ' '))}</span>`);
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

        window.currentReleases = data.releases || [];
        window.currentDetailsData = data;
        window.currentRequestId = data.request.id;
        window.currentRequestMediaType = data.request.media_type || 'movie';
        window.updateActiveStageBanner(data);

        const cacheIndicator = document.getElementById('release-cache-indicator');
        const cacheIndicatorText = document.getElementById('release-cache-indicator-text');

        if (data.request.media_type === 'tv' && data.tv_info) {
            window.currentTvSeasons = data.tv_info.seasons || [];
            document.getElementById('release-results-header').classList.remove('hidden');
            document.getElementById('release-controls').classList.remove('hidden');
            if (cacheIndicator) cacheIndicator.classList.add('hidden');
            releases.innerHTML = window.renderSeasonAccordion(data);
            if (preservedDetailsState && window.restoreDetailsAccordionState) {
                window.restoreDetailsAccordionState(preservedDetailsState);
            }
            if (window.updateTvAccordionControls) window.updateTvAccordionControls();
        } else {
            document.getElementById('release-results-header').classList.remove('hidden');
            document.getElementById('release-controls').classList.remove('hidden');
            if (window.currentReleases.length > 0) {
                releases.innerHTML = window.currentReleases.map(release => window.renderReleaseCard(release, window.currentRequestId)).join('');
                if (cacheIndicator && cacheIndicatorText) {
                    cacheIndicatorText.textContent = 'Showing cached results';
                    cacheIndicator.classList.remove('hidden');
                }
            } else {
                if (cacheIndicator) cacheIndicator.classList.add('hidden');
                releases.innerHTML = '<div class="text-gray-500 text-sm">No cached search results yet. Use Refresh Search to search indexers.</div>';
            }
        }
        window.applyDetailsControls(data.release_controls || {});
        window.updateReleaseCountText(data);

        window.currentRequestTimeline = data.timeline || [];
        renderTimeline(window.currentRequestTimeline);

        if (data.auto_search_eligible && !window.detailsAutoSearchStarted[requestId]) {
            window.detailsAutoSearchStarted[requestId] = true;
            if (data.request.media_type === 'tv') {
                releases.innerHTML = '<div class="text-gray-500 text-sm">Searching indexers for TV results...</div>';
                window.searchTvRequestAll({ auto: true });
            } else {
                releases.innerHTML = window.renderMovieSearchLoadingState();
                window.searchRequestFromDetails({ auto: true });
            }
        }
    } catch (err) {
        title.textContent = 'Error loading details';
        meta.textContent = err.message || 'Unknown error';
        overview.textContent = '';
        window.updateActiveStageBanner({ active_staged_torrent: null });
        window.setPoster(null, 'Poster unavailable');
        releases.innerHTML = '<div class="text-red-400 text-sm">Failed to load request details. Check that Overseerr is reachable.</div>';
    }
}

async function searchTvRequestAll() {
    if (!window.currentRequestId) return;
    const btn = document.getElementById('request-details-tv-search-btn');
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Searching...';
    }
    const detailsTitle = document.getElementById('request-details-title')?.textContent?.trim() || 'TV Search All';
    const streamUrl = '/requests/' + window.currentRequestId + '/search/stream';
    window.startTvSearchProgress(streamUrl, 'TV Search All: ' + detailsTitle, async function(data) {
        if (!data || data.reload_details !== false) {
            await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true });
        }
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText || 'Refresh Search';
        }
    }, function() {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText || 'Refresh Search';
        }
    });
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
    window.startSearchProgress(window.currentRequestId, detailsTitle, async function() {
        await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true });
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText || 'Refresh Search';
        }
    });
}

function renderTimeline(timelineData) {
    const container = document.getElementById('request-details-timeline');
    const entries = document.getElementById('timeline-entries');
    if (!timelineData || timelineData.length === 0) {
        container.classList.add('hidden');
        return;
    }
    container.classList.remove('hidden');
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
    entries.innerHTML = timelineData.map(entry => {
        const dot = colorMap[entry.event_type] || 'bg-gray-500';
        const label = labelMap[entry.event_type] || entry.event_type.replace(/_/g, ' ');
        let detail = '';
        if (entry.details) {
            const d = entry.details;
            if (entry.event_type === 'request_status_changed' && d.old_status && d.new_status) {
                detail = `${d.old_status} → ${d.new_status}`;
                if (d.reason) detail += ` (${d.reason})`;
            } else if (entry.event_type === 'search_completed' && d.result_count !== undefined) {
                detail = `${d.result_count} results found`;
            } else if (entry.event_type === 'release_staged' && d.title) {
                detail = d.title;
            } else if (entry.event_type === 'error' && d.error) {
                detail = d.error;
            } else if (entry.event_type === 'episode_marked_available' && d.episode) {
                detail = `S${String(d.season || '?').padStart(2,'0')}E${String(d.episode).padStart(2,'0')}`;
            } else if (entry.event_type === 'rule_evaluation') {
                const parts = [];
                if (d.passed !== undefined) parts.push(`${d.passed} passed`);
                if (d.failed !== undefined) parts.push(`${d.failed} failed`);
                if (d.title) parts.push(d.title);
                detail = parts.join(', ');
            } else {
                const summary = Object.entries(d).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(', ');
                if (summary) detail = summary;
            }
        }
        const ts = entry.created_at ? new Date(entry.created_at).toLocaleString() : '';
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
    }).join('');
}

// Export functions to window for HTML onclick handlers
window.openRequestDetails = openRequestDetails;
window.ensureDetailsControlHandlers = ensureDetailsControlHandlers;
window.resetDetailsControls = resetDetailsControls;
window.buildDetailsUrl = buildDetailsUrl;
window.applyDetailsControls = applyDetailsControls;
window.updateReleaseCountText = updateReleaseCountText;
window.applyLocalReleaseSort = applyLocalReleaseSort;
window.refreshPlexAndReload = refreshPlexAndReload;
window.searchRequestFromDetails = searchRequestFromDetails;
window.searchTvRequestAll = searchTvRequestAll;
