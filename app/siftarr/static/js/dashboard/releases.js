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
    // Long release titles get two wrapped lines at <lg (with a modest type
    // down-step) instead of a single truncated line, and truncate on desktop
    // where the row is wide enough.
    const RELEASE_TITLE_CLASS = 'text-[13px] lg:text-sm text-white font-medium overflow-wrap-anywhere line-clamp-2 lg:line-clamp-none lg:truncate';
    const titleHtml = stagedBadge
        ? '<div class="flex items-center gap-2 flex-wrap"><span class="' + RELEASE_TITLE_CLASS + '">' + titleText + '</span>' + stagedBadge + '</div>'
        : '<div class="' + RELEASE_TITLE_CLASS + '">' + titleText + '</div>';

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
            release.files != null ? '<span data-release-files="true">' + window.escapeHtml(String(release.files)) + ' file' + (release.files === 1 ? '' : 's') + '</span>' : '',
            release.indexer ? renderAnnotation(release.indexer, 'text-gray-500', 'data-release-indexer="true"') : '',
        ].filter(Boolean);
        metaHtml = metaParts.length ? '<div class="mt-0.5 text-[11px] lg:text-xs text-gray-400 flex items-center gap-x-1.5 gap-y-0.5 lg:gap-2 flex-wrap">' + metaParts.join('<span>·</span>') + '</div>' : '';
    }

    const coverageHtml = !rejected && options.coverageHtml ? options.coverageHtml : '';
    // `basis-[70%]` lets the action button reflow onto its own line at narrow
    // widths instead of squeezing the title down to a few characters.
    const bodyHtml = '<div class="min-w-0 flex-1 basis-[70%] lg:basis-auto">' + titleHtml + metaHtml + coverageHtml + '</div>';

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
        actionHtml = '<div class="ml-auto flex items-center gap-2 shrink-0">' +
            '<button type="button" onclick="inlineStagedAction(\'/staged/' + stagedId + '/approve\', this)" class="rounded-lg bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white">Approve</button>' +
            '<button type="button" onclick="inlineStagedAction(\'/staged/' + stagedId + '/discard\', this)" class="rounded-lg text-xs px-3 py-1.5 text-red-400 hover:text-red-300 hover:bg-red-950/40">Discard</button>' +
        '</div>';
    } else {
        const disabledAttr = disableAction ? ' disabled' : '';
        const stageAttrs = ' title="' + window.escapeHtml(disableAction ? 'No download source available' : actionTitle) + '" data-stage-url="' + window.escapeHtml(formAction) + '" data-stage-fields="' + manualDataJson + '" data-stage-scope="' + stageScopeJson + '" onclick="stageRelease(this)"';
        if (hasActiveStagedSelection && !isActiveSelection) {
            actionHtml = '<button type="button" class="ml-auto rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 hover:text-white shrink-0 tap"' + disabledAttr + stageAttrs + '>Replace</button>';
        } else if (!window.siftarrStagingModeEnabled) {
            actionHtml = '<button type="button" class="' + (rejected ? 'ml-auto rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-500 hover:text-white' : 'ml-auto rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs font-semibold text-white') + ' shrink-0 tap"' + disabledAttr + stageAttrs + '>' + (rejected ? 'Force Download' : 'Download') + '</button>';
        } else if (rejected) {
            actionHtml = '<button type="button" class="ml-auto rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-500 hover:text-white shrink-0 tap"' + disabledAttr + stageAttrs + '>Force</button>';
        } else {
            actionHtml = '<button type="button" class="ml-auto rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs font-semibold text-white shrink-0 tap"' + disabledAttr + stageAttrs + '>Stage</button>';
        }
    }

    if (bucket) {
        const outerClass = activeSelectionMode
            ? 'rounded-lg border border-gray-700/60 bg-cyan-950/20'
            : 'rounded-lg border border-gray-700/40 bg-surface-800';
        return '<div class="' + outerClass + (rejected ? ' opacity-60' : '') + ' p-2.5 flex flex-wrap items-start gap-x-3 gap-y-2 lg:flex-nowrap lg:items-center">' + scoreGutter + bodyHtml + actionHtml + '</div>';
    }

    const outerClass = activeSelectionMode
        ? 'bg-cyan-950/20 border-b border-gray-700/60'
        : 'hover:bg-surface-850/60';
    return '<li class="flex flex-wrap items-start gap-x-3 gap-y-2 px-3 py-3 lg:flex-nowrap lg:items-center lg:gap-4 lg:px-4 ' + outerClass + (rejected ? ' opacity-60' : '') + '">' + scoreGutter + bodyHtml + actionHtml + '</li>';
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

// ── Season pack helpers (shared by the inline per-season drawer and the
// "Season packs" scope tab) ──
const SEASON_PACK_ICON_SVG = '<svg class="w-3.5 h-3.5 text-brand-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/></svg>';

function isMultiSeasonRelease(release) {
    const scope = release.target_scope || {};
    const coveredSeasons = Array.isArray(release.covered_seasons) ? release.covered_seasons : [];
    return scope.type === 'multi_season_pack' || scope.type === 'complete_series' || !!release.is_complete_series || coveredSeasons.length > 1;
}

// Single source of truth for "this episode needs nothing further". The backend
// enum only has `completed`, but older payloads (and the Plex sync path) can
// still surface `available`, so both are treated as complete.
function isEpisodeComplete(episode) {
    const status = episode && episode.status;
    return status === 'available' || status === 'completed';
}

function seasonNeededCount(season) {
    return (season && season.episodes ? season.episodes : []).filter(function(ep) {
        return !isEpisodeComplete(ep);
    }).length;
}

function findDetailsSeason(requestId, seasonNumber) {
    const data = window.currentDetailsData;
    if (!data || !data.tv_info || !Array.isArray(data.tv_info.seasons)) return null;
    if (data.request && data.request.id !== requestId) return null;
    return data.tv_info.seasons.find(function(s) { return s.season_number === seasonNumber; }) || null;
}

// Coverage line for a single-season pack. The backend parser treats a season
// pack as covering the entire season (episode granularity is not parsed for
// packs), so the bar is full-width; the label compares that coverage to the
// episodes still needed (non-available/completed) in the season.
function renderPackCoverage(release, season) {
    const scope = release.target_scope || {};
    if (scope.type !== 'season_pack') return '';
    const episodes = (season && season.episodes) || [];
    const total = episodes.length;
    if (!total) return '';
    const needed = seasonNeededCount(season);
    const neededText = needed ? 'all ' + needed + ' needed' : 'none still needed';
    return '<div class="mt-1.5 flex flex-wrap items-center gap-1.5">' +
        '<div class="flex h-1.5 w-24 lg:w-40 overflow-hidden rounded-full bg-gray-700/60"><div class="bg-emerald-500" style="width:100%"></div></div>' +
        '<span class="text-[11px] text-emerald-400">' + total + '/' + total + ' episodes · ' + neededText + '</span>' +
    '</div>';
}

// Shared pack row renderer: a bucket-variant release card with an episode
// coverage line (single-season packs) or the season-coverage badge
// (multi-season / complete-series packs).
function renderPackRow(release, requestId, season) {
    const coverageHtml = isMultiSeasonRelease(release)
        ? renderCoverageBadge(release)
        : renderPackCoverage(release, season);
    return renderReleaseCard(release, requestId, { bucket: true, coverageHtml: coverageHtml });
}

const SEASON_PACK_SEARCH_BUTTON_CLASS = 'shrink-0 rounded-lg bg-brand-600 hover:bg-brand-500 px-2.5 py-1 text-[11px] font-semibold text-white';
const SEASON_PACK_EMPTY_MESSAGE = '<div class="text-gray-500 text-sm py-2">No cached season-pack results yet. Search to fetch fresh results from your indexers.</div>';

function renderSeasonPackRows(releases, requestId, season) {
    return releases.length
        ? releases.map(function(r) { return renderPackRow(r, requestId, season); }).join('')
        : SEASON_PACK_EMPTY_MESSAGE;
}

// Inline "Season packs" sub-drawer rendered as the first row inside each
// season's accordion in the "All results" scope. Brand-tinted so it stands
// apart from episode rows; the id keeps open/closed state across re-renders
// via captureDetailsAccordionState/restoreDetailsAccordionState.
function renderSeasonPacksDrawer(requestId, season, seasonPacks) {
    const seasonNumber = season.season_number;
    return '<details id="season-packs-details-' + requestId + '-' + seasonNumber + '" class="group rounded-lg border border-brand-500/25 bg-brand-600/5" ontoggle="window.updateTvAccordionControls && window.updateTvAccordionControls()">' +
        '<summary class="flex flex-wrap items-center gap-x-3 gap-y-2 lg:flex-nowrap cursor-pointer px-3 py-2 hover:bg-surface-850/60 transition-colors">' +
            '<svg class="accordion-chevron w-3.5 h-3.5 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
            SEASON_PACK_ICON_SVG +
            '<span class="text-sm text-white font-medium">Season packs</span>' +
            '<span class="text-xs text-gray-500">' + seasonPacks.length + ' cached</span>' +
            '<div class="ml-auto flex items-center gap-2 shrink-0">' +
                '<button type="button" onclick="searchSeasonPacks(' + requestId + ', ' + seasonNumber + '); event.preventDefault(); event.stopPropagation();" class="' + SEASON_PACK_SEARCH_BUTTON_CLASS + '">Search packs</button>' +
            '</div>' +
        '</summary>' +
        '<div class="px-3 pb-3 pt-2 space-y-2" data-season-pack-results="' + requestId + '-' + seasonNumber + '">' +
            renderSeasonPackRows(seasonPacks, requestId, season) +
        '</div>' +
    '</details>';
}

// Per-season group card for the "Season packs" scope tab: header row with
// pack count + needed-episode summary + a per-season "Search packs" button,
// followed by the shared pack rows.
function renderSeasonPackGroup(requestId, season, seasonPacks) {
    const seasonNumber = season.season_number;
    const total = (season.episodes || []).length;
    const needed = seasonNeededCount(season);
    const packCountText = seasonPacks.length
        ? seasonPacks.length + ' pack' + (seasonPacks.length === 1 ? '' : 's')
        : 'no cached packs';
    const headerMeta = packCountText + ' · need ' + needed + ' of ' + total + ' episodes';
    return '<div class="rounded-xl border border-gray-700/60 bg-surface-850 overflow-hidden">' +
        '<div class="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3 lg:flex-nowrap lg:px-4 border-b border-gray-700/40 bg-surface-900/40">' +
            '<span class="text-white font-medium text-sm">Season ' + seasonNumber + '</span>' +
            '<span class="text-xs text-gray-500">' + window.escapeHtml(headerMeta) + '</span>' +
            '<div class="ml-auto flex items-center gap-2">' +
                '<button type="button" onclick="searchSeasonPacks(' + requestId + ', ' + seasonNumber + ')" class="' + SEASON_PACK_SEARCH_BUTTON_CLASS + '">Search packs</button>' +
            '</div>' +
        '</div>' +
        '<div class="p-2.5 space-y-2" data-season-pack-results="' + requestId + '-' + seasonNumber + '">' +
            renderSeasonPackRows(seasonPacks, requestId, season) +
        '</div>' +
    '</div>';
}

// "Multi-season packs" group at the bottom of the "Season packs" scope tab.
function renderMultiSeasonPackGroup(requestId, multiSeasonReleases) {
    const countText = multiSeasonReleases.length
        ? multiSeasonReleases.length + ' pack' + (multiSeasonReleases.length === 1 ? '' : 's') + ' spanning multiple seasons'
        : 'no cached multi-season packs';
    const rows = multiSeasonReleases.length
        ? multiSeasonReleases.map(function(r) { return renderPackRow(r, requestId, null); }).join('')
        : '<div class="text-gray-500 text-sm py-2">No cached multi-season pack results yet. Search to fetch fresh results from your indexers.</div>';
    return '<div class="rounded-xl border border-gray-700/60 bg-surface-850 overflow-hidden">' +
        '<div class="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3 lg:flex-nowrap lg:px-4 border-b border-gray-700/40 bg-surface-900/40">' +
            '<span class="text-white font-medium text-sm">Multi-season packs</span>' +
            '<span class="text-xs text-gray-500">' + window.escapeHtml(countText) + '</span>' +
            '<div class="ml-auto flex items-center gap-2">' +
                '<button type="button" onclick="searchMultiSeasonPacks(' + requestId + ')" class="' + SEASON_PACK_SEARCH_BUTTON_CLASS + '">Search multi-season</button>' +
            '</div>' +
        '</div>' +
        '<div class="p-2.5 space-y-2" data-multi-season-pack-results="' + requestId + '">' + rows + '</div>' +
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
            const isCompleteSeries = release.is_complete_series || release.covers_all_known_seasons || relScope.type === 'complete_series';
            const releaseKey = release.stored_release_id || release.id || release.title;
            if (isMultiSeasonRelease(release) && !seenMultiSeasonReleases.has(releaseKey)) {
                seenMultiSeasonReleases.add(releaseKey);
                multiSeasonReleases.push(release);
                if (isCompleteSeries) completeSeriesReleases.push(release);
            }
        });
    });

    // Single-season packs grouped under their actual season. releases_by_season
    // buckets multi-season packs under every covered season, so filter those out
    // here — they live in the dedicated "Multi-season packs" group instead.
    const packsBySeason = {};
    let singleSeasonPackCount = 0;
    tvInfo.seasons.forEach(function(season) {
        const seasonReleases = (tvInfo.releases_by_season && tvInfo.releases_by_season[String(season.season_number)]) || [];
        const packs = seasonReleases.filter(function(release) { return !isMultiSeasonRelease(release); });
        packsBySeason[season.season_number] = packs;
        singleSeasonPackCount += packs.length;
    });
    const totalPackCount = singleSeasonPackCount + multiSeasonReleases.length;

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
        renderScopeChip(requestId, 'season_packs', 'Season packs', totalPackCount, scope) +
        renderScopeChip(requestId, 'complete_series', 'Complete series', completeSeriesReleases.length, scope) +
        '<span class="basis-full text-xs text-gray-500 lg:basis-auto lg:ml-auto">' + window.escapeHtml(aggregateText) + '</span>' +
    '</div>';

    // ── Section: All results (2-level Season → Episode accordion) ──
    const panelToggle = '<div class="flex justify-end">' + renderTvAccordionToggle('panel', requestId) + '</div>';

    const seasonAccordion = tvInfo.seasons.map(function(season) {
        const seasonKey = String(season.season_number);
        const seasonBadgeClass = episodeStatusBadge(season.status);
        const seasonPacks = packsBySeason[season.season_number] || [];
        const packsChip = seasonPacks.length
            ? '<span class="inline-flex items-center gap-1 rounded-full border border-brand-500/30 bg-brand-600/10 px-2 py-0.5 text-[11px] text-brand-300">' + seasonPacks.length + ' pack' + (seasonPacks.length === 1 ? '' : 's') + '</span>'
            : '';
        const hasMarkable = (season.episodes || []).some(function(ep) { return !isEpisodeComplete(ep); });
        const seasonHasStaged = (season.episodes || []).some(function(ep) { return ep.status === 'staged'; });

        const summaryBits = [season.available_count + '/' + season.total_count + ' available'];
        if (season.staged_count) summaryBits.push(season.staged_count + ' staged');
        if (season.pending_count) summaryBits.push(season.pending_count + ' pending');
        if (season.unreleased_count) summaryBits.push(season.unreleased_count + ' unreleased');
        const availableText = summaryBits.join(' \u00B7 ');

        const seasonLinks = '<div class="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-xs">' +
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
            const isComplete = isEpisodeComplete(ep);
            // Completed episodes collapse by default even when they have cached
            // releases — there is nothing left to act on. They remain manually
            // expandable, and captureDetailsAccordionState/restoreDetailsAccordionState
            // keeps a manual expansion across re-renders.
            const isOpen = !isComplete && (episodeReleases.length > 0 || isStaged);
            const episodeBucketHtml = episodeReleases.length
                ? episodeReleases.map(function(r) { return renderReleaseCard(r, requestId, { bucket: true }); }).join('')
                : '<div class="text-gray-500 text-sm py-2">No cached episode results yet. Search for new checks missing aired episodes; Full search refreshes all aired episode results.</div>';
            const topRelease = episodeReleases.find(isUsableCachedRelease);
            const showInlineActions = !isComplete;

            return '<details id="' + episodeDetailsId + '" class="group rounded-lg border ' + (isStaged ? 'border-gray-700/60 bg-cyan-950/10' : 'border-gray-700/40 bg-surface-900/50') + '" ontoggle="window.updateTvAccordionControls && window.updateTvAccordionControls()"' + (isOpen ? ' open' : '') + '>' +
                '<summary class="flex flex-wrap items-center gap-x-3 gap-y-1.5 lg:flex-nowrap cursor-pointer px-3 py-2 hover:bg-surface-850/60 transition-colors">' +
                    '<svg class="accordion-chevron w-3.5 h-3.5 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
                    '<span class="text-xs font-mono text-gray-500 shrink-0">S' + String(season.season_number).padStart(2, '0') + 'E' + String(ep.episode_number).padStart(2, '0') + '</span>' +
                    '<span class="min-w-0 flex-1 basis-[55%] text-[13px] lg:text-sm text-white truncate lg:basis-auto">' + window.escapeHtml(ep.title || 'Untitled') + '</span>' +
                    '<div class="ml-auto flex flex-wrap items-center justify-end gap-2 shrink-0">' +
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
            '<summary class="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3 lg:flex-nowrap lg:px-4 cursor-pointer hover:bg-surface-800 transition-colors">' +
                '<svg class="accordion-chevron w-4 h-4 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>' +
                '<span class="text-white font-medium text-sm">Season ' + season.season_number + '</span>' +
                '<span class="text-xs text-gray-500">' + window.escapeHtml(availableText) + '</span>' +
                '<span class="badge ' + seasonBadgeClass + '">' + window.escapeHtml(season.status || 'unknown') + '</span>' +
                packsChip +
                seasonLinks +
            '</summary>' +
            '<div class="px-4 pb-4 space-y-2 border-t border-gray-700/40 pt-3">' + renderSeasonPacksDrawer(requestId, season, seasonPacks) + episodeHtml + '</div>' +
        '</details>';
    }).join('');

    const allSection = '<div class="space-y-3"' + (scope === 'all' ? '' : ' hidden') + '>' + panelToggle + seasonAccordion + '</div>';

    // ── Section: Season packs (per-season groups + multi-season group) ──
    const packsHelperRow = '<div class="flex flex-wrap items-center justify-between gap-2">' +
        '<span class="text-xs text-gray-500">Packs grouped by season. Coverage compares pack contents to episodes you still need.</span>' +
        '<button type="button" onclick="searchAllSeasonPacks(' + requestId + ', this)" class="rounded-md border border-gray-600/80 bg-surface-900/70 px-2.5 py-1 text-xs font-medium text-gray-200 transition-colors hover:border-brand-400/70 hover:bg-surface-800 hover:text-white">Search all seasons</button>' +
    '</div>';
    const seasonPackGroups = tvInfo.seasons.map(function(season) {
        return renderSeasonPackGroup(requestId, season, packsBySeason[season.season_number] || []);
    }).join('');
    const seasonPacksSection = '<div id="scope-season-packs-' + requestId + '" class="space-y-3"' + (scope === 'season_packs' ? '' : ' hidden') + '>' +
        packsHelperRow +
        seasonPackGroups +
        renderMultiSeasonPackGroup(requestId, multiSeasonReleases) +
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

// Stream a per-season pack search into every pack container for that season —
// the inline drawer in the "All results" scope and the season group in the
// "Season packs" scope share the same data attribute. Results are persisted
// server-side, so on completion the details modal reloads (preserving UI
// state) to rebuild counts, chips, and groups from the cached payload.
function searchSeasonPacks(requestId, seasonNumber) {
    return new Promise(function(resolve) {
        var containers = document.querySelectorAll('[data-season-pack-results="' + requestId + '-' + seasonNumber + '"]');
        if (!containers.length) return resolve();
        var drawer = document.getElementById('season-packs-details-' + requestId + '-' + seasonNumber);
        if (drawer) drawer.open = true;
        containers.forEach(function(c) { c.innerHTML = renderSearchLoadingState('Searching season packs...'); });
        window.startTvSearchProgress('/requests/' + requestId + '/seasons/' + seasonNumber + '/season-packs/search/stream', 'Season ' + seasonNumber + ' packs', async function(data) {
            var releases = (data.releases || []).filter(function(r) { return !isMultiSeasonRelease(r); });
            var season = findDetailsSeason(requestId, seasonNumber);
            var rows = releases.length
                ? releases.map(function(r) { return renderPackRow(r, requestId, season); }).join('')
                : '<div class="text-gray-500 text-sm py-2">No season pack results found.</div>';
            containers.forEach(function(c) { c.innerHTML = rows; });
            await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
            resolve();
        }, function() { resolve(); });
    });
}

// Stream a multi-season pack search into the "Multi-season packs" group in the
// "Season packs" scope tab.
function searchMultiSeasonPacks(requestId) {
    return new Promise(function(resolve) {
        var containers = document.querySelectorAll('[data-multi-season-pack-results="' + requestId + '"]');
        if (!containers.length) return resolve();
        containers.forEach(function(c) { c.innerHTML = renderSearchLoadingState('Searching multi-season packs...'); });
        window.startTvSearchProgress('/requests/' + requestId + '/multi-season-packs/search/stream', 'Multi-season packs', async function(data) {
            var releases = data.releases || [];
            var rows = releases.length
                ? releases.map(function(r) { return renderPackRow(r, requestId, null); }).join('')
                : '<div class="text-gray-500 text-sm py-2">No multi-season or complete-series results found.</div>';
            containers.forEach(function(c) { c.innerHTML = rows; });
            await window.openRequestDetails(requestId, window.currentDetailsIndex, { preserveUiState: true });
            resolve();
        }, function() { resolve(); });
    });
}

// "Search all seasons" in the Season packs tab: run the per-season pack
// searches sequentially (each one streams progress and reloads the modal).
async function searchAllSeasonPacks(requestId, btn = null) {
    var data = window.currentDetailsData;
    var seasons = (data && data.tv_info && data.tv_info.seasons) || [];
    if (!seasons.length) return;
    var originalText = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Searching…';
    }
    try {
        for (var i = 0; i < seasons.length; i++) {
            await searchSeasonPacks(requestId, seasons[i].season_number);
        }
    } finally {
        // The modal re-renders after each search, so the original button node is
        // usually gone; only restore it if it is still attached.
        if (btn && btn.isConnected) {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }
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
    return Array.from(document.querySelectorAll('#request-details-releases details[id^="season-details-' + requestId + '-"], #request-details-releases details[id^="episode-details-' + requestId + '-"], #request-details-releases details[id^="season-packs-details-' + requestId + '-"]'));
}

function tvSeasonNodes(requestId, seasonNumber) {
    return Array.from(document.querySelectorAll('#season-details-' + requestId + '-' + seasonNumber + ', #season-packs-details-' + requestId + '-' + seasonNumber + ', #request-details-releases details[id^="episode-details-' + requestId + '-' + seasonNumber + '-"]'));
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
window.searchMultiSeasonPacks = searchMultiSeasonPacks;
window.searchAllSeasonPacks = searchAllSeasonPacks;
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
