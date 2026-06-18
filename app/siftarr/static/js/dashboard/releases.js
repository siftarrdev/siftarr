// Dashboard Releases Module - Release card rendering and search actions
// ====================================================================

function renderAnnotation(value, toneClass = 'text-gray-400', dataAttr = '') {
    if (!value) return '';
    return `<span class="${toneClass}" ${dataAttr}>${window.escapeHtml(value)}</span>`;
}

function renderSearchLoadingState(message) {
    return '<div class="dashboard-search-loading" role="status" aria-live="polite">' +
        '<svg class="animate-spin h-4 w-4 shrink-0" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
        '<span>' + window.escapeHtml(message) + '</span>' +
    '</div>';
}

function renderMovieSearchLoadingState() {
    return '<div class="dashboard-details-search-loading" role="status" aria-live="polite">' +
        '<div class="flex items-start gap-3">' +
            '<svg class="animate-spin h-5 w-5 shrink-0 text-blue-300 mt-0.5" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
            '<div>' +
                '<div class="text-sm font-semibold text-white">Searching movie torrents</div>' +
                '<div class="mt-1 text-xs text-blue-200/90">Checking indexers now. Results will appear here when the search completes.</div>' +
            '</div>' +
        '</div>' +
    '</div>';
}

function releaseAnnotationTone(release, field) {
    const matches = Array.isArray(release.matches) ? release.matches : [];
    const fieldValue = String(release[field] || (field === 'group' ? release.release_group : '') || '').toLowerCase();
    const fieldTokens = {
        size: ['size'],
        resolution: ['resolution', 'quality', fieldValue].filter(Boolean),
        codec: ['codec', fieldValue].filter(Boolean),
        group: ['group', 'release group', fieldValue].filter(Boolean),
        indexer: ['indexer', 'uploader', fieldValue].filter(Boolean),
    }[field] || [field, fieldValue].filter(Boolean);
    const relevantMatches = matches.filter((match) => {
        const ruleType = String(match.rule_type || '').toLowerCase();
        const effect = String(match.effect || '').toLowerCase();
        if (field === 'size' && (ruleType === 'size_limit' || effect === 'size_limit')) return true;
        const ruleName = String(match.rule_name || '').toLowerCase();
        return fieldTokens.some((token) => token && ruleName.includes(token));
    });
    if (relevantMatches.some((match) => match.matched === true && String(match.effect || '').toLowerCase() === 'disallow')) {
        return 'text-red-400';
    }
    if (field === 'size' && relevantMatches.some((match) => match.matched === false && String(match.effect || '').toLowerCase() === 'size_limit')) {
        return 'text-red-400';
    }
    if (relevantMatches.some((match) => match.matched === true && ['allow', 'size_limit'].includes(String(match.effect || '').toLowerCase()))) {
        return 'text-emerald-400';
    }
    if (matches.length > 0) {
        return 'text-gray-400';
    }
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

// Render a release card in the calm, score-first layout. Movie list items wrap
// the row in `<li>`; TV episode buckets (Phase 3) pass `{ bucket: true }` to
// render a bordered `<div>` variant with tighter spacing. Both variants share
// the score gutter + title/meta body + a single right-aligned action. Staged
// (active selection) cards carry inline Approve + Discard; conflicting siblings
// show Replace; passed releases show Stage; rejected releases dim and inline the
// rejection reason as the meta line, with a Force action.
function renderReleaseCard(release, requestId, options = {}) {
    const bucket = !!(options && options.bucket);
    const releaseScope = release.target_scope || {};
    const isScopedEpisodeRelease = releaseScope.type === 'single_episode';
    const activeStagedTorrent = release.active_staged_torrent || null;
    const conflictsActiveSelection = !!release.conflicts_active_selection;
    const hasActiveStagedSelection = window.siftarrStagingModeEnabled && conflictsActiveSelection;
    const isActiveSelection = !!release.is_active_selection || !!(
        !isScopedEpisodeRelease && hasActiveStagedSelection && activeStagedTorrent && release.title === activeStagedTorrent.title
    );
    const activeSelectionMode = window.siftarrStagingModeEnabled && isActiveSelection;
    const rejected = release.passed === false;

    const scoreColor = release.passed ? 'text-emerald-400' : 'text-gray-200';
    const gutterWidth = bucket ? 'w-8' : 'w-10';
    const scoreSize = bucket ? 'text-base' : 'text-xl';
    const scoreGutter = '<div class="shrink-0 ' + gutterWidth + ' text-right"><div class="' + scoreSize + ' font-bold ' + scoreColor + ' tabular-nums leading-none">' + window.escapeHtml(String(release.score ?? 0)) + '</div></div>';

    const stagedBadge = activeSelectionMode
        ? '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-cyan-900/60 text-cyan-300 ring-1 ring-inset ring-cyan-700/40">Staged</span>'
        : '';
    const titleText = window.escapeHtml(release.title);
    const titleHtml = stagedBadge
        ? '<div class="flex items-center gap-2 flex-wrap"><span class="text-sm text-white font-medium truncate">' + titleText + '</span>' + stagedBadge + '</div>'
        : '<div class="text-sm text-white font-medium truncate">' + titleText + '</div>';

    let metaHtml;
    if (rejected) {
        const reason = release.rejection_reason ? ' · ' + window.escapeHtml(release.rejection_reason) : '';
        metaHtml = '<div class="mt-0.5 text-xs text-red-300/80">Rejected' + reason + '</div>';
    } else {
        const metaParts = [
            renderAnnotation(release.resolution, releaseAnnotationTone(release, 'resolution'), 'data-release-resolution="true"'),
            renderAnnotation(release.codec, releaseAnnotationTone(release, 'codec'), 'data-release-codec="true"'),
            renderAnnotation(release.size, releaseAnnotationTone(release, 'size'), 'data-release-size="true"'),
            release.seeders != null ? '<span>' + window.escapeHtml(String(release.seeders)) + ' seeders</span>' : '',
            release.indexer ? renderAnnotation(release.indexer, 'text-gray-500', 'data-release-indexer="true"') : '',
        ].filter(Boolean);
        metaHtml = metaParts.length ? '<div class="mt-0.5 text-xs text-gray-400 flex items-center gap-2 flex-wrap">' + metaParts.join('<span>·</span>') + '</div>' : '';
    }

    const bodyHtml = '<div class="min-w-0 flex-1">' + titleHtml + metaHtml + '</div>';

    const storedReleaseId = release.stored_release_id || release.id;
    const formAction = storedReleaseId
        ? '/requests/' + requestId + '/releases/' + storedReleaseId + '/use'
        : '/requests/' + requestId + '/manual-release/use';
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
    const stageScopeJson = window.escapeHtml(JSON.stringify(releaseScope || {}));
    const actionTitle = window.siftarrStagingModeEnabled
        ? (isActiveSelection
            ? 'This torrent is already the active staged selection.'
            : hasActiveStagedSelection
                ? 'Replace the active staged torrent with this selection.'
                : 'Stage this torrent for review and approval.')
        : 'Send this torrent to qBittorrent.';

    let actionHtml;
    if (activeSelectionMode && activeStagedTorrent && activeStagedTorrent.id) {
        const stagedId = activeStagedTorrent.id;
        actionHtml = '<div class="flex items-center gap-2 shrink-0">' +
            '<button type="button" onclick="inlineStagedAction(\'/staged/' + stagedId + '/approve\', this)" class="rounded-lg bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white">Approve</button>' +
            '<button type="button" onclick="inlineStagedAction(\'/staged/' + stagedId + '/discard\', this)" class="rounded-lg text-xs px-3 py-1.5 text-red-400 hover:text-red-300 hover:bg-red-950/40">Discard</button>' +
        '</div>';
    } else {
        const disabledAttr = disableAction ? ' disabled' : '';
        const stageAttrs = ' title="' + window.escapeHtml(disableAction ? 'No download source available' : actionTitle) + '" data-stage-url="' + window.escapeHtml(formAction) + '" data-stage-fields="' + manualDataJson + '" data-stage-scope="' + stageScopeJson + '" onclick="stageRelease(this)"';
        if (hasActiveStagedSelection && !isActiveSelection) {
            actionHtml = '<button type="button" class="rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 hover:text-white shrink-0"' + disabledAttr + stageAttrs + '>Replace</button>';
        } else if (!window.siftarrStagingModeEnabled) {
            actionHtml = '<button type="button" class="' + (rejected ? 'rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-500 hover:text-white' : 'rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs font-semibold text-white') + ' shrink-0"' + disabledAttr + stageAttrs + '>' + (rejected ? 'Force Download' : 'Download') + '</button>';
        } else if (rejected) {
            actionHtml = '<button type="button" class="rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-500 hover:text-white shrink-0"' + disabledAttr + stageAttrs + '>Force</button>';
        } else {
            actionHtml = '<button type="button" class="rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs font-semibold text-white shrink-0"' + disabledAttr + stageAttrs + '>Stage</button>';
        }
    }

    if (bucket) {
        const outerClass = activeSelectionMode
            ? 'rounded-lg border border-cyan-500/40 bg-cyan-950/20'
            : 'rounded-lg border border-gray-700/40 bg-surface-800';
        return '<div class="' + outerClass + (rejected ? ' opacity-60' : '') + ' p-2.5 flex items-center gap-3">' + scoreGutter + bodyHtml + actionHtml + '</div>';
    }

    const outerClass = activeSelectionMode
        ? 'bg-cyan-950/20 border-b border-cyan-500/50'
        : 'hover:bg-surface-850/60';
    return '<li class="flex items-center gap-4 px-4 py-3 ' + outerClass + (rejected ? ' opacity-60' : '') + '">' + scoreGutter + bodyHtml + actionHtml + '</li>';
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
        ? '<span data-release-size-per-season="true" class="' + (release.size_per_season_passed === true ? 'text-emerald-400' : release.size_per_season_passed === false ? 'text-red-400' : 'text-gray-400') + '">' + window.escapeHtml(release.size_per_season) + '/season</span>'
        : '';

    return '<div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">' +
        '<span class="badge badge-blue">' + window.escapeHtml(countText) + '</span>' +
        '<span>' + window.escapeHtml(coverageText) + '</span>' +
        sizePerSeason +
        seriesBadge +
    '</div>';
}

function isUsableCachedRelease(release) {
    return !!release && !!(release.stored_release_id || release.id) && !!(release.download_url || release.magnet_url) && release.passed !== false;
}

function renderStageTopEpisodeButton(requestId, release) {
    if (!isUsableCachedRelease(release)) return '';
    const storedReleaseId = release.stored_release_id || release.id;
    return '<button type="button" onclick="stageTopEpisodeRelease(this, ' + requestId + ', ' + storedReleaseId + '); event.preventDefault(); event.stopPropagation();" class="shrink-0 rounded-lg bg-brand-600 hover:bg-brand-500 px-2.5 py-1 text-[11px] font-semibold text-white">Stage top</button>';
}

const TV_ACCORDION_TOGGLE_CLASS = 'tv-accordion-toggle inline-flex items-center justify-center rounded-md border border-gray-600/80 bg-surface-900/70 px-2.5 py-1 text-xs font-medium leading-4 text-gray-200 shadow-sm transition-colors hover:border-brand-400/70 hover:bg-surface-800 hover:text-white focus:outline-none focus:ring-2 focus:ring-brand-400/60 focus:ring-offset-2 focus:ring-offset-surface-900';

function renderTvAccordionToggle(scope, requestId, seasonNumber = null) {
    const toggleAttr = scope === 'season'
        ? 'data-tv-accordion-toggle="season"'
        : 'data-tv-accordion-toggle="panel"';
    const seasonAttrs = scope === 'season'
        ? ' data-season-number="' + seasonNumber + '"'
        : '';
    const clickHandler = scope === 'season'
        ? 'event.preventDefault(); event.stopPropagation(); toggleTvSeasonDetails(' + requestId + ', ' + seasonNumber + ');'
        : 'toggleTvDetailsAll(' + requestId + ');';

    return '<button type="button" ' + toggleAttr + ' data-request-id="' + requestId + '"' + seasonAttrs + ' aria-expanded="false" onclick="' + clickHandler + '" class="' + TV_ACCORDION_TOGGLE_CLASS + '">Expand all</button>';
}

function episodeStatusBadge(status) {
    const colors = {
        'received': 'badge-gray',
        'searching': 'badge-blue',
        'pending': 'badge-yellow',
        'unreleased': 'badge-purple',
        'staged': 'badge-cyan',
        'downloading': 'badge-blue',
        'completed': 'badge-green',
        'available': 'badge-green',
        'partially_available': 'badge-yellow',
        'failed': 'badge-blue',
    };
    return colors[status] || 'badge-gray';
}

// Render a single TV scope chip for the "Show: All results · Season packs ·
// Complete series" filter bar. The active chip uses the brand fill; inactive
// chips use a quiet bordered pill. Clicking switches `detailsControlState.scope`
// (client-only) via `setDetailsScope`, which re-renders the TV branch without a
// backend reload.
function renderScopeChip(requestId, value, label, count, activeScope) {
    const isActive = (activeScope || 'all') === value;
    const classes = isActive
        ? 'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium bg-brand-600 text-white'
        : 'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border border-gray-600/80 text-gray-400 hover:text-white hover:border-brand-500 transition-colors';
    const countClass = isActive ? 'text-white/70' : 'text-gray-600';
    return '<button type="button" onclick="setDetailsScope(' + requestId + ', \'' + value + '\')" class="' + classes + '">' + window.escapeHtml(label) + ' <span class="' + countClass + '">' + count + '</span></button>';
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

    // Client-only scope (detailsControlState.scope). Defaults to 'all' when the
    // control state has not been initialized yet.
    const controls = (window.detailsControlState && window.detailsControlState[requestId]) || {};
    const scope = controls.scope || 'all';

    // Collect multi-season pack + complete-series releases (now behind scope
    // chips, not their own nested <details> drawers). Reuses the same
    // releases_by_season scan + dedup as the previous deep accordion.
    const seenMultiSeasonReleases = new Set();
    const multiSeasonReleases = [];
    const completeSeriesReleases = [];
    Object.values(tvInfo.releases_by_season || {}).forEach(function(releases) {
        (releases || []).forEach(function(release) {
            const relScope = release.target_scope || {};
            const coveredSeasons = Array.isArray(release.covered_seasons) ? release.covered_seasons : [];
            const isMultiSeason = relScope.type === 'multi_season_pack' || relScope.type === 'complete_series' || release.is_complete_series || coveredSeasons.length > 1;
            const isCompleteSeries = release.is_complete_series || release.covers_all_known_seasons || relScope.type === 'complete_series';
            const releaseKey = release.stored_release_id || release.id || release.title;
            if (isMultiSeason && !seenMultiSeasonReleases.has(releaseKey)) {
                seenMultiSeasonReleases.add(releaseKey);
                multiSeasonReleases.push(release);
                if (isCompleteSeries) completeSeriesReleases.push(release);
            }
        });
    });

    // Aggregate availability text from tv_info.aggregate_counts (e.g. "3 of 10 available").
    const aggregateCounts = tvInfo.aggregate_counts || {};
    const aggregateAvailable = Number(aggregateCounts.available || 0);
    const aggregateTotal = Number(aggregateCounts.total || 0);
    const aggregateText = aggregateAvailable + ' of ' + aggregateTotal + ' available';

    // Total episode count across seasons drives the "All results" chip count.
    const totalEpisodeCount = tvInfo.seasons.reduce(function(sum, season) {
        return sum + ((season.episodes || []).length);
    }, 0);

    const scopeChipBar = '<div class="shrink-0 flex items-center gap-2 flex-wrap px-4 py-2.5 border-b border-gray-700/40">' +
        '<span class="text-xs text-gray-500">Show:</span>' +
        renderScopeChip(requestId, 'all', 'All results', totalEpisodeCount, scope) +
        renderScopeChip(requestId, 'season_packs', 'Season packs', multiSeasonReleases.length, scope) +
        renderScopeChip(requestId, 'complete_series', 'Complete series', completeSeriesReleases.length, scope) +
        '<span class="ml-auto text-xs text-gray-500">' + window.escapeHtml(aggregateText) + '</span>' +
    '</div>';

    // ── Section: All results (2-level Season → Episode accordion) ──
    const panelToggle = '<div class="flex justify-end">' + renderTvAccordionToggle('panel', requestId) + '</div>';

    const seasonAccordion = tvInfo.seasons.map(function(season) {
        const seasonKey = String(season.season_number);
        const seasonBadgeClass = episodeStatusBadge(season.status);
        const hasMarkable = (season.episodes || []).some(function(ep) { return ep.status !== 'available' && ep.status !== 'completed'; });
        const seasonHasStaged = (season.episodes || []).some(function(ep) { return ep.status === 'staged'; });

        const summaryBits = [season.available_count + '/' + season.total_count + ' available'];
        if (season.staged_count) summaryBits.push(season.staged_count + ' staged');
        if (season.pending_count) summaryBits.push(season.pending_count + ' pending');
        if (season.unreleased_count) summaryBits.push(season.unreleased_count + ' unreleased');
        const availableText = summaryBits.join(' \u00B7 ');

        const seasonLinks = '<div class="ml-auto flex items-center gap-3 text-xs">' +
            (hasMarkable
                ? '<button type="button" onclick="markSeasonAvailable(' + requestId + ', ' + season.id + '); event.stopPropagation();" class="text-brand-400 hover:text-brand-300">Mark all</button>'
                : '') +
            '<button type="button" onclick="stageIndividualEpisodes(this, ' + requestId + ', ' + season.season_number + '); event.preventDefault(); event.stopPropagation();" class="text-brand-400 hover:text-brand-300">Stage individual episodes</button>' +
            '<button type="button" onclick="searchSeasonPacks(' + requestId + ', ' + season.season_number + '); event.preventDefault(); event.stopPropagation();" class="text-brand-400 hover:text-brand-300">Search season</button>' +
        '</div>';

        const episodeHtml = (season.episodes || []).map(function(ep) {
            const epKey = seasonKey + '-' + ep.episode_number;
            const badgeClass = episodeStatusBadge(ep.status);
            const episodeReleases = (tvInfo.releases_by_episode && tvInfo.releases_by_episode[epKey]) || [];
            const episodeDetailsId = 'episode-details-' + requestId + '-' + season.season_number + '-' + ep.episode_number;
            const isStaged = ep.status === 'staged';
            const isOpen = episodeReleases.length > 0 || isStaged;
            const episodeBucketHtml = episodeReleases.length
                ? episodeReleases.map(function(r) { return renderReleaseCard(r, requestId, { bucket: true }); }).join('')
                : '<div class="text-gray-500 text-sm py-2">No cached episode results yet. Search for new checks missing aired episodes; Full search refreshes all aired episode results.</div>';
            const topRelease = episodeReleases.find(isUsableCachedRelease);
            const showInlineActions = ep.status !== 'available' && ep.status !== 'completed';

            return '<details id="' + episodeDetailsId + '" class="group rounded-lg border ' + (isStaged ? 'border-cyan-500/30 bg-cyan-950/10' : 'border-gray-700/40 bg-surface-900/50') + '" ontoggle="window.updateTvAccordionControls && window.updateTvAccordionControls()"' + (isOpen ? ' open' : '') + '>' +
                '<summary class="flex items-center gap-3 cursor-pointer px-3 py-2 hover:bg-surface-850/60 transition-colors">' +
                    '<svg class="accordion-chevron w-3.5 h-3.5 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
                    '<span class="text-xs font-mono text-gray-500 shrink-0">S' + String(season.season_number).padStart(2, '0') + 'E' + String(ep.episode_number).padStart(2, '0') + '</span>' +
                    '<span class="text-sm text-white truncate flex-1">' + window.escapeHtml(ep.title || 'Untitled') + '</span>' +
                    '<div class="flex items-center gap-2 shrink-0">' +
                        '<span class="badge ' + badgeClass + '">' + window.escapeHtml(ep.status || 'unknown') + '</span>' +
                        (showInlineActions ? renderStageTopEpisodeButton(requestId, topRelease) : '') +
                        (showInlineActions
                            ? '<button type="button" onclick="markEpisodeAvailable(' + requestId + ', ' + ep.id + '); event.stopPropagation();" class="shrink-0 text-xs text-brand-400 hover:text-brand-300">Mark Available</button>'
                            : '') +
                    '</div>' +
                '</summary>' +
                '<div class="px-3 pb-3 pt-2 space-y-2">' + episodeBucketHtml + '</div>' +
            '</details>';
        }).join('');

        return '<details id="season-details-' + requestId + '-' + season.season_number + '" class="group rounded-xl border border-gray-700/60 bg-surface-850" ontoggle="window.updateTvAccordionControls && window.updateTvAccordionControls()"' + (seasonHasStaged ? ' open' : '') + '>' +
            '<summary class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-surface-800 transition-colors">' +
                '<svg class="accordion-chevron w-4 h-4 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
                '<span class="text-white font-medium text-sm">Season ' + season.season_number + '</span>' +
                '<span class="text-xs text-gray-500">' + window.escapeHtml(availableText) + '</span>' +
                '<span class="badge ' + seasonBadgeClass + '">' + window.escapeHtml(season.status || 'unknown') + '</span>' +
                seasonLinks +
            '</summary>' +
            '<div class="px-4 pb-4 space-y-2 border-t border-gray-700/40 pt-3">' + episodeHtml + '</div>' +
        '</details>';
    }).join('');

    const allSection = '<div class="space-y-3"' + (scope === 'all' ? '' : ' hidden') + '>' + panelToggle + seasonAccordion + '</div>';

    // ── Section: Season packs (flat list of multi-season pack releases) ──
    const seasonPacksSection = '<div id="scope-season-packs-' + requestId + '" class="space-y-2"' + (scope === 'season_packs' ? '' : ' hidden') + '>' +
        (multiSeasonReleases.length
            ? '<ul class="divide-y divide-gray-700/40 rounded-xl border border-gray-700/60 bg-surface-850">' + multiSeasonReleases.map(function(r) { return renderReleaseCard(r, requestId); }).join('') + '</ul>'
            : '<div class="text-gray-500 text-sm py-2">No cached season-pack results yet. Full search refreshes all aired episode and pack results.</div>') +
    '</div>';

    // ── Section: Complete series (flat list of complete-series releases) ──
    const completeSeriesSection = '<div id="scope-complete-series-' + requestId + '" class="space-y-2"' + (scope === 'complete_series' ? '' : ' hidden') + '>' +
        (completeSeriesReleases.length
            ? '<ul class="divide-y divide-gray-700/40 rounded-xl border border-gray-700/60 bg-surface-850">' + completeSeriesReleases.map(function(r) { return renderReleaseCard(r, requestId); }).join('') + '</ul>'
            : '<div class="text-gray-500 text-sm py-2">No complete-series results yet. Full search refreshes all aired episode and pack results.</div>') +
    '</div>';

    return '<div class="space-y-3">' + scopeChipBar + allSection + seasonPacksSection + completeSeriesSection + '</div>';
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
    // Season packs now live behind the "Season packs" scope chip, so switch to
    // that view first (client-only re-render) and render results into its container.
    if (window.setDetailsScope) window.setDetailsScope(requestId, 'season_packs');
    var container = document.getElementById('scope-season-packs-' + requestId);
    if (!container) return;
    container.innerHTML = renderSearchLoadingState('Searching season packs...');
    window.startTvSearchProgress('/requests/' + requestId + '/seasons/' + seasonNumber + '/season-packs/search/stream', 'Season ' + seasonNumber, function(data) {
        var releases = data.releases || [];
        container.innerHTML = releases.length
            ? '<ul class="divide-y divide-gray-700/40 rounded-xl border border-gray-700/60 bg-surface-850">' + releases.map(function(r) { return renderReleaseCard(r, requestId); }).join('') + '</ul>'
            : '<div class="text-gray-500 text-sm py-2">No season pack results found.</div>';
    });
}

function renderSearchAllResults(releases) {
    return releases.map(function(release) {
        return renderReleaseCard(release, window.currentRequestId);
    }).join('');
}

async function searchEpisode(requestId, seasonNumber, episodeNumber) {
    var details = document.getElementById('episode-details-' + requestId + '-' + seasonNumber + '-' + episodeNumber);
    var container = document.getElementById('episode-search-' + requestId + '-' + seasonNumber + '-' + episodeNumber);
    if (!container) return;
    if (details) details.open = true;
    container.innerHTML = renderSearchLoadingState('Searching episode...');
    const episodeLabel = 'S' + String(seasonNumber).padStart(2, '0') + 'E' + String(episodeNumber).padStart(2, '0');
    window.startTvSearchProgress('/requests/' + requestId + '/seasons/' + seasonNumber + '/episodes/' + episodeNumber + '/search/stream', episodeLabel, async function(data) {
        container.innerHTML = (data.releases || []).map(function(r) { return renderReleaseCard(r, requestId); }).join('') || '<div class="text-gray-500 text-sm py-2">No results found for this episode.</div>';
        // Refresh the details modal to update episode status badge, season counts, and active stage banner
        await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
    });
}

async function stageRelease(btn) {
    const url = btn.dataset.stageUrl;
    const fields = JSON.parse(btn.dataset.stageFields || '{}');
    const stagedScope = JSON.parse(btn.dataset.stageScope || '{}');
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
            throw new Error(errData?.detail || errData?.message || `HTTP ${resp.status}`);
        }
        const payload = await resp.json().catch(() => ({}));
        btn.textContent = window.siftarrStagingModeEnabled ? 'Active ✓' : 'Sent ✓';
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-disabled');
        window.showToast(payload.message || (window.siftarrStagingModeEnabled ? 'Active staged selection updated' : 'Torrent sent successfully'));
        window.refreshStagedTabData();
        if (window.siftarrStagingModeEnabled && window.currentRequestId) {
            await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, {
                preserveUiState: true,
                focusTvScope: stagedScope,
            });
        }
    } catch (err) {
        btn.disabled = false;
        btn.textContent = originalText;
        window.showToast('Error: ' + err.message);
    }
}

async function stageTopEpisodeRelease(btn, requestId, releaseId) {
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Staging…';
    try {
        const resp = await fetch('/requests/' + requestId + '/releases/' + releaseId + '/use', {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: new FormData(),
        });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => null);
            throw new Error(errData?.detail || `HTTP ${resp.status}`);
        }
        const payload = await resp.json().catch(() => ({}));
        window.showToast(payload.message || 'Top episode release staged');
        window.refreshStagedTabData();
        await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
    } catch (err) {
        btn.disabled = false;
        btn.textContent = originalText;
        window.showToast('Error: ' + err.message);
    }
}

async function stageIndividualEpisodes(btn, requestId, seasonNumber) {
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Staging…';
    try {
        const resp = await fetch('/requests/' + requestId + '/seasons/' + seasonNumber + '/stage-individual-episodes', {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => null);
            throw new Error(errData?.detail || `HTTP ${resp.status}`);
        }
        const payload = await resp.json().catch(() => ({}));
        window.showToast(payload.message || 'Individual episode releases staged');
        window.refreshStagedTabData();
        await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
    } catch (err) {
        btn.disabled = false;
        btn.textContent = originalText;
        window.showToast('Error: ' + err.message);
    }
}

// Inline Approve/Discard on a staged release card. Mirrors `postStagedAction`
// (staged.js): POST `redirect_to='/?tab=staged'` to the action URL, then refresh
// the staged tab and reload the open details modal so the card re-renders with
// the new staged state. Disables the clicked button and shows "…" while in
// flight.
async function inlineStagedAction(actionUrl, btn = null) {
    const originalText = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = '…';
    }
    try {
        const formData = new FormData();
        formData.append('redirect_to', '/?tab=staged');
        const response = await fetch(actionUrl, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData,
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.message || `Server error: ${response.status}`);
        }
        const data = await response.json().catch(() => ({}));
        if (window.refreshStagedTabData) await window.refreshStagedTabData();
        if (window.openRequestDetails && window.currentRequestId) {
            await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true });
        }
        window.showToast(data.message || 'Staged torrent updated');
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = originalText;
        }
        window.showToast('Error: ' + err.message);
    }
}

function captureDetailsAccordionState() {
    const state = {};
    document.querySelectorAll('#request-details-releases details[id]').forEach(function(details) {
        state[details.id] = !!details.open;
    });
    return state;
}

function restoreDetailsAccordionState(state) {
    if (!state) return;
    document.querySelectorAll('#request-details-releases details[id]').forEach(function(details) {
        if (Object.prototype.hasOwnProperty.call(state, details.id)) {
            details.open = !!state[details.id];
        }
    });
    updateTvAccordionControls();
}

function tvDetailsNodesForRequest(requestId) {
    return Array.from(document.querySelectorAll('#request-details-releases details[id^="season-details-' + requestId + '-"], #request-details-releases details[id^="episode-details-' + requestId + '-"]'));
}

function tvSeasonNodes(requestId, seasonNumber) {
    return Array.from(document.querySelectorAll('#season-details-' + requestId + '-' + seasonNumber + ', #request-details-releases details[id^="episode-details-' + requestId + '-' + seasonNumber + '-"]'));
}

function setTvAccordionNodesOpen(nodes, open) {
    nodes.forEach(function(details) { details.open = open; });
    updateTvAccordionControls();
}

function toggleTvDetailsAll(requestId) {
    var nodes = tvDetailsNodesForRequest(requestId);
    var shouldOpen = nodes.some(function(details) { return !details.open; });
    setTvAccordionNodesOpen(nodes, shouldOpen);
}

function toggleTvSeasonDetails(requestId, seasonNumber) {
    var nodes = tvSeasonNodes(requestId, seasonNumber);
    var shouldOpen = nodes.some(function(details) { return !details.open; });
    setTvAccordionNodesOpen(nodes, shouldOpen);
}

function focusTvEpisode(requestId, seasonNumber, episodeNumber) {
    if (!requestId || !seasonNumber || !episodeNumber) return;
    const seasonDetails = document.getElementById('season-details-' + requestId + '-' + seasonNumber);
    const targetDetails = document.getElementById('episode-details-' + requestId + '-' + seasonNumber + '-' + episodeNumber);
    if (!seasonDetails || !targetDetails) return;
    document.querySelectorAll('#request-details-releases details[id^="episode-details-' + requestId + '-"]').forEach(function(details) {
        details.open = details === targetDetails;
    });
    seasonDetails.open = true;
    targetDetails.open = true;
    if (targetDetails.scrollIntoView) {
        targetDetails.scrollIntoView({ block: 'nearest' });
    }
    updateTvAccordionControls();
}

function focusStagedTvScope(requestId, scope) {
    if (!scope || scope.type !== 'single_episode') return;
    focusTvEpisode(requestId, scope.season_number, scope.episode_number);
}

function parseSingleEpisodeScopeFromTitle(title) {
    const match = String(title || '').match(/S(\d{1,2})E(\d{1,2})/i);
    if (!match) return null;
    return {
        type: 'single_episode',
        season_number: Number(match[1]),
        episode_number: Number(match[2]),
    };
}

function openStagedRequestDetailsFromElement(element) {
    const requestId = Number(element?.dataset?.requestId || 0);
    if (!requestId) return;
    const focusTvScope = parseSingleEpisodeScopeFromTitle(element.dataset.title || element.textContent || '');
    window.openRequestDetails(requestId, null, { focusTvScope });
}

function updateTvAccordionControls() {
    document.querySelectorAll('[data-tv-accordion-toggle]').forEach(function(button) {
        var requestId = button.dataset.requestId;
        var nodes = button.dataset.tvAccordionToggle === 'season'
            ? tvSeasonNodes(requestId, button.dataset.seasonNumber)
            : tvDetailsNodesForRequest(requestId);
        var allOpen = nodes.length > 0 && nodes.every(function(details) { return details.open; });
        button.textContent = allOpen ? 'Collapse all' : 'Expand all';
        button.setAttribute('aria-expanded', allOpen ? 'true' : 'false');
    });
}

// Export functions to window for HTML onclick handlers
window.renderReleaseCard = renderReleaseCard;
window.renderSearchLoadingState = renderSearchLoadingState;
window.renderMovieSearchLoadingState = renderMovieSearchLoadingState;
window.renderSeasonAccordion = renderSeasonAccordion;
window.formatRelativePublishAge = formatRelativePublishAge;
window.markEpisodeAvailable = markEpisodeAvailable;
window.markSeasonAvailable = markSeasonAvailable;
window.searchSeasonPacks = searchSeasonPacks;
window.searchEpisode = searchEpisode;
window.stageRelease = stageRelease;
window.stageTopEpisodeRelease = stageTopEpisodeRelease;
window.stageIndividualEpisodes = stageIndividualEpisodes;
window.inlineStagedAction = inlineStagedAction;
window.focusTvEpisode = focusTvEpisode;
window.focusStagedTvScope = focusStagedTvScope;
window.openStagedRequestDetailsFromElement = openStagedRequestDetailsFromElement;
window.captureDetailsAccordionState = captureDetailsAccordionState;
window.restoreDetailsAccordionState = restoreDetailsAccordionState;
window.toggleTvDetailsAll = toggleTvDetailsAll;
window.toggleTvSeasonDetails = toggleTvSeasonDetails;
window.updateTvAccordionControls = updateTvAccordionControls;
