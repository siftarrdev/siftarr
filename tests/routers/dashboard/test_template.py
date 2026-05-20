"""Template assertions for dashboard UI."""

import os


def _read_dashboard_js():
    """Read all dashboard JS module files and return combined content."""
    js_dir = os.path.join(os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard")
    content = ""
    for filename in os.listdir(js_dir):
        if filename.endswith(".js"):
            filepath = os.path.join(js_dir, filename)
            with open(filepath, encoding="utf-8") as handle:
                content += handle.read() + "\n"
    return content


def _read_dashboard_css():
    """Read dashboard CSS file."""
    css_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/css/dashboard.css"
    )
    with open(css_path, encoding="utf-8") as handle:
        return handle.read()


def _read_dashboard_entry_js():
    js_path = os.path.join(os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard.js")
    with open(js_path, encoding="utf-8") as handle:
        return handle.read()


def test_dashboard_template_loads_external_assets(dashboard_template_path):
    """Dashboard template should load external CSS and JS files."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "url_for('static', path='/css/dashboard.css') }}?v={{ static_version }}" in template
    assert "url_for('static', path='/js/dashboard.js') }}?v={{ static_version }}" in template
    assert "window.siftarrStaticVersion = {{ static_version | tojson }};" in template
    assert 'type="module"' in template


def test_dashboard_entry_cache_busts_imported_modules():
    """ES module children need their own version query, not just the entry file."""
    js = _read_dashboard_entry_js()

    assert "window.siftarrStaticVersion" in js
    assert "await import(`./dashboard/details.js?v=${moduleVersion}`);" in js
    assert "await import(`./dashboard/search_sse.js?v=${moduleVersion}`);" in js


def test_dashboard_css_contains_resize_styles():
    """Dashboard CSS should contain resize handle styles."""
    css = _read_dashboard_css()

    assert ".resize-handle" in css
    assert "cursor: col-resize" in css
    assert "table.data-resizable" in css
    assert ".accordion-chevron" in css
    assert ".dashboard-search-loading" in css


def test_dashboard_js_uses_shared_search_loading_state():
    """Torrent searches should share one accessible loading indicator."""
    js = _read_dashboard_js()

    assert "function renderSearchLoadingState(message)" in js
    assert "function renderMovieSearchLoadingState()" in js
    assert 'role="status" aria-live="polite"' in js
    assert "window.renderSearchLoadingState = renderSearchLoadingState;" in js
    assert "window.renderMovieSearchLoadingState = renderMovieSearchLoadingState;" in js
    assert "function showSearchProgressToast(" in js
    assert "window.showSearchProgressToast = showSearchProgressToast;" in js
    assert "search-progress-toast" in js
    assert "document.createElement('div')" in js
    assert js.count("window.escapeHtml = escapeHtml;") == 1
    assert "Search for new checks missing aired episodes" in js
    assert "window.renderMovieSearchLoadingState()" in js
    assert "Searching movie torrents" in js
    assert "Checking indexers now" in js


def test_dashboard_tv_details_use_new_and_full_search_actions():
    """Dashboard TV search UI should expose Search for new and Full search actions."""
    with open(
        os.path.join(os.path.dirname(__file__), "../../../app/siftarr/templates/dashboard.html"),
        encoding="utf-8",
    ) as handle:
        template = handle.read()

    assert 'id="request-details-tv-search-btn" onclick="searchTvRequestNew()"' in template
    assert 'id="request-details-tv-full-search-btn" onclick="searchTvRequestFull()"' in template
    assert "Search for new" in template
    assert "Full search" in template
    assert "Search Scope" not in template
    assert "TV Search Scope" not in template
    assert "Search All Pending Episodes" not in template
    assert "Search Multi-Season Packs" not in template
    assert "toggleTvSearchScopeMenu(event)" not in template


def test_dashboard_js_includes_read_only_tv_buckets():
    """Dashboard JS should show read-only TV buckets filled by Search All."""
    js = _read_dashboard_js()

    assert "Season packs and complete series" in js
    assert "Larger releases that may cover more than one requested season" in js
    assert "Full search refreshes all aired episode and pack results." in js
    assert "No cached episode results yet" in js


def test_dashboard_js_includes_tv_details_expand_collapse_controls():
    js = _read_dashboard_js()

    shared_toggle_class = (
        "tv-accordion-toggle inline-flex items-center justify-center rounded-md border "
        "border-gray-600/80 bg-surface-900/70 px-2.5 py-1 text-xs font-medium "
        "leading-4 text-gray-200 shadow-sm transition-colors hover:border-brand-400/70 "
        "hover:bg-surface-800 hover:text-white focus:outline-none focus:ring-2 "
        "focus:ring-brand-400/60 focus:ring-offset-2 focus:ring-offset-surface-900"
    )

    assert 'data-tv-accordion-toggle="panel"' in js
    assert 'data-tv-accordion-toggle="season"' in js
    assert "const TV_ACCORDION_TOGGLE_CLASS" in js
    assert shared_toggle_class in js
    assert "function renderTvAccordionToggle(scope, requestId, seasonNumber = null)" in js
    assert "renderTvAccordionToggle('panel', requestId)" in js
    assert "renderTvAccordionToggle('season', requestId, season.season_number)" in js
    assert "toggleTvDetailsAll" in js
    assert "toggleTvSeasonDetails" in js
    assert "updateTvAccordionControls" in js
    assert "aria-expanded" in js
    assert "button.textContent = allOpen ? 'Collapse all' : 'Expand all';" in js
    assert "event.preventDefault(); event.stopPropagation(); toggleTvSeasonDetails" in js
    assert "No cached season-pack results yet" in js
    assert "function searchTvRequestNew()" in js
    assert "function searchTvRequestFull()" in js
    assert "search/stream?search_mode=' + encodeURIComponent(fullSearch ? 'full' : 'new')" in js
    assert "window.startTvSearchProgress(streamUrl, modeLabel + ': ' + detailsTitle" in js
    assert "function searchRequestFromDetails()" in js
    assert (
        "window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true })"
        in js
    )
    assert "Search Season Packs" not in js
    assert "Search Multi Season Packs" not in js


def test_dashboard_details_auto_search_and_reload_hooks():
    """Details modal should auto-search empty caches and reload after SSE completion."""
    js = _read_dashboard_js()

    assert "window.detailsAutoSearchStarted = window.detailsAutoSearchStarted || {};" in js
    assert "delete window.detailsAutoSearchStarted[requestId];" in js
    assert "if (data.auto_search_eligible && !window.detailsAutoSearchStarted[requestId])" in js
    assert "window.searchTvRequestNew({ auto: true });" in js
    assert "window.searchRequestFromDetails({ auto: true });" in js
    assert "if (!data || data.reload_details !== false)" in js
    assert (
        "await window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true });"
        in js
    )


def test_dashboard_details_search_sets_progress_and_restores_button():
    """Movie details searches should show progress and restore controls."""
    js = _read_dashboard_js()
    css = _read_dashboard_css()

    assert "releasesContainer.innerHTML = window.renderMovieSearchLoadingState();" in js
    assert ".dashboard-details-search-loading" in css
    assert "border: 1px solid rgba(59, 130, 246, 0.3)" in css
    assert "btn.disabled = true;" in js
    assert "btn.textContent = 'Searching...';" in js
    assert "window.startSearchProgress(" in js
    assert "/requests/' + window.currentRequestId + '/search/results" not in js
    assert "No cached search results yet. Use Refresh Search to search indexers." in js
    assert "skipAutoSearch" not in js
    assert "if (!skipAutoSearch)" not in js
    assert "searchRequestFromDetails();" not in js
    assert (
        "window.openRequestDetails(window.currentRequestId, window.currentDetailsIndex, { preserveUiState: true })"
        in js
    )
    assert "btn.innerHTML = originalText || 'Refresh Search';" in js
    assert "cacheInd.classList.add('hidden');" in js
    assert (
        "window.startSearchProgress(window.currentRequestId, detailsTitle, async function()" in js
    )


def test_dashboard_details_sort_controls_reorder_locally_without_reload():
    """Sort-only details controls should avoid refetching the whole modal."""
    js = _read_dashboard_js()

    assert "function applyLocalReleaseSort()" in js
    assert "controls.sort = sortSelect.value;\n        applyLocalReleaseSort();" in js
    assert "applyDetailsControls(controls);\n        applyLocalReleaseSort();" in js
    assert "controls.sort = sortSelect.value;\n        reloadDetailsWithControls();" not in js
    assert "applyDetailsControls(controls);\n        reloadDetailsWithControls();" not in js


def test_dashboard_template_search_actions_use_progress_helpers(dashboard_template_path):
    """Search actions should opt into progress helpers without changing deny actions."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    assert 'onsubmit="return handleBulkRequestActionSubmit(event, this)"' in template
    assert 'name="action" value="search" data-search-submit-control="true"' in template
    assert 'name="action" value="search_all_pending" data-search-submit-control="true"' in template
    assert "Search All" in template
    assert "postToAction('/requests/{{ req.id }}/search', '/?tab=pending', this)" in template
    assert 'data-search-action="true"' in template
    assert "openDenyModal({{ req.id }}, '/?tab=pending')" in template
    assert "handleBulkRequestActionSubmit(event, form)" in js
    assert "event.preventDefault();" in js
    assert "window.startSearchProgress(" in js
    assert "window.startBulkSearchProgress(" in js
    assert "window.updateRequestRow(" in js
    assert "collectBulkSearchTitles(form, searchAll)" in js
    assert "getRequestTitleFromRow(row)" in js
    assert "if (form.dataset.searchSubmitting === 'true') return false;" in js
    assert "form.dataset.searchSubmitting = 'true';" in js
    assert "submitter.dataset.searchSubmitControl !== 'true'" in js
    assert 'input[name="request_ids"]:checked, input[name="torrent_ids"]:checked' in js
    assert "actionInput.name = 'action';" in js
    assert "actionInput.value = submitter.value;" in js
    assert "setSearchActionLoading(trigger" in js
    assert "disableSearchControls(form);" in js


def test_dashboard_template_removes_all_requests_and_conditionally_shows_staging(
    dashboard_template_path,
):
    """Dashboard tabs should omit All Requests and gate staging navigation."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    assert "All Requests" not in template
    assert "content-active" not in template
    assert "tab-active" not in template
    assert "showTab('active')" not in template
    assert "if staging_mode_enabled" in template
    assert "showTab('pending');" in js


def test_dashboard_template_deny_actions_use_modal_and_bulk_path(dashboard_template_path):
    """Pending single deny uses the modal while bulk deny stays separate."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    assert "openDenyModal({{ req.id }}, '/?tab=active')" not in template
    assert "openDenyModal({{ req.id }}, '/?tab=pending')" in template
    assert 'id="deny-submit-btn" onclick="submitDenyRequest()"' in template
    assert 'name="action" value="deny" class="btn-danger btn-sm"' in template
    assert "if (submitter.value === 'deny')" in js
    assert "return handleBulkDenyAction(event, form);" in js
    assert "submitter.dataset.searchSubmitControl !== 'true'" in js
    assert "window.startBulkSearchProgress(" in js
    assert (
        'data-search-submit-control="true"'
        not in template.split('name="action" value="deny"', 1)[1].split(">", 1)[0]
    )


def test_dashboard_modals_exports_inline_handler_names():
    """Inline deny handlers should match exported modal functions."""
    js_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard/modals.js"
    )
    with open(js_path, encoding="utf-8") as handle:
        js = handle.read()

    for name in ("openDenyModal", "submitDenyRequest", "closeDenyModal"):
        assert f"function {name}(" in js
        assert f"window.{name} = {name};" in js
    assert "headers: { 'Accept': 'application/json' }" in js
    assert "await window.refreshCurrentTabContent();" in js
    assert "window.bindDenyModalHandlers = bindDenyModalHandlers;" in js
    assert "toggle.dataset.selectAllBound === 'true'" in js


def test_dashboard_js_uses_collapsible_episode_results():
    """Episode cached results should live in their own collapsible sections."""
    js = _read_dashboard_js()

    assert "episode-details-" in js
    assert '<details id="\' + episodeDetailsId + \'" class="group rounded-lg border' in js
    assert "No cached episode results yet" in js


def test_dashboard_js_includes_release_status_column_and_upload_age():
    """Torrent cards should render a right-side status area with rejection reason and age."""
    js = _read_dashboard_js()

    assert 'data-release-status-column="true"' in js
    assert 'data-release-rejection-reason="true"' in js
    assert 'data-release-upload-age="true"' in js
    assert 'data-release-size-per-season="true"' in js
    assert 'data-release-resolution="true"' in js
    assert 'data-release-codec="true"' in js
    assert "function formatRelativePublishAge(publishDate)" in js
    assert "window.siftarrStagingModeEnabled" in js
    assert "/manual-release/use" in js
    assert "background refresh updates Plex/Overseerr data" not in js
    assert "Plex episode availability is being resolved for partial seasons" not in js


def test_dashboard_js_supports_annotation_highlighting():
    """Torrent annotation highlighting helpers should exist in the JS."""
    js = _read_dashboard_js()

    assert "function renderAnnotation(" in js
    assert "function releaseAnnotationTone(" in js


def test_dashboard_js_includes_active_stage_replacement_copy():
    """Request details should explain replacement semantics for staged picks."""
    js = _read_dashboard_js()

    assert "request-details-active-stage-banner" in js
    assert "Replace staged" in js
    assert "Stage release" in js
    assert "Stage this torrent for review and approval." in js
    assert "Selecting another result will replace it." in js
    assert "text-emerald-400" in js
    assert "text-red-400" in js


def test_dashboard_js_uses_per_release_conflict_metadata_for_stage_buttons():
    """Release cards should not infer replacement from stale request-wide staged state."""
    js = _read_dashboard_js()

    assert "const releaseScope = release.target_scope || {};" in js
    assert "const isScopedEpisodeRelease = releaseScope.type === 'single_episode';" in js
    assert "const activeStagedTorrent = release.active_staged_torrent || null;" in js
    assert "const conflictsActiveSelection = !!release.conflicts_active_selection;" in js


def test_dashboard_js_uses_cyan_staged_release_indicators():
    """Staged releases and TV status badges should use cyan styling."""
    js = _read_dashboard_js()

    assert "'staged': 'badge-cyan'" in js
    assert "'badge-blue' : 'badge-cyan'" in js
    assert "border-cyan-500/70 bg-cyan-950/20" in js


def test_dashboard_js_collapses_staged_tv_scope_after_reload():
    """TV scoped staging should remember and collapse the matching accordion."""
    js = _read_dashboard_js()

    assert "data-stage-scope" in js
    assert "{ preserveUiState: true }" in js
    assert "function captureDetailsAccordionState()" in js
    assert "function restoreDetailsAccordionState(state)" in js
    assert "function collapseStagedTvScope(requestId, scope)" in js
    assert "episode-details-' + requestId + '-' + scope.season_number" in js
    assert "season-details-' + requestId + '-' + seasons[0]" in js
    assert "collapseStagedTvScope(window.currentRequestId, stagedScope);" in js


def test_dashboard_js_removes_scope_menu_helpers():
    """Normal TV details UI should not expose scope-menu search controls."""
    js = _read_dashboard_js()

    assert "tv-search-scope-menu" not in js
    assert "tv-search-scope-seasons" not in js
    assert "function toggleTvSearchScopeMenu(event)" not in js
    assert "function closeTvSearchScopeMenu()" not in js
    assert "function populateTvSearchScopeMenu()" not in js
    assert "tv-search-dropdown" not in js
    assert (
        "!isScopedEpisodeRelease && hasActiveStagedSelection && activeStagedTorrent && release.title === activeStagedTorrent.title"
        in js
    )


def test_dashboard_js_refreshes_full_staged_content():
    """Staged refresh should replace the whole section so empty states can appear."""
    js = _read_dashboard_js()

    assert "const stagedContent = document.getElementById('content-staged');" in js
    assert "const newContent = doc.getElementById('content-staged');" in js
    assert "stagedContent.innerHTML = newContent.innerHTML;" in js
    assert "const downloadingContent = document.getElementById('content-downloading');" in js
    assert "const newContent = doc.getElementById('content-downloading');" in js
    assert "downloadingContent.innerHTML = newContent.innerHTML;" in js
    assert "#downloading-torrents-body tr, #downloading-torrent-cards [data-torrent-id]" in js
    assert "[data-dashboard-stat-cards]" in js


def test_dashboard_template_has_mobile_staged_downloading_cards(dashboard_template_path):
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    assert "data-dashboard-stat-cards" in template
    assert "gap-2 sm:gap-3" in template
    assert "px-3 py-2 sm:px-4 sm:py-3" in template
    assert 'id="staged-torrent-cards" class="space-y-3 p-3 md:hidden"' in template
    assert 'id="downloading-torrent-cards" class="space-y-3 p-3 md:hidden"' in template
    assert 'class="hidden md:block {% if staged_torrents %}overflow-x-auto{% endif %}"' in template
    assert (
        'class="hidden md:block {% if downloading_torrents %}overflow-x-auto{% endif %}"'
        in template
    )
    assert "btn-success btn-sm w-full" in template
    assert "btn-danger btn-sm w-full" in template
    assert "btn-primary btn-sm w-full" in template
    assert "#staged-torrents-body tr, #staged-torrent-cards [data-torrent-id]" in js


def test_dashboard_template_splits_staged_and_downloading_tabs(dashboard_template_path):
    """Staged review controls and downloading qBittorrent controls live separately."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "Staged / Downloading" not in template
    assert 'id="tab-staged"' in template
    assert 'id="tab-downloading"' in template
    assert 'id="content-staged"' in template
    assert 'id="content-downloading"' in template
    assert 'id="downloading-torrents-body"' in template
    assert "data-download-progress" in template
    assert "data-download-eta" in template
    assert "data-qbit-finished-waiting-plex" in template
    assert "qBittorrent finished; waiting for Plex" in template
    assert "RAR-packed or otherwise unimportable" in template
    assert "Open qBittorrent" in template
    assert "torrent.id in replace_staged_torrent_ids" in template
    assert "openReplaceModal({{ torrent.id }}" in template
    assert (
        "openReplaceModal({{ torrent.id }}, {{ torrent.request_id }}"
        not in template[
            template.index('id="content-downloading"') : template.index(
                "{# ═══════════════ FINISHED TAB"
            )
        ]
    )
    assert "/?tab=downloading" in template


def test_dashboard_template_updates_pending_and_unreleased_columns(dashboard_template_path):
    """Pending and unreleased tables expose the new sort columns."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()
    entry_js = _read_dashboard_entry_js()

    assert "Requested On" in template
    assert 'data-sort="status"' in template
    assert "Next Retry" not in template
    assert "Last Error" not in template
    assert "Release Date" in template
    assert 'data-sort="releasedate"' in template
    assert "window.sortTable('unreleased', 'releasedate');" in entry_js
    assert "downloading: 'downloading-torrents-table'" in js
    assert "function filterDownloadingTable()" in js
    assert "row.dataset.releasedate" in js
    assert "row.dataset.expected" not in js
    assert "event.target?.id === 'downloading-filter-input'" in entry_js


def test_dashboard_column_sorting_uses_full_header_cell_and_numeric_year():
    """Dashboard table sorting should work from the full header cell."""
    js = _read_dashboard_js()
    entry_js = _read_dashboard_entry_js()

    assert "th[data-table][data-sort]" in entry_js
    assert "e.target.closest('.resize-handle')" in entry_js
    assert "window.sortTable(th.dataset.table, sortKey);" in entry_js
    assert "'year'" in js


def test_dashboard_details_navigation_uses_visible_filtered_rows():
    """Details previous/next navigation should follow only displayed rows in the current tab."""
    js = _read_dashboard_js()

    assert "document.querySelector('.tab-content:not(.hidden)')" in js
    assert "row.style.display !== 'none'" in js
    assert "function refreshDetailsNavigationContext()" in js
    assert "window.visibleRequests = window.getVisibleRequests();" in js
    assert "findIndex(r => r.id === window.currentRequestId)" in js
    assert "window.refreshDetailsNavigationContext();" in js


def test_dashboard_active_unreleased_toggle_removed_and_filters_refresh_navigation(
    dashboard_template_path,
):
    """Active no longer exposes unreleased rows, so the legacy toggle is removed."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    assert "show-unreleased-toggle" not in template
    assert "Show Unreleased" not in template
    assert "toggleShowUnreleased" not in js
    assert "showUnreleasedActive" not in js
    assert "unreleasedMatch" not in js
    assert "row.style.display = (textMatch && mediaMatch) ? '' : 'none';" in js
    assert "window.refreshDetailsNavigationContext();" in js
    assert "rows.forEach(row => tbody.appendChild(row));" in js
    assert "if (card) cardContainer.appendChild(card);" in js
