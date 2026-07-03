"""Template assertions for dashboard UI."""

import os
import subprocess
import textwrap


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

    # Scope chips replace the old "Season packs and complete series" drawer.
    assert "Show:" in js
    assert "All results" in js
    assert "Season packs" in js
    assert "Complete series" in js
    assert "setDetailsScope" in js
    assert "scope-season-packs-" in js
    assert "scope-complete-series-" in js
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
    # Season-row quiet links: Mark all / Stage individual episodes / Search season.
    assert "Mark all" in js
    assert "Stage individual episodes" in js
    assert "Search season" in js
    assert "searchSeasonPacks(' + requestId + ', ' + season.season_number + ')" in js
    # Scope chips switch client-side without a backend reload.
    assert "function setDetailsScope(requestId, scope)" in js
    assert "window.setDetailsScope = setDetailsScope;" in js
    assert "scope: 'all'" in js
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
    assert "ids.forEach(id => formData.append('request_ids', id));" in js
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
    assert "window.renderDetailsStats = renderDetailsStats;" in js
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
    assert "window.formatRelativePublishAge = formatRelativePublishAge;" in js
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


def test_release_annotation_tone_uses_rule_metadata_before_fallbacks():
    """Rule-highlighted annotations should be green/red/default from relevant matches."""
    js_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard/releases.js"
    )
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        global.window = {{}};
        global.document = {{}};
        eval(fs.readFileSync({js_path!r}, 'utf8'));
        const cases = [
          [releaseAnnotationTone({{resolution: '2160p', matches: [{{rule_name: 'Resolution 2160p', matched: true, effect: 'allow'}}]}}, 'resolution'), 'text-emerald-400'],
          [releaseAnnotationTone({{codec: 'x265', matches: [{{rule_name: 'Codec x265', matched: true, effect: 'disallow'}}]}}, 'codec'), 'text-red-400'],
          [releaseAnnotationTone({{resolution: '2160p', codec: 'x265', matches: [{{rule_name: 'Indexer trusted', matched: true, effect: 'allow'}}]}}, 'resolution'), 'text-gray-400'],
          [releaseAnnotationTone({{codec: 'x265', matches: []}}, 'codec'), 'text-emerald-400'],
        ];
        for (const [actual, expected] of cases) {{
          if (actual !== expected) {{
            throw new Error(`expected ${{expected}}, got ${{actual}}`);
          }}
        }}
        """
    )

    subprocess.run(["node", "-e", script], check=True)


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
    # Inline Approve/Discard/Replace live on the staged release card itself.
    assert "inlineStagedAction" in js
    assert "/approve" in js
    assert "/discard" in js
    assert ">Approve</button>" in js
    assert ">Discard</button>" in js
    assert ">Replace</button>" in js
    assert ">Stage</button>" in js
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

    assert "'staged': 'badge-cyan'" in js
    # New score-first card: staged items keep cyan-tinted backgrounds/badges,
    # but outlines stay in the muted gray border family.
    assert "bg-cyan-950/20" in js
    assert "border-gray-700/60 bg-cyan-950/20" in js
    assert "border-gray-700/60 bg-cyan-950/10" in js
    # Staged badge on the card title uses the inline cyan pill style.
    assert "bg-cyan-900/60 text-cyan-300" in js


def test_dashboard_js_focuses_staged_tv_episode_after_reload():
    """TV scoped staging should reopen details focused to one episode."""
    js = _read_dashboard_js()

    assert "data-stage-scope" in js
    assert "focusTvScope: stagedScope" in js
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
        template.index('id="content-staged"') : template.index("{# ═══════════════ DOWNLOADING TAB")
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
    assert "rows.forEach(row => tbody.appendChild(row));" in js
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
    assert "element.style.display === 'none'" in js
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
