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


def _compact_js(content: str) -> str:
    """Normalize formatter-only whitespace for source-level behavior assertions."""
    return " ".join(content.split())


def _assert_window_facade_exports(content: str, *names: str) -> None:
    """Assert names remain available through a temporary window facade."""
    facade_blocks = content.split("Object.assign(window, {")[1:]
    for name in names:
        assert any(f"{name}," in block.split("});", 1)[0] for block in facade_blocks)


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


def test_torrent_status_shows_live_download_totals(dashboard_template_path):
    """The active queue displays aggregate transfer metrics from the poll payload."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert 'id="qbit-download-summary"' in template
    assert 'aria-live="polite"' in template
    js_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard/staged.js"
    )
    with open(js_path, encoding="utf-8") as handle:
        js = handle.read()
    assert "export function renderQbitDownloadSummary" in js


def test_dashboard_entry_cache_busts_imported_modules():
    """The import map versions every static ES module dependency."""
    js = _read_dashboard_entry_js()
    template_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/templates/dashboard.html"
    )
    with open(template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "import '/static/js/dashboard/details.js';" in js
    assert "import '/static/js/dashboard/search_sse.js';" in js
    assert '<script type="importmap">' in template
    assert '"/static/js/dashboard/details.js"' in template
    assert "dashboard/details.js') }}?v={{ static_version }}" in template
    assert '"/static/js/dashboard/core/state.js"' in template


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
    _assert_window_facade_exports(js, "renderSearchLoadingState", "renderMovieSearchLoadingState")
    assert "function showSearchProgressToast(" in js
    assert "showSearchProgressToast," in js
    assert "search-progress-toast" in js
    assert "document.createElement('div')" in js
    _assert_window_facade_exports(js, "escapeHtml")
    assert "Search for new checks missing aired episodes" in js
    assert "window.renderMovieSearchLoadingState()" in js
    assert "Searching movie torrents" in js
    assert "Checking indexers now" in js


def test_dashboard_js_highlights_zero_seeders():
    """Zero-seeder warnings should be bold and red in release views."""
    js = _read_dashboard_js()

    assert js.count("font-bold text-red-400") >= 2
    assert "Number(release.seeders) === 0" in js
    assert "item.seeders != null && Number(item.seeders) === 0" in js


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

    # Scope chips replace the old "Season packs and complete series" drawer.
    assert "Show:" in js
    assert "All results" in js
    assert "Season packs" in js
    assert "Complete series" in js
    assert "setDetailsScope" in js
    assert "scope-season-packs-" in js
    assert "scope-complete-series-" in js
    assert "Search complete series to fetch fresh results from your indexers." in js
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
    assert "const TV_ACCORDION_TOGGLE_CLASS" in js
    assert shared_toggle_class in js
    assert "function renderTvAccordionToggle(scope, requestId, seasonNumber = null)" in js
    assert "renderTvAccordionToggle('panel', requestId)" in js
    assert "toggleTvDetailsAll" in js
    assert "toggleTvSeasonDetails" in js
    assert "updateTvAccordionControls" in js
    assert "aria-expanded" in js
    assert "button.textContent = allOpen ? 'Collapse all' : 'Expand all';" in js
    assert "No cached season-pack results yet" in js
    # Season → Episode details plus the per-season "Season packs" sub-drawer.
    assert "season-details-" in js
    assert "episode-details-" in js
    assert "season-packs-details-" in js
    assert "season-packs-all-details-" not in js
    # Season packs scope tab: per-season groups plus a multi-season group.
    assert "data-season-pack-results=" in js
    assert "data-multi-season-pack-results=" in js
    assert "function searchAllSeasonPacks(requestId" in js
    assert "multi-season-packs/search/stream" in js
    # Multi-season packs are searchable and manageable from the pack tabs.
    assert "function stagedMultiSeasonPacks(stagedTorrents)" in js
    assert "renderMultiSeasonPackGroup(requestId, multiSeasonReleases, stagedMultiSeason)" in js
    assert ">Search multi-season</button>" in js
    assert ">Search complete series</button>" in js
    assert "await searchMultiSeasonPacks(requestId);" in js
    # Season-row controls: quiet management links plus a scoped icon search.
    assert "Mark all" in js
    assert "Stage individual episodes" in js
    assert "Search Season " in js
    assert "searchSeasonPacks(" in js
    # Scope chips switch client-side without a backend reload.
    assert "function setDetailsScope(requestId, scope)" in js
    _assert_window_facade_exports(js, "setDetailsScope")
    assert "scope: 'all'" in js
    assert "function searchTvRequestNew(options = {})" in js
    assert "function searchTvRequestFull(options = {})" in js
    assert "search/stream?search_mode=' + encodeURIComponent(fullSearch ? 'full' : 'new')" in js
    assert "window.startTvSearchProgress(" in js
    assert "streamUrl" in js and "modeLabel + ': ' + detailsTitle" in js
    assert "function searchRequestFromDetails()" in js
    assert (
        "openRequestDetails(dashboardState.currentRequestId, dashboardState.currentDetailsIndex, { preserveUiState: true, })"
        in _compact_js(js)
    )
    assert "Search Season Packs" not in js
    assert "Search Multi Season Packs" not in js


def test_dashboard_details_auto_search_and_reload_hooks():
    """Details modal should retain TV details while auto-searching empty caches."""
    js = _read_dashboard_js()

    assert "const detailsAutoSearchStarted = window.detailsAutoSearchStarted || {};" in js
    assert "delete detailsAutoSearchStarted[requestId];" in js
    assert "if (data.auto_search_eligible && !detailsAutoSearchStarted[requestId])" in js
    assert "searchTvRequestNew({ auto: true });" in js
    assert "searchRequestFromDetails({ auto: true });" in js
    _assert_window_facade_exports(js, "detailsAutoSearchStarted")
    assert (
        "releases.innerHTML = '<div class=\"text-gray-500 text-sm\">Searching indexers for new TV results...</div>';"
        not in js
    )
    assert "if (!data || data.reload_details !== false)" in js
    assert "await reloadOpenDetailsIfActive(requestId);" in js


def test_dashboard_live_result_refresh_is_debounced_and_modal_safe():
    """SSE result batches reuse the details API, rather than polling or rendering payloads."""
    js = _read_dashboard_js()

    assert "function scheduleLiveDetailsRefresh(requestId)" in js
    assert "setTimeout(async function ()" in js
    assert "}, 1250);" in js
    assert "if (state.timer || state.inFlight) return;" in js
    assert "window.activeDetailsRequestId !== requestId" in js
    _assert_window_facade_exports(js, "cancelLiveDetailsRefresh", "scheduleLiveDetailsRefresh")
    assert "case 'results_updated':" in js
    assert "window.scheduleLiveDetailsRefresh(data.request_id);" in js


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
        "openRequestDetails(dashboardState.currentRequestId, dashboardState.currentDetailsIndex, { preserveUiState: true, })"
        in _compact_js(js)
    )
    assert "btn.innerHTML = originalText || 'Refresh Search';" in js
    assert "cacheInd.classList.add('hidden');" in js
    assert (
        "window.startSearchProgress(dashboardState.currentRequestId, detailsTitle, async function ()"
        in _compact_js(js)
    )


def test_dashboard_details_sort_controls_reorder_locally_without_reload():
    """Sort-only details controls should avoid refetching the whole modal."""
    js = _read_dashboard_js()

    assert "function applyLocalReleaseSort()" in js
    compact_js = _compact_js(js)
    assert "controls.sort = sortSelect.value; applyLocalReleaseSort();" in compact_js
    assert "applyDetailsControls(controls); applyLocalReleaseSort();" in compact_js
    assert "controls.sort = sortSelect.value; reloadDetailsWithControls();" not in compact_js
    assert "applyDetailsControls(controls); reloadDetailsWithControls();" not in compact_js


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
    assert (
        "postToAction('/requests/{{ req.id }}/search', '/?tab=pending', this, this.closest('[data-request-id]'))"
        in template
    )
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
    assert "function getDedupedCheckedBulkCheckboxes" in js
    assert "formData.delete('request_ids');" in js
    assert "ids.forEach((id) => formData.append('request_ids', id));" in js
    assert "getDedupedCheckedBulkCheckboxes(form);" in js
    assert "disableSearchControls(row);" in js


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
        assert f"{name}," in js
    assert "Accept: 'application/json'" in js
    assert "await refreshCurrentTabContent();" in js
    assert "bindDenyModalHandlers," in js
    assert "toggle.dataset.selectAllBound === 'true'" in js


def test_dashboard_js_uses_collapsible_episode_results():
    """Episode cached results should live in their own collapsible sections."""
    js = _read_dashboard_js()

    assert "episode-details-" in js
    # Episode rows are divider-separated inside the season body (no per-row
    # border box), so only the rounded/tinted wrapper is asserted here.
    assert "episodeDetailsId" in js
    assert 'class="group rounded-xl ' in js
    assert "divide-y divide-gray-700/40" in js
    assert "No cached episode results yet" in js


def test_dashboard_js_collapses_completed_episodes_by_default():
    """Completed episodes start collapsed even when they have cached releases."""
    js = _read_dashboard_js()

    # One shared completeness helper instead of repeated inline status checks.
    assert "function isEpisodeComplete(episode)" in js
    assert "const isComplete = isEpisodeComplete(ep);" in js
    assert "const isOpen = !isComplete && (episodeReleases.length > 0 || isStaged);" in js
    assert "const showInlineActions = !isComplete;" in js
    assert "return !isEpisodeComplete(ep);" in js
    # The old inline convention is gone from the season/episode renderers.
    assert "ep.status !== 'available' && ep.status !== 'completed'" not in js
    # Manual expansion still survives re-renders through the accordion state hooks.
    assert "function captureDetailsAccordionState()" in js
    assert "function restoreDetailsAccordionState(state)" in js


def test_dashboard_tv_ui_is_responsive_at_mobile_widths():
    """Season-pack / release UI emits real responsive classes for narrow viewports."""
    js = _read_dashboard_js()
    css = _read_dashboard_css()

    # Release cards reflow (action button drops to its own line) instead of
    # squeezing the title. Assert on the load-bearing tokens only so the tests
    # survive class reordering.
    assert "basis-[70%]" in js
    # Season / episode / pack-group headers wrap on their own, not via a CSS patch.
    assert js.count("flex-wrap") >= 5
    assert js.count("lg:flex-nowrap") >= 5
    # Pack coverage bar shrinks on small screens.
    assert "w-24 lg:w-40" in js
    # Long titles wrap (capped at two lines) instead of truncating to nothing.
    assert "line-clamp-2" in js
    assert "lg:line-clamp-none" in js
    # The blunt summary flex-wrap escape hatch is retired.
    assert "#request-details-releases summary" not in css


def test_dashboard_details_title_gets_its_own_line_on_mobile(dashboard_template_path):
    """Details modal title should not share a truncated flex row at 390px."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert 'class="flex min-w-0 flex-col items-start gap-0.5 lg:flex-row' in template
    assert "overflow-wrap-anywhere line-clamp-2 lg:line-clamp-none lg:truncate" in template
    # Desktop table title cells are consistent (width cap + wrapping).
    assert template.count("text-sm font-medium text-white max-w-sm overflow-wrap-anywhere") == 6


def test_dashboard_js_includes_release_status_column_and_upload_age():
    """Torrent cards render a score-first row with annotation data hooks."""
    js = _read_dashboard_js()

    assert 'data-release-status-column="true"' not in js
    assert 'data-release-rejection-reason="true"' not in js
    assert 'data-release-upload-age="true"' not in js
    assert 'data-release-size-per-season="true"' in js
    assert 'data-release-resolution="true"' in js
    assert 'data-release-codec="true"' in js
    assert 'data-release-files="true"' in js
    assert "function formatRelativePublishAge(publishDate)" in js
    assert "window.siftarrStagingModeEnabled" in js
    assert "/manual-release/use" in js
    assert "background refresh updates Plex/Overseerr data" not in js
    assert "Plex episode availability is being resolved for partial seasons" not in js


def test_dashboard_details_stats_rail_is_populated():
    """Desktop left-rail `<dl id="request-details-stats">` should be populated."""
    js = _read_dashboard_js()
    css = _read_dashboard_css()

    # The renderer + its window export exist so the rail is filled at runtime.
    assert "function renderDetailsStats(data)" in js
    _assert_window_facade_exports(js, "renderDetailsStats")
    # Wired into openRequestDetails after the details payload loads, and cleared
    # on a fresh (non-preserveUiState) modal open.
    assert "renderDetailsStats(data);" in js
    assert "getElementById('request-details-stats')" in js
    # The five named stat labels from the mockup are emitted by the renderer.
    for label in ("Cached results", "Passed", "Rejected", "Staged", "Last search"):
        assert label in js
    # Tone classes per the mockup (emerald=passed, red=rejected, cyan=staged).
    assert "text-emerald-400 tabular-nums" in js
    assert "text-red-400 tabular-nums" in js
    assert "text-cyan-300 tabular-nums" in js
    # Stat values are sourced from the serializer's named fields, not hardcoded.
    assert "data.total_releases" in js
    assert "data.active_staged_torrents" in js
    assert "data.active_staged_torrent" in js
    # Last search reuses the shared relative-time helper, now exported.
    assert "function formatRelativePublishAge(publishDate)" in js
    _assert_window_facade_exports(js, "formatRelativePublishAge")
    assert "window.formatRelativePublishAge" in js
    # The dangling CSS rule referencing the removed `data-release-rejection-reason`
    # attribute contract is gone (Phase 2 inlined the rejection reason as text).
    assert '[data-release-rejection-reason="true"]' not in css


def test_dashboard_js_supports_annotation_highlighting():
    """Torrent annotation highlighting helpers should exist in the JS."""
    js = _read_dashboard_js()

    assert "function renderAnnotation(" in js
    assert "function releaseAnnotationTone(" in js
    assert "match.effect" in js
    assert "release.size_per_season_passed === true" in js


def test_dashboard_js_includes_active_stage_replacement_copy():
    """Request details should explain replacement semantics for staged picks."""
    js = _read_dashboard_js()

    # The separate staged banner was removed in Phase 5 (element gone from the
    # template, updateActiveStageBanner function and its call sites deleted).
    # Replacement semantics now live entirely on the inline staged release card.
    assert "request-details-active-stage-banner" not in js
    assert "Selecting another result will replace it." not in js
    assert "updateActiveStageBanner" not in js
    assert "currentActiveStagedTorrent" not in js
    # Inline icon actions live on the release card itself.
    assert "inlineStagedAction" in js
    assert "/approve" in js
    assert "/discard" in js
    assert "Approve and download now" in js
    assert "Reject staged release" in js
    assert "Already staged for review" in js
    assert "Approve and download now" in js
    assert "Reject staged release" in js
    assert "Stage this torrent for review and approval." in js
    assert "Replace the active staged torrent with this selection." in js
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

    assert "staged: 'badge-cyan'" in js
    # Staged episode rows keep a cyan-tinted fill; the staged release card
    # inside them is a borderless recessed dark box (its fill is the separation,
    # so no outline is needed).
    assert "bg-cyan-950/40" in js
    assert "rounded-xl bg-surface-950" in js
    # Staged badge on the card title uses the inline cyan pill style.
    assert "bg-cyan-900/60 text-cyan-300" in js


def test_dashboard_js_uses_icon_actions_for_synthetic_staged_pack_rows():
    js = _read_dashboard_js()
    staged_pack_renderer = js[
        js.index("function renderStagedPackRow") : js.index("function renderStagedPackRows")
    ]

    assert "Already staged for review" in staged_pack_renderer
    assert "Approve and download now" in staged_pack_renderer
    assert "Reject staged release" in staged_pack_renderer
    assert ">Approve</button>" not in staged_pack_renderer
    assert ">Discard</button>" not in staged_pack_renderer


def test_dashboard_js_skips_ineligible_pack_searches_and_can_research_completed_episodes():
    js = _read_dashboard_js()

    assert "function isSeasonPackEligible(season)" in js
    assert "airDate && airDate > today" in js
    assert "Pack search skipped: season is incomplete or partly available" in js
    assert "const RELEASE_ACTION_ICONS" in js
    assert "search:" in js
    assert "Search S" in js
    assert 'id="episode-search-' in js


def test_dashboard_tv_large_search_uses_scope_modal_and_supports_cancel(
    dashboard_template_path,
):
    js = _read_dashboard_js()
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "function countUnavailableTvSeasons(data = window.currentDetailsData)" in js
    assert "unavailableSeasonCount < 5" in js
    assert "function chooseLargeTvSearch(choice)" in js
    assert "window.confirm(" not in js
    assert "searchChoice === 'packs'" in js
    assert "await window.searchAllSeasonPacks(requestId)" in js
    assert "'/search/cancel'" in js
    assert "function cancelActiveTvSearch()" in js
    assert "window.confirmLargeTvSearch(requestId)" in js
    assert "window.startTvSearchProgress(" in js
    assert 'id="large-tv-search-modal"' in template
    assert "Search all</span>" in template
    assert "Search season packs</span>" in template
    assert "Don't search</span>" in template
    assert 'id="search-progress-cancel"' in template


def test_dashboard_js_focuses_staged_tv_episode_after_reload():
    """TV scoped staging should reopen details focused to one episode."""
    js = _read_dashboard_js()

    assert "data-stage-scope" in js
    assert "focusTvScope: approveNow ? null : stagedScope" in js
    assert "function captureDetailsAccordionState()" in js
    assert "function restoreDetailsAccordionState(state)" in js
    assert "function focusTvEpisode(requestId, seasonNumber, episodeNumber)" in js
    assert "function focusStagedTvScope(requestId, scope)" in js
    assert "details.open = details === targetDetails;" in js
    assert "seasonDetails.open = true;" in js
    assert "targetDetails.open = true;" in js
    assert "window.focusStagedTvScope(requestId, focusTvScope);" in js
    assert "collapseStagedTvScope" not in js


def test_dashboard_template_staged_details_uses_row_card_clicks(dashboard_template_path):
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    staged_section = template[
        template.index('id="content-staged"') : template.index(
            "{# ═══════════════ TORRENT STATUS TAB"
        )
    ]
    assert "openStagedRequestDetailsFromElement(this)" in staged_section
    assert "openRequestDetails({{ torrent.request_id }})" not in staged_section
    assert ">Details</button>" not in staged_section
    assert "event.stopPropagation()" in staged_section
    assert "postStagedAction('/staged/{{ torrent.id }}/approve'" in staged_section
    assert "postStagedAction('/staged/{{ torrent.id }}/discard'" in staged_section
    assert "openReplaceModal({{ torrent.id }}" in staged_section


def test_dashboard_js_removes_scope_menu_helpers():
    """Normal TV details UI should not expose scope-menu search controls."""
    js = _read_dashboard_js()

    assert "tv-search-scope-menu" not in js
    assert "tv-search-scope-seasons" not in js
    assert "function toggleTvSearchScopeMenu(event)" not in js
    assert "function closeTvSearchScopeMenu()" not in js
    assert "function populateTvSearchScopeMenu()" not in js
    assert "tv-search-dropdown" not in js
    compact_js = _compact_js(js)
    assert (
        "!isScopedEpisodeRelease && hasActiveStagedSelection && activeStagedTorrent" in compact_js
    )
    assert "release.title === activeStagedTorrent.title" in compact_js


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


def test_dashboard_template_has_mobile_request_cards_for_list_tabs(dashboard_template_path):
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    for container_id in (
        "pending-request-cards",
        "unreleased-request-cards",
        "finished-request-cards",
        "rejected-request-cards",
    ):
        assert f'id="{container_id}" class="space-y-3 p-3 md:hidden"' in template

    assert template.count('class="hidden md:block overflow-x-auto"') >= 4
    assert "#pending-requests-body tr, #pending-request-cards [data-request-id]" in js
    assert "#unreleased-requests-body tr, #unreleased-request-cards [data-request-id]" in js
    assert "#finished-requests-body tr, #finished-request-cards [data-request-id]" in js
    assert "#rejected-requests-body tr, #rejected-request-cards [data-request-id]" in js
    assert "pending: 'pending-request-cards'" in js
    assert "unreleased: 'unreleased-request-cards'" in js
    assert "finished: 'finished-request-cards'" in js
    assert "rejected: 'rejected-request-cards'" in js
    assert "activeTabContent.querySelectorAll('[data-request-id]')" in js
    assert "window.getComputedStyle(current).display === 'none'" in js
    assert "seen.has(item.id)" in js


def test_dashboard_mobile_card_sort_controls(dashboard_template_path):
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    for select_id, tab_name in (
        ("pending-mobile-sort", "pending"),
        ("unreleased-mobile-sort", "unreleased"),
        ("staged-mobile-sort", "staged"),
        ("downloading-mobile-sort", "downloading"),
        ("finished-mobile-sort", "finished"),
        ("rejected-mobile-sort", "rejected"),
    ):
        assert f'id="{select_id}"' in template
        assert f"onchange=\"sortDashboardCards('{tab_name}', this.value)\"" in template

    for option in (
        'value="requested:desc"',
        'value="releasedate:asc"',
        'value="score:desc"',
        'value="progress:desc"',
        'value="completed:desc"',
        'value="rejectedat:desc"',
        'value="size:desc"',
    ):
        assert option in template

    assert "function sortDashboardCards(tableName, encodedSort)" in js
    assert "sortTable(tableName, sortKey, true, direction === 'desc' ? 'desc' : 'asc');" in js
    assert "forcedDirection = null" in js
    assert "rows.forEach((row) => tbody.appendChild(row));" in js
    assert "if (card) cardContainer.appendChild(card);" in js


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
    assert "'downloading-filter-input': window.filterDownloadingTable" in entry_js


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
    assert "element.style.display === 'none'" in js
    assert "function refreshDetailsNavigationContext()" in js
    assert "window.visibleRequests = window.getVisibleRequests();" in js
    assert "findIndex((r) => r.id === window.currentRequestId)" in js
    assert "refreshDetailsNavigationContext();" in js


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
    assert "row.style.display = textMatch && mediaMatch ? '' : 'none';" in js
    assert "refreshDetailsNavigationContext();" in js
    assert "rows.forEach((row) => tbody.appendChild(row));" in js
    assert "if (card) cardContainer.appendChild(card);" in js


def test_torrent_status_poll_intervals_are_named_constants():
    """Active downloads poll every 1s, completed torrents every 10s."""
    js = _read_dashboard_js()

    assert "const ACTIVE_DOWNLOADS_POLL_INTERVAL_MS = 1000;" in js
    assert "const COMPLETED_TORRENTS_POLL_INTERVAL_MS = 10000;" in js
    assert "setInterval(_patchStagedDownloadStatus, _currentPollIntervalMs())" in js


def test_torrent_status_subtabs_and_grouped_rendering():
    """Sub-tab toggle, grouped rendering and collapse-state preservation exist."""
    js = _read_dashboard_js()

    assert "function showQbitView(view)" in js
    assert "qbit-completed-list" in js
    assert "'/api/torrents/completed' : '/api/downloads'" in js
    assert "function renderQbitGroups(groups, options)" in js
    assert "function renderQbitCompleted(groups)" in js
    assert "function captureQbitGroupState(prefix)" in js
    assert "function restoreQbitGroupState(prefix, state)" in js
    assert "function toggleQbitGroup(prefix, key)" in js
    # Groups of one render flat instead of as a needless collapsible group.
    assert "if (group.count === 1 && torrents.length === 1)" in js
    assert "if (window.reinitColumnResizer) window.reinitColumnResizer();" in js


def test_torrent_status_tab_drops_embedded_webui(dashboard_template_path):
    """The embedded qBittorrent Web UI sub-view is gone; the external link stays."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()
    js = _read_dashboard_js()

    assert "qbit-webui-panel" not in template
    assert "qbit-webui-frame" not in template
    assert "<iframe" not in template
    assert "siftarrQbittorrentUrl" not in template
    assert "qbit-webui" not in js
    assert ">Torrent Status</button>" in template
    assert 'id="qbit-subtab-downloading"' in template
    assert 'id="qbit-subtab-completed"' in template
    assert 'id="completed-torrents-table"' in template
    assert 'id="completed-torrents-body"' in template
    assert 'id="completed-torrent-cards" class="space-y-3 p-3 md:hidden"' in template
    assert "Open qBittorrent ↗" in template


def test_completed_torrents_table_is_column_resizable(dashboard_template_path):
    """Completed table needs its own id plus resizable markup."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    table = template.split('id="completed-torrents-table"')[1].split("</table>")[0]

    assert "<colgroup>" in table
    for key in (
        "name",
        "size",
        "downloaded",
        "uploaded",
        "ratio",
        "state",
        "actions",
    ):
        assert f'<col data-col-key="{key}"' in table
        assert f'data-col-key="{key}" style="position: relative;"' in table
    assert 'data-col-key="category"' not in table
    assert table.count('<div class="resize-handle"></div>') == 7


def test_downloads_poll_guards_overlap_and_hidden_tabs():
    """Fast polling must not pile up requests or run in a hidden browser tab."""
    js = _read_dashboard_js()

    assert "if (_downloadStatusPatchInFlight) return;" in js
    assert "_downloadStatusPatchInFlight = true;" in js
    assert "_downloadStatusPatchInFlight = false;" in js
    assert "document.addEventListener('visibilitychange'" in js
    assert "document.visibilityState === 'hidden'" in js


def test_downloading_torrents_table_is_column_resizable(dashboard_template_path):
    """Downloads table needs colgroup, col keys and handles for ColumnResizer."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    table = template.split('id="downloading-torrents-table"')[1].split("</table>")[0]

    assert "<colgroup>" in table
    for key in ("name", "progress", "state", "eta", "speed", "size", "actions"):
        assert f'<col data-col-key="{key}"' in table
        assert f'data-col-key="{key}" style="position: relative;"' in table
    assert 'data-col-key="category"' not in table
    assert table.count('<div class="resize-handle"></div>') == 7


def test_group_collapse_state_ors_table_and_card_representations():
    """Capturing must not let the untouched mobile <details> clobber the table row."""
    js = _read_dashboard_js()

    capture = js.split("function captureQbitGroupState(")[1].split("\nfunction ")[0]

    assert "state[key] = !!state[key] || !!open;" in capture
    assert "state[el.dataset.downloadGroup] = !!el.open;" not in capture
    assert "document.querySelectorAll(`tr[data-download-group]" in capture


def _read_dashboard_template():
    with open(
        os.path.join(os.path.dirname(__file__), "../../../app/siftarr/templates/dashboard.html"),
        encoding="utf-8",
    ) as handle:
        return handle.read()


def test_activity_panel_is_a_header_triggered_overlay():
    """Activity is an overlay over the right third of the modal, not a column."""
    template = _read_dashboard_template()

    # Header trigger replaces the old full-height collapse tab.
    assert 'id="activity-toggle"' in template
    assert 'onclick="toggleActivityPanel()"' in template
    assert 'id="activity-show"' not in template
    assert "collapseActivityPanel()" not in template

    # Overlay panel: hidden by default, pinned to the right third on desktop.
    assert 'id="activity-panel"' in template
    assert 'id="activity-backdrop"' in template
    assert "absolute inset-y-0 right-0" in template
    assert "lg:w-1/3" in template
    assert 'class="hidden absolute inset-y-0 right-0' in template


def test_activity_panel_starts_closed_on_modal_open():
    """A fresh details modal open always closes the Activity overlay."""
    js = _read_dashboard_js()

    assert "function setActivityPanelOpen(open)" in js
    _assert_window_facade_exports(js, "toggleActivityPanel")
    assert "if (modal.classList.contains('hidden')) closeActivityPanel();" in js
    # The count now lives on the header toggle button.
    assert "activity-mobile-count" not in js
    assert "getElementById('activity-count')" in js


def test_escape_closes_activity_overlay_before_the_details_modal():
    entry_js = _read_dashboard_entry_js()

    assert "window.isActivityPanelOpen && window.isActivityPanelOpen()" in entry_js
    assert "window.closeActivityPanel();" in entry_js


def test_season_summary_splits_actions_onto_a_second_line():
    """Season headline row carries status only; actions drop to a quiet line."""
    js = _read_dashboard_js()

    assert "flex flex-col gap-2 px-4 py-4 lg:px-5 cursor-pointer" in js
    assert "flex flex-wrap items-center gap-5 pl-7 text-xs" in js


def test_release_rows_use_the_gap_shorthand_not_axis_longhands():
    """Row spacing regression guard.

    The season/episode/release summary rows previously used `gap-x-*`/`gap-y-*`
    longhands and rendered with no space between labels ("Season 30/9
    availabledenied", "S07E01Episode 1"). The `gap-*` shorthand is what worked
    before, so the details rows must stay on it.
    """
    js_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard/releases.js"
    )
    with open(js_path, encoding="utf-8") as handle:
        releases_js = handle.read()

    assert "gap-x-" not in releases_js
    assert "gap-y-" not in releases_js


def test_season_packs_drawer_uses_a_dark_box_not_a_dashed_outline():
    js = _read_dashboard_js()

    assert "border-gray-700/60 bg-surface-900/60" in js
    assert "border-dashed" not in js


def test_poster_box_keeps_the_2_by_3_aspect_ratio():
    """Portrait posters must not be cropped, and the rail must start level."""
    template = _read_dashboard_template()

    assert 'id="request-details-poster" class="hidden w-14 aspect-[2/3]' in template
    assert 'id="request-details-poster-fallback" class="flex w-14 aspect-[2/3]' in template
    assert "lg:h-44" not in template


def test_tailwind_stylesheet_is_cache_busted():
    """An unversioned tailwind.css strands browsers on stale utility classes."""
    base_path = os.path.join(os.path.dirname(__file__), "../../../app/siftarr/templates/base.html")
    with open(base_path, encoding="utf-8") as handle:
        base = handle.read()

    assert "/static/css/tailwind.css?v={{ asset_version() }}" in base
    assert 'href="/static/css/tailwind.css"' not in base


def test_episode_release_cards_are_vertically_compact():
    """Episode drawers list many candidates, so each row stays short."""
    js = _read_dashboard_js()

    # Bucket (per-episode/per-pack) cards: short padding, tight title/meta lines.
    assert "px-3 py-2 flex flex-wrap items-start gap-3 lg:flex-nowrap" in js
    assert "text-[13px] leading-snug text-white font-medium" in js
    assert "text-[11px] leading-tight text-gray-400" in js
    assert "space-y-1.5" in js
    # The taller pre-compaction card padding is gone from the bucket card.
    assert "py-3.5 flex flex-wrap items-start" not in js


def test_tap_touch_target_floor_is_mobile_only():
    """The 32px tap floor must not pad out compact desktop release rows."""
    css_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/css/tailwind-input.css"
    )
    with open(css_path, encoding="utf-8") as handle:
        css = handle.read()

    tap_block = css[css.index("@media (max-width: 1023px)") :]
    assert ".tap-primary {" in tap_block[:400]
    assert ".tap {" in tap_block[:400]


def test_rejected_releases_keep_their_meta_line_with_a_short_verdict():
    """Rejections annotate the meta line instead of replacing it."""
    js = _read_dashboard_js()

    assert "function summarizeRejectionReason(reason)" in js
    assert "function renderRejectionVerdict(release)" in js
    assert "'size over $1'" in js
    assert "'size under $1'" in js
    assert "'excluded by $1'" in js
    assert "'no required match'" in js
    assert 'data-release-rejection="true"' in js
    # The meta line is built once for passed and rejected releases alike, so the
    # resolution/codec/size/seeders annotations survive a rejection.
    assert "rejected ? renderRejectionVerdict(release) : ''," in js
    assert "text-[11px] leading-tight text-red-300/80" not in js


def test_staged_packs_are_visible_when_no_cached_pack_row_exists():
    """A staged pack must be visible even after its cached release is purged."""
    js = _read_dashboard_js()

    assert "function stagedPacksForSeason(stagedTorrents, seasonNumber)" in js
    assert "function stagedPacksMissingFromRows(stagedPacks, releases)" in js
    assert "function renderStagedPackRow(staged, _requestId)" in js
    assert 'data-staged-pack-row="true"' in js
    # Count label distinguishes staged from cached, and the drawer opens itself
    # when there is a staged pack the user would otherwise not see.
    assert "countParts.push(orphanStaged.length + ' staged');" in js
    assert "const openAttr = orphanStaged.length ? ' open' : '';" in js
    assert "no cached result — awaiting approval" in js


def test_episodes_label_staging_that_comes_from_a_pack():
    """The status badge says when staging came from a pack, not this episode."""
    js = _read_dashboard_js()

    assert "'staged via ' + stagedScopeTypeLabel(coveringPack)" in js
    assert "function stagedScopeTypeLabel(staged)" in js
    assert "'multi-season pack'" in js
    # Only pack-scoped staging is relabelled, not an episode's own release.
    assert "(stagedTorrent.target_scope || {}).type !== 'single_episode'" in js
    # The verbose inline notice this replaced is gone.
    assert "renderCoveringPackNotice" not in js
    assert "data-covering-pack-notice" not in js
