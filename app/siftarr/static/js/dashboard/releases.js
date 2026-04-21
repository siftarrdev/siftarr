// Dashboard Releases Module - Release card rendering and search actions
// ====================================================================

function renderAnnotation(value, toneClass = 'text-gray-400', dataAttr = '') {
    if (!value) return '';
    return `<span class="${toneClass}" ${dataAttr}>${window.escapeHtml(value)}</span>`;
}

function releaseAnnotationTone(release, field) {
    const isSizeFailure = !release.passed && typeof release.rejection_reason === 'string' && release.rejection_reason.toLowerCase().startsWith('size ');
    if (field === 'size') {
        if (isSizeFailure) return 'text-red-400';
        if (release.size_passed === true) return 'text-emerald-400';
        return 'text-gray-400';
    }
    if (field === 'resolution') {
        return release[field] ? 'text-emerald-400' : 'text-gray-400';
    }
    if (field === 'codec') {
        const codec = String(release.codec || '').toLowerCase();
        return /(av1|265)/.test(codec) ? 'text-emerald-400' : 'text-gray-400';
    }
    return 'text-gray-400';
}

function hasAnyMatch(names, needles) {
    return needles.some((needle) => names.some((name) => name.includes(needle)));
}

function formatRelativePublishAge(publishDate) {
    if (!publishDate) return '';

    const publishedAt = new Date(publishDate);
    if (Number.isNaN(publishedAt.getTime())) return '';

    const diffMs = Date.now() - publishedAt.getTime();
    if (diffMs < 0) return 'In the future';

    const minutes = Math.floor(diffMs / 60000);
    if (minutes < 60) return minutes <= 1 ? '1 minute ago' : `${minutes} minutes ago`;

    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours === 1 ? '1 hour ago' : `${hours} hours ago`;

    const days = Math.floor(hours / 24);
    if (days < 30) return days === 1 ? '1 day ago' : `${days} days ago`;

    const months = Math.floor(days / 30);
    if (months < 12) return months === 1 ? '1 month ago' : `${months} months ago`;

    const years = Math.floor(days / 365);
    return years === 1 ? '1 year ago' : `${years} years ago`;
}

function renderReleaseCard(release, requestId) {
    const statusClass = release.passed ? 'badge-green' : 'badge-yellow';
    const releaseScope = release.target_scope || {};
    const isScopedEpisodeRelease = releaseScope.type === 'single_episode';
    const secondaryMeta = [
        renderAnnotation(release.size, releaseAnnotationTone(release, 'size'), 'data-release-size="true"'),
        renderAnnotation(release.indexer, releaseAnnotationTone(release, 'indexer'), 'data-release-indexer="true"'),
        renderAnnotation(release.resolution, releaseAnnotationTone(release, 'resolution'), 'data-release-resolution="true"'),
        renderAnnotation(release.codec, releaseAnnotationTone(release, 'codec'), 'data-release-codec="true"'),
        renderAnnotation(release.release_group, releaseAnnotationTone(release, 'group'), 'data-release-group="true"'),
        release.files != null ? `<span class="text-gray-400">${release.files} file${release.files !== 1 ? 's' : ''}</span>` : null,
    ].filter(Boolean).join(' \u00B7 ');
    const availability = `Seeders ${release.seeders ?? 0} \u00B7 Leechers ${release.leechers ?? 0}`;
    const downloaded = release.downloaded ? '<span class="badge badge-blue">Already sent</span>' : '';
    const activeStagedTorrent = release.active_staged_torrent || (isScopedEpisodeRelease ? null : window.currentActiveStagedTorrent);
    const hasActiveStagedSelection = window.siftarrStagingModeEnabled && !!activeStagedTorrent;
    const isActiveSelection = !!release.is_active_selection || !!(
        !isScopedEpisodeRelease && hasActiveStagedSelection && activeStagedTorrent && release.title === activeStagedTorrent.title
    );
    const activeSelectionMode = window.siftarrStagingModeEnabled && isActiveSelection;
    const activeSelectionBadge = activeSelectionMode
        ? `<span class="badge ${
            (release.active_selection_status || activeStagedTorrent?.status) === 'approved' ? 'badge-blue' : 'badge-yellow'
        }">${window.escapeHtml((release.active_selection_source || activeStagedTorrent?.selection_source) === 'rule' ? 'Auto-selected and staged' : 'Currently staged')}</span>`
        : '';
    const actionVerb = window.siftarrStagingModeEnabled
        ? (isActiveSelection ? 'Already staged' : hasActiveStagedSelection ? 'Replace staged' : 'Stage release')
        : 'Download';
    const useLabel = release.passed ? actionVerb : `Force ${actionVerb}`;
    const storedReleaseId = release.stored_release_id || release.id;
    const formAction = storedReleaseId
        ? `/requests/${requestId}/releases/${storedReleaseId}/use`
        : `/requests/${requestId}/manual-release/use`;
    const disableAction = !(release.download_url || release.magnet_url);
    const manualDataJson = storedReleaseId ? '{}' : window.escapeHtml(JSON.stringify({
        title: release.title || '',
        size: release.size_bytes ?? 0,
        seeders: release.seeders ?? 0,
        leechers: release.leechers ?? 0,
        indexer: release.indexer || '',
        download_url: release.download_url || '',
        magnet_url: release.magnet_url || '',
        info_hash: release.info_hash || '',
        publish_date: release.publish_date || '',
        resolution: release.resolution || '',
        codec: release.codec || '',
        release_group: release.release_group || '',
    }));
    const actionTitle = window.siftarrStagingModeEnabled
        ? (isActiveSelection
            ? 'This torrent is already the active staged selection.'
            : hasActiveStagedSelection
                ? 'Replace the active staged torrent with this selection.'
                : 'Stage this torrent for review and approval.')
        : 'Send this torrent to qBittorrent.';
    const actionHtml = `<button type="button" class="btn-primary btn-sm" ${disableAction || activeSelectionMode ? 'disabled' : ''} title="${window.escapeHtml(disableAction ? 'No download source available' : actionTitle)}" data-stage-url="${window.escapeHtml(formAction)}" data-stage-fields="${manualDataJson}" onclick="stageRelease(this)">${useLabel}</button>`;
    const publishAge = formatRelativePublishAge(release.publish_date);
    const coverageHtml = (Array.isArray(release.covered_seasons) || release.is_complete_series)
        ? renderCoverageBadge(release)
        : '';
    const rejectionIsSize = typeof release.rejection_reason === 'string' && release.rejection_reason.toLowerCase().startsWith('size ');
    const rejectionHtml = !release.passed && release.rejection_reason && !rejectionIsSize
        ? `<div class="mt-2 max-w-xs text-right text-xs text-red-300" data-release-rejection-reason="true">${window.escapeHtml(release.rejection_reason)}</div>`
        : '';

    return `
        <div class="rounded-xl border border-gray-700/60 bg-surface-800 p-2">
            <div class="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
                <div class="min-w-0">
                    <div class="font-medium text-white text-sm break-words">${window.escapeHtml(release.title)}</div>
                    <div class="mt-1 text-xs text-gray-400">Score <span class="font-semibold text-emerald-400">${release.score}</span> &middot; ${secondaryMeta || '<span class="text-gray-400">No release metadata</span>'} &middot; ${window.escapeHtml(availability)}${publishAge ? ` &middot; <span data-release-upload-age="true">${window.escapeHtml(publishAge)}</span>` : ''}${release.files != null ? ` &middot; <span class="text-gray-400">${release.files} file${release.files !== 1 ? 's' : ''}</span>` : ''}</div>
                    ${coverageHtml}
                </div>
                <div class="flex shrink-0 flex-col items-end gap-2 text-right" data-release-status-column="true">
                    <span class="badge ${statusClass}">${window.escapeHtml(release.status_label || (release.passed ? 'Passed' : 'Rejected'))}</span>
                    ${downloaded}
                    ${activeSelectionBadge}
                    ${actionHtml}
                    ${rejectionHtml}
                </div>
            </div>
        </div>
    `;
}

function renderCoverageBadge(release) {
    const coveredSeasons = Array.isArray(release.covered_seasons) ? release.covered_seasons : [];
    const seasonCount = release.known_total_seasons;
    const coverageText = coveredSeasons.length
        ? `S${coveredSeasons.join(', S')}`
        : 'Season coverage unknown';
    const countText = seasonCount
        ? `${release.covered_season_count || coveredSeasons.length}/${seasonCount} seasons`
        : `${release.covered_season_count || coveredSeasons.length} seasons`;
    const seriesBadge = release.is_complete_series || release.covers_all_known_seasons
        ? '<span class="badge badge-green">Complete series</span>'
        : '';
    const sizePerSeason = release.size_per_season
        ? '<span data-release-size-per-season="true" class="' + (release.size_per_season_passed === false ? 'text-red-400' : 'text-emerald-400') + '">' + window.escapeHtml(release.size_per_season) + '/season</span>'
        : '';

    return '<div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">' +
        '<span class="badge badge-blue">' + window.escapeHtml(countText) + '</span>' +
        '<span>' + window.escapeHtml(coverageText) + '</span>' +
        sizePerSeason +
        seriesBadge +
    '</div>';
}

function episodeStatusBadge(status) {
    const colors = {
        'received': 'badge-gray',
        'searching': 'badge-blue',
        'pending': 'badge-yellow',
        'unreleased': 'badge-purple',
        'staged': 'badge-blue',
        'downloading': 'badge-blue',
        'completed': 'badge-green',
        'available': 'badge-green',
        'partially_available': 'badge-yellow',
        'failed': 'badge-blue',
    };
    return colors[status] || 'badge-gray';
}

function renderSeasonAccordion(data) {
    const tvInfo = data.tv_info;
    const requestId = data.request.id;
    const syncState = tvInfo.sync_state || {};

    if (!tvInfo.seasons || tvInfo.seasons.length === 0) {
        const emptyState = syncState.refresh_in_progress
            ? 'No cached season information yet. Background refresh in progress...'
            : 'No season information available.';
        return '<div class="text-gray-500 text-sm">' + window.escapeHtml(emptyState) + '</div>';
    }

    const syncBanner = syncState.stale || syncState.refresh_in_progress || syncState.needs_plex_enrichment
        ? '<div class="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-xs text-blue-200">' +
            (syncState.refresh_in_progress
                ? 'Showing cached TV details while a background refresh updates Plex/Overseerr data.'
                : syncState.needs_plex_enrichment
                    ? 'Showing cached TV details while Plex episode availability is being resolved for partial seasons.'
                    : 'Showing cached TV details. Reopen after a background refresh for newer availability.') +
          '</div>'
        : '';

    const searchAllSection = '<div class="rounded-xl border border-gray-700/60 bg-surface-800 p-3 space-y-3">' +
        '<div class="flex items-center justify-between gap-3">' +
            '<div>' +
                '<div class="text-white font-medium">Search Multi Season Packs</div>' +
                '<div class="text-xs text-gray-500">Broad search for multi-season ranges and complete-series packs without downloading.</div>' +
            '</div>' +
            '<button onclick="searchAllSeasonPacks(' + requestId + '); event.stopPropagation();" class="btn-primary btn-sm">Search Multi Season Packs</button>' +
        '</div>' +
        '<div id="season-packs-all-' + requestId + '" class="space-y-1"><div class="text-gray-500 text-sm">Run Search Multi Season Packs to inspect broad multi-season coverage.</div></div>' +
    '</div>';

    return '<div class="space-y-3">' + syncBanner + searchAllSection + tvInfo.seasons.map(function(season) {
        var seasonKey = String(season.season_number);
        var seasonBadgeClass = episodeStatusBadge(season.status);
        var seasonReleases = (tvInfo.releases_by_season && tvInfo.releases_by_season[seasonKey]) || [];
        var seasonReleasesHtml = seasonReleases.map(function(r) { return renderReleaseCard(r, requestId); }).join('');
        var episodeHtml = (season.episodes || []).map(function(ep) {
            var epKey = seasonKey + '-' + ep.episode_number;
            var badgeClass = episodeStatusBadge(ep.status);
            var episodeReleases = (tvInfo.releases_by_episode && tvInfo.releases_by_episode[epKey]) || [];
            var episodeReleasesHtml = episodeReleases.map(function(r) { return renderReleaseCard(r, requestId); }).join('');
            var episodeDetailsId = 'episode-details-' + requestId + '-' + season.season_number + '-' + ep.episode_number;
            var episodeSearchId = 'episode-search-' + requestId + '-' + season.season_number + '-' + ep.episode_number;
            var isOpen = episodeReleases.length > 0 ? ' open' : '';

            return '<details id="' + episodeDetailsId + '" class="group rounded-lg border border-gray-700/40 bg-surface-800/50"' + isOpen + '>' +
                '<summary class="flex items-center justify-between gap-3 cursor-pointer px-3 py-2 hover:bg-surface-850/60 transition-colors">' +
                    '<div class="flex items-center gap-3 min-w-0 flex-1">' +
                        '<svg class="accordion-chevron w-4 h-4 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
                        '<span class="text-gray-500 text-xs font-mono shrink-0">E' + String(ep.episode_number).padStart(2, '0') + '</span>' +
                        '<span class="text-white text-sm truncate">' + window.escapeHtml(ep.title || 'Untitled') + '</span>' +
                        (ep.air_date ? '<span class="text-gray-600 text-xs shrink-0">' + window.escapeHtml(ep.air_date) + '</span>' : '') +
                    '</div>' +
                    '<div class="flex items-center gap-2 shrink-0">' +
                        '<span class="badge ' + badgeClass + '">' + window.escapeHtml(ep.status || 'unknown') + '</span>' +
                        (ep.status !== 'available' && ep.status !== 'completed'
                            ? '<button onclick="markEpisodeAvailable(' + requestId + ', ' + ep.id + '); event.stopPropagation();" class="bg-brand-500 hover:bg-brand-400 text-white text-xs px-2 py-0.5 rounded">Mark Available</button>'
                            : '') +
                        '<button onclick="searchEpisode(' + requestId + ', ' + season.season_number + ', ' + ep.episode_number + '); event.stopPropagation();" class="btn-ghost btn-sm">Search</button>' +
                    '</div>' +
                '</summary>' +
                '<div id="' + episodeSearchId + '" class="ml-7 mr-3 mb-3 space-y-1">' + episodeReleasesHtml + '</div>' +
            '</details>';
        }).join('');

        var hasMarkable = (season.episodes || []).some(function(ep) { return ep.status !== 'available' && ep.status !== 'completed'; });

        var summaryBits = [season.available_count + '/' + season.total_count + ' available'];
        if (season.pending_count) summaryBits.push(season.pending_count + ' pending');
        if (season.unreleased_count) summaryBits.push(season.unreleased_count + ' unreleased');
        var availableText = summaryBits.join(' \u00B7 ');

        return '<details class="group">' +
            '<summary class="flex items-center justify-between gap-3 cursor-pointer rounded-xl border border-gray-700/60 bg-surface-800 p-3 hover:bg-surface-850/80 transition-colors">' +
                '<div class="flex items-center gap-3">' +
                    '<svg class="accordion-chevron w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
                    '<span class="text-white font-medium">Season ' + season.season_number + '</span>' +
                    '<span class="text-gray-500 text-xs">' + availableText + '</span>' +
                '</div>' +
                '<div class="flex items-center gap-2 shrink-0">' +
                    '<span class="badge ' + seasonBadgeClass + '">' + window.escapeHtml(season.status || 'unknown') + '</span>' +
                    (hasMarkable
                        ? '<button onclick="markSeasonAvailable(' + requestId + ', ' + season.id + '); event.stopPropagation();" class="bg-brand-500 hover:bg-brand-400 text-white text-xs px-2 py-0.5 rounded">Mark All Available</button>'
                        : '') +
                    '<button onclick="searchSeasonPacks(' + requestId + ', ' + season.season_number + '); event.stopPropagation();" class="btn-primary btn-sm">Search Season Packs</button>' +
                '</div>' +
            '</summary>' +
            '<div class="mt-2 ml-2 space-y-2">' +
                '<div id="season-packs-' + requestId + '-' + season.season_number + '" class="space-y-1">' + seasonReleasesHtml + '</div>' +
                episodeHtml +
            '</div>' +
        '</details>';
    }).join('') + '</div>';
}

async function markEpisodeAvailable(requestId, episodeId) {
    try {
        var response = await fetch('/requests/' + requestId + '/episodes/' + episodeId + '/mark-available', { method: 'POST' });
        if (!response.ok) throw new Error('Server error: ' + response.status);
        window.openRequestDetails(requestId);
    } catch (e) {
        console.error('Failed to mark episode available:', e);
    }
}

async function markSeasonAvailable(requestId, seasonId) {
    try {
        var response = await fetch('/requests/' + requestId + '/seasons/' + seasonId + '/mark-all-available', { method: 'POST' });
        if (!response.ok) throw new Error('Server error: ' + response.status);
        window.openRequestDetails(requestId);
    } catch (e) {
        console.error('Failed to mark season available:', e);
    }
}

async function searchSeasonPacks(requestId, seasonNumber) {
    var container = document.getElementById('season-packs-' + requestId + '-' + seasonNumber);
    if (!container) return;
    container.innerHTML = '<div class="flex items-center gap-2 text-gray-400 text-sm py-2">' +
        '<svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
        ' Searching season packs...</div>';

    try {
        var response = await fetch('/requests/' + requestId + '/seasons/' + seasonNumber + '/search', { method: 'POST' });
        if (!response.ok) throw new Error('Server error: ' + response.status);
        var data = await response.json();

        if (data.releases && data.releases.length > 0) {
            container.innerHTML = data.releases.map(function(r) { return renderReleaseCard(r, requestId); }).join('');
        } else {
            container.innerHTML = '<div class="text-gray-500 text-sm py-2">No season pack results found.</div>';
        }
    } catch (err) {
        container.innerHTML = '<div class="text-red-400 text-sm py-2">Error: ' + window.escapeHtml(err.message) + '</div>';
    }
}

function renderSearchAllResults(releases) {
    return releases.map(function(release) {
        return renderReleaseCard(release, window.currentRequestId);
    }).join('');
}

async function searchAllSeasonPacks(requestId = null) {
    var targetRequestId = requestId || window.currentRequestId;
    if (!targetRequestId) return;

    var container = document.getElementById('season-packs-all-' + targetRequestId);
    if (!container) {
        container = document.getElementById('tv-search-all-results');
    }
    if (!container) return;

    container.innerHTML = '<div class="flex items-center gap-2 text-gray-400 text-sm py-2">' +
        '<svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
        ' Searching multi season packs...</div>';

    try {
        var response = await fetch('/requests/' + targetRequestId + '/seasons/search-all', { method: 'POST' });
        if (!response.ok) throw new Error('Server error: ' + response.status);
        var data = await response.json();

        if (data.releases && data.releases.length > 0) {
            container.innerHTML = renderSearchAllResults(data.releases);
        } else {
            container.innerHTML = '<div class="text-gray-500 text-sm py-2">No multi season or complete-series results found.</div>';
        }
    } catch (err) {
        container.innerHTML = '<div class="text-red-400 text-sm py-2">Error: ' + window.escapeHtml(err.message) + '</div>';
    }
}

async function searchEpisode(requestId, seasonNumber, episodeNumber) {
    var details = document.getElementById('episode-details-' + requestId + '-' + seasonNumber + '-' + episodeNumber);
    var container = document.getElementById('episode-search-' + requestId + '-' + seasonNumber + '-' + episodeNumber);
    if (!container) return;
    if (details) details.open = true;
    container.innerHTML = '<div class="flex items-center gap-2 text-gray-400 text-sm py-2">' +
        '<svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
        ' Searching episode...</div>';

    try {
        var response = await fetch('/requests/' + requestId + '/seasons/' + seasonNumber + '/episodes/' + episodeNumber + '/search', { method: 'POST' });
        if (!response.ok) throw new Error('Server error: ' + response.status);
        var data = await response.json();

        if (data.releases && data.releases.length > 0) {
            container.innerHTML = data.releases.map(function(r) { return renderReleaseCard(r, requestId); }).join('');
        } else {
            container.innerHTML = '<div class="text-gray-500 text-sm py-2">No results found for this episode.</div>';
        }
    } catch (err) {
        container.innerHTML = '<div class="text-red-400 text-sm py-2">Error: ' + window.escapeHtml(err.message) + '</div>';
    }
}

function toggleTvSearchDropdown(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('tv-search-dropdown');
    if (!dropdown) return;
    dropdown.classList.toggle('hidden');
}

function closeTvSearchDropdown() {
    const dropdown = document.getElementById('tv-search-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
}

function populateTvSearchDropdown() {
    const container = document.getElementById('tv-search-dropdown-seasons');
    if (!container) return;
    container.innerHTML = window.currentTvSeasons.map(function(season) {
        return '<button onclick="searchSeasonPacks(window.currentRequestId, ' + season.season_number + '); closeTvSearchDropdown();" ' +
            'class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-surface-850 cursor-pointer transition-colors">' +
            'Search Season ' + season.season_number + ' Packs</button>';
    }).join('');
}

async function searchAllEpisodes() {
    if (!window.currentRequestId || !window.currentTvSeasons.length) return;
    closeTvSearchDropdown();

    const allEpisodes = [];
    for (const season of window.currentTvSeasons) {
        for (const ep of (season.episodes || [])) {
            if (ep.status === 'completed' || ep.status === 'available') continue;
            allEpisodes.push({ season: season.season_number, episode: ep.episode_number });
        }
    }

    if (allEpisodes.length === 0) {
        window.showToast('No pending episodes to search.');
        return;
    }

    for (let i = 0; i < allEpisodes.length; i++) {
        const ep = allEpisodes[i];
        window.showToast('Searching S' + String(ep.season).padStart(2, '0') + 'E' + String(ep.episode).padStart(2, '0') + '... (' + (i + 1) + '/' + allEpisodes.length + ')');
        await searchEpisode(window.currentRequestId, ep.season, ep.episode);
    }

    window.showToast('Finished searching all episodes (' + allEpisodes.length + ' total).');
}

async function stageRelease(btn) {
    const url = btn.dataset.stageUrl;
    const fields = JSON.parse(btn.dataset.stageFields || '{}');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = window.siftarrStagingModeEnabled ? 'Updating…' : 'Sending…';
    try {
        const formData = new FormData();
        for (const [key, value] of Object.entries(fields)) {
            formData.append(key, String(value));
        }
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData,
        });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => null);
            throw new Error(errData?.detail || `HTTP ${resp.status}`);
        }
        const payload = await resp.json().catch(() => ({}));
        btn.textContent = window.siftarrStagingModeEnabled ? 'Active ✓' : 'Sent ✓';
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-disabled');
        window.showToast(payload.message || (window.siftarrStagingModeEnabled ? 'Active staged selection updated' : 'Torrent sent successfully'));
        window.refreshStagedTabData();
        if (window.siftarrStagingModeEnabled && window.currentRequestId) {
            await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex);
        }
    } catch (err) {
        btn.disabled = false;
        btn.textContent = originalText;
        window.showToast('Error: ' + err.message);
    }
}

function updateActiveStageBanner(data) {
    const banner = document.getElementById('request-details-active-stage-banner');
    if (!banner) return;
    const active = data.active_staged_torrent;
    window.currentActiveStagedTorrent = active || null;
    if (!window.siftarrStagingModeEnabled || !active) {
        banner.classList.add('hidden');
        banner.textContent = '';
        return;
    }

    const sourceLabel = active.selection_source === 'rule' ? 'Auto-selected torrent' : 'Active staged torrent';
    const statusLabel = active.status === 'approved' ? 'sent to qBittorrent' : 'already staged';
    banner.textContent = `${sourceLabel}: ${active.title} (${statusLabel}). Selecting another result will replace it.`;
    banner.classList.remove('hidden');
}

// Export functions to window for HTML onclick handlers
window.renderReleaseCard = renderReleaseCard;
window.renderSeasonAccordion = renderSeasonAccordion;
window.markEpisodeAvailable = markEpisodeAvailable;
window.markSeasonAvailable = markSeasonAvailable;
window.searchSeasonPacks = searchSeasonPacks;
window.searchAllSeasonPacks = searchAllSeasonPacks;
window.searchEpisode = searchEpisode;
window.toggleTvSearchDropdown = toggleTvSearchDropdown;
window.closeTvSearchDropdown = closeTvSearchDropdown;
window.populateTvSearchDropdown = populateTvSearchDropdown;
window.searchAllEpisodes = searchAllEpisodes;
window.stageRelease = stageRelease;
window.updateActiveStageBanner = updateActiveStageBanner;
window.escapeHtml = escapeHtml;
