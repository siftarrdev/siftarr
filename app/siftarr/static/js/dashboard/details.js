// Dashboard Details Module - Request details modal and timeline
// =============================================================

async function openRequestDetails(requestId, explicitIndex = null) {
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

    title.textContent = 'Loading...';
    meta.textContent = '';
    overview.textContent = '';
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
        window.closeTvSearchScopeMenu();
    }
    window.currentTvSeasons = [];
    window.updateActiveStageBanner({ active_staged_torrent: null });
    window.setPoster(null, 'Loading poster');
    document.getElementById('release-results-header').classList.remove('hidden');
    document.getElementById('release-filter-input').classList.remove('hidden');
    releases.innerHTML = '<div class="text-gray-500 text-sm">Loading search results...</div>';
    const cacheIndicatorInit = document.getElementById('release-cache-indicator');
    if (cacheIndicatorInit) cacheIndicatorInit.classList.add('hidden');
    if (filterInput) filterInput.value = '';
    modal.classList.remove('hidden');

    try {
        const response = await fetch(`/requests/${requestId}/details`);
        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }
        const data = await response.json();

        title.textContent = data.request.title;
        meta.textContent = `${data.request.media_type || 'unknown'} \u00B7 ${data.request.status || 'unknown'} \u00B7 ${data.overseerr?.status || 'unknown'} in Overseerr`;
        overview.textContent = data.overseerr?.overview || 'No synopsis available.';
        const metaRow = document.getElementById('request-details-meta-row');
        if (metaRow) {
            const items = [];
            if (data.request.year) items.push(`<span class="badge badge-gray">Year ${window.escapeHtml(String(data.request.year))}</span>`);
            if (data.request.media_type) items.push(`<span class="badge badge-gray">${window.escapeHtml(String(data.request.media_type).toUpperCase())}</span>`);
            if (data.request.status) items.push(`<span class="badge badge-gray">${window.escapeHtml(String(data.request.status).replace(/_/g, ' '))}</span>`);
            metaRow.innerHTML = items.join('');
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
        window.currentRequestId = data.request.id;
        window.updateActiveStageBanner(data);

        const cacheIndicator = document.getElementById('release-cache-indicator');
        const cacheIndicatorText = document.getElementById('release-cache-indicator-text');

        if (data.request.media_type === 'tv' && data.tv_info) {
            window.currentTvSeasons = data.tv_info.seasons || [];
            window.populateTvSearchScopeMenu();
            document.getElementById('release-results-header').classList.add('hidden');
            document.getElementById('release-filter-input').classList.add('hidden');
            if (cacheIndicator) cacheIndicator.classList.add('hidden');
            releases.innerHTML = window.renderSeasonAccordion(data);
        } else {
            document.getElementById('release-results-header').classList.remove('hidden');
            document.getElementById('release-filter-input').classList.remove('hidden');
            if (window.currentReleases.length > 0) {
                releases.innerHTML = window.currentReleases.map(release => window.renderReleaseCard(release, window.currentRequestId)).join('');
                if (cacheIndicator && cacheIndicatorText) {
                    cacheIndicatorText.textContent = 'Showing cached results';
                    cacheIndicator.classList.remove('hidden');
                }
            } else {
                releases.innerHTML = '<div class="text-gray-500 text-sm">No cached results – searching automatically...</div>';
                if (cacheIndicator) cacheIndicator.classList.add('hidden');
                searchRequestFromDetails();
            }
        }

        window.currentRequestTimeline = data.timeline || [];
        renderTimeline(window.currentRequestTimeline);
    } catch (err) {
        title.textContent = 'Error loading details';
        meta.textContent = err.message || 'Unknown error';
        overview.textContent = '';
        window.updateActiveStageBanner({ active_staged_torrent: null });
        window.setPoster(null, 'Poster unavailable');
        releases.innerHTML = '<div class="text-red-400 text-sm">Failed to load request details. Check that Overseerr is reachable.</div>';
    }
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
    const releasesContainer = document.getElementById('request-details-releases');
    try {
        const response = await fetch('/requests/' + window.currentRequestId + '/search', { method: 'POST' });
        if (!response.ok) {
            const errorData = await response.json().catch(() => null);
            throw new Error(errorData?.error || 'Server error: ' + response.status);
        }
        const data = await response.json();
        window.currentReleases = data.releases || [];
        if (releasesContainer) {
            releasesContainer.innerHTML = window.currentReleases.map(release => window.renderReleaseCard(release, window.currentRequestId)).join('') || '<div class="text-gray-500 text-sm">No search results found for this request.</div>';
        }
        const cacheInd = document.getElementById('release-cache-indicator');
        const cacheIndText = document.getElementById('release-cache-indicator-text');
        if (cacheInd && cacheIndText && window.currentReleases.length > 0) {
            cacheIndText.textContent = 'Fresh results';
            cacheInd.classList.remove('hidden');
        } else if (cacheInd) {
            cacheInd.classList.add('hidden');
        }
        renderTimeline(window.currentRequestTimeline);
    } catch (err) {
        if (releasesContainer) {
            releasesContainer.innerHTML = '<div class="text-red-400 text-sm">Search failed: ' + window.escapeHtml(err.message) + '</div>';
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText || 'Refresh Search';
        }
    }
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
window.refreshPlexAndReload = refreshPlexAndReload;
window.searchRequestFromDetails = searchRequestFromDetails;
