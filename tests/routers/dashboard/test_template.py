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

    assert "url_for('static', path='/css/dashboard.css')" in template
    assert "url_for('static', path='/js/dashboard.js')" in template
    assert 'type="module"' in template


def test_dashboard_css_contains_resize_styles():
    """Dashboard CSS should contain resize handle styles."""
    css = _read_dashboard_css()

    assert ".resize-handle" in css
    assert "cursor: col-resize" in css
    assert "table.data-resizable" in css
    assert ".accordion-chevron" in css


def test_dashboard_tv_scope_selector_uses_explicit_actions():
    """Dashboard TV search UI should expose explicit search scopes."""
    with open(
        os.path.join(os.path.dirname(__file__), "../../../app/siftarr/templates/dashboard.html"),
        encoding="utf-8",
    ) as handle:
        template = handle.read()

    assert "Search Scope" in template
    assert "TV Search Scope" in template
    assert "Search All Pending Episodes" in template
    assert "Search Multi-Season Packs" in template
    assert "toggleTvSearchScopeMenu(event)" in template
    assert "searchAllPendingEpisodes(); closeTvSearchScopeMenu();" in template
    assert "searchMultiSeasonPacks(currentRequestId); closeTvSearchScopeMenu();" in template


def test_dashboard_js_includes_search_multi_season_ui():
    """Dashboard JS should expose the explicit multi-season TV search UI."""
    js = _read_dashboard_js()

    assert "Search Multi Season Packs" in js
    assert "Run Search Multi Season Packs to inspect broad multi-season coverage." in js
    assert "Searching multi season packs..." in js
    assert "No multi season or complete-series results found." in js
    assert "function searchMultiSeasonPacks(" in js
    assert "/requests/' + targetRequestId + '/multi-season-packs/search" in js
    assert "function searchSeasonPacks(requestId, seasonNumber)" in js
    assert "/requests/' + requestId + '/seasons/' + seasonNumber + '/season-packs/search" in js
    assert "function searchAllPendingEpisodes()" in js
    assert "No pending aired episodes to search." in js
    assert "Finished searching all pending aired episodes" in js
    assert "tv-search-all-results" not in js


def test_dashboard_js_uses_collapsible_episode_results():
    """Episode search results should live in their own collapsible sections."""
    js = _read_dashboard_js()

    assert "episode-details-" in js
    assert '<details id="\' + episodeDetailsId + \'" class="group rounded-lg border' in js
    assert "if (details) details.open = true;" in js
    assert (
        "/requests/' + requestId + '/seasons/' + seasonNumber + '/episodes/' + episodeNumber + '/search"
        in js
    )


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
    assert "background refresh updates Plex/Overseerr data" in js
    assert "Plex episode availability is being resolved for partial seasons" in js


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


def test_dashboard_js_scopes_episode_stage_buttons_to_target_scope():
    """Episode cards should ignore request-wide staged fallback when scope is episode-specific."""
    js = _read_dashboard_js()

    assert "const releaseScope = release.target_scope || {};" in js
    assert "const isScopedEpisodeRelease = releaseScope.type === 'single_episode';" in js
    assert (
        "const activeStagedTorrent = release.active_staged_torrent || (isScopedEpisodeRelease ? null : window.currentActiveStagedTorrent);"
        in js
    )


def test_dashboard_js_uses_scope_menu_helpers():
    """TV scope menu helpers should replace the legacy generic dropdown naming."""
    js = _read_dashboard_js()

    assert "tv-search-scope-menu" in js
    assert "tv-search-scope-seasons" in js
    assert "function toggleTvSearchScopeMenu(event)" in js
    assert "function closeTvSearchScopeMenu()" in js
    assert "function populateTvSearchScopeMenu()" in js
    assert "closeTvSearchScopeMenu();" in js
    assert "populateTvSearchScopeMenu();" in js
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
    assert "document.querySelectorAll('#downloading-torrents-body tr')" in js


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
    assert "torrent.request_id in downloading_request_ids" in template
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
    assert (
        "rows.forEach(row => tbody.appendChild(row));\n    window.refreshDetailsNavigationContext();"
        in js
    )
