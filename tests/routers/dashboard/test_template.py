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
    # Episode rows are divider-separated inside the season body (no per-row
    # border box), so only the rounded/tinted wrapper is asserted here.
    assert '<details id="\' + episodeDetailsId + \'" class="group rounded-xl ' in js
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


def test_dashboard_js_uses_matching_episode_stage_for_approve_and_collapses_it():
    """A staged episode approves its own torrent, then only its drawer closes."""
    js_path = os.path.join(
        os.path.dirname(__file__), "../../../app/siftarr/static/js/dashboard/releases.js"
    )
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const approved = {{id: 'episode-details-9-1-2', open: true}};
        const sibling = {{id: 'episode-details-9-1-3', open: true}};
        global.document = {{
          getElementById: (id) => id === approved.id ? approved : id === sibling.id ? sibling : null,
          querySelectorAll: () => [],
        }};
        global.window = {{
          escapeHtml: (value) => String(value),
          siftarrStagingModeEnabled: true,
          detailsControlState: {{}},
          currentRequestId: 9,
          currentDetailsIndex: 0,
          refreshStagedTabData: async () => {{}},
          openRequestDetails: async () => {{ sibling.open = true; }},
          showToast: () => {{}},
        }};
        eval(fs.readFileSync({js_path!r}, 'utf8'));
        const html = renderSeasonAccordion({{
          request: {{id: 9}},
          active_staged_torrents: [
            {{id: 71, status: 'staged', target_scope: {{type: 'single_episode', season_number: 1, episode_number: 3}}}},
            {{id: 72, status: 'staged', target_scope: {{type: 'single_episode', season_number: 1, episode_number: 2}}}},
          ],
          tv_info: {{
            seasons: [{{id: 1, season_number: 1, status: 'staged', available_count: 0, total_count: 2, staged_count: 1, pending_count: 1, unreleased_count: 0, episodes: [
              {{id: 2, episode_number: 2, title: 'Two', status: 'staged'}},
              {{id: 3, episode_number: 3, title: 'Three', status: 'pending'}},
            ]}}],
            releases_by_episode: {{'1-2': [{{id: 200, passed: true, download_url: 'https://example.test/torrent'}}]}},
            releases_by_season: {{}}, aggregate_counts: {{available: 0, total: 2}},
          }},
        }});
        if (!html.includes("/staged/72/approve") || html.includes("stageTopEpisodeRelease(this, 9, 200)")) {{
          throw new Error('staged episode did not render its matching Approve action');
        }}
        global.fetch = async () => ({{ok: true, json: async () => ({{}})}});
        inlineStagedAction('/staged/72/approve', {{
          textContent: 'Approve', disabled: false,
          closest: () => ({{id: approved.id}}),
        }}).then(() => {{
          if (approved.open || !sibling.open) throw new Error('approval collapsed the wrong drawer');
        }}).catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )

    subprocess.run(["node", "-e", script], check=True)


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
    assert "window.toggleActivityPanel = toggleActivityPanel;" in js
    assert "if (modal.classList.contains('hidden') && window.closeActivityPanel)" in js
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
