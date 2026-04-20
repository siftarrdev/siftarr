"""Template assertions for dashboard UI."""


def test_dashboard_template_includes_search_multi_season_ui(dashboard_template_path):
    """Dashboard template should expose the Search Multi Season TV UI."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "Search Multi Season Packs" in template
    assert "Run Search Multi Season Packs to inspect broad multi-season coverage." in template
    assert "Searching multi season packs..." in template
    assert "No multi season or complete-series results found." in template
    assert "function searchAllSeasonPacks(" in template
    assert "/requests/' + targetRequestId + '/seasons/search-all" in template


def test_dashboard_template_uses_collapsible_episode_results(dashboard_template_path):
    """Episode search results should live in their own collapsible sections."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "episode-details-" in template
    assert "<details id=\"' + episodeDetailsId + '\" class=\"group rounded-lg border" in template
    assert "if (details) details.open = true;" in template


def test_dashboard_template_includes_release_status_column_and_upload_age(dashboard_template_path):
    """Torrent cards should render a right-side status area with rejection reason and age."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert 'data-release-status-column="true"' in template
    assert 'data-release-rejection-reason="true"' in template
    assert 'data-release-upload-age="true"' in template
    assert 'data-release-size-per-season="true"' in template
    assert 'data-release-resolution="true"' in template
    assert 'data-release-codec="true"' in template
    assert "function formatRelativePublishAge(publishDate)" in template
    assert "window.siftarrStagingModeEnabled" in template
    assert "/manual-release/use" in template
    assert "background refresh updates Plex/Overseerr data" in template
    assert "Plex episode availability is being resolved for partial seasons" in template


def test_dashboard_template_supports_annotation_highlighting(dashboard_template_path):
    """Torrent annotation highlighting helpers should exist in the template."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "function renderAnnotation(" in template
    assert "function releaseAnnotationTone(" in template


def test_dashboard_template_includes_active_stage_replacement_copy(dashboard_template_path):
    """Request details template should explain replacement semantics for staged picks."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "request-details-active-stage-banner" in template
    assert "Replace staged" in template
    assert "Stage release" in template
    assert "Stage this torrent for review and approval." in template
    assert "Selecting another result will replace it." in template
    assert "text-emerald-400" in template
    assert "text-red-400" in template


def test_dashboard_template_scopes_episode_stage_buttons_to_target_scope(dashboard_template_path):
    """Episode cards should ignore request-wide staged fallback when scope is episode-specific."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "const releaseScope = release.target_scope || {};" in template
    assert "const isScopedEpisodeRelease = releaseScope.type === 'single_episode';" in template
    assert (
        "const activeStagedTorrent = release.active_staged_torrent || (isScopedEpisodeRelease ? null : currentActiveStagedTorrent);"
        in template
    )
    assert (
        "!isScopedEpisodeRelease && hasActiveStagedSelection && activeStagedTorrent && release.title === activeStagedTorrent.title"
        in template
    )


def test_dashboard_template_refreshes_full_staged_content(dashboard_template_path):
    """Staged refresh should replace the whole section so empty states can appear."""
    with open(dashboard_template_path, encoding="utf-8") as handle:
        template = handle.read()

    assert "const stagedContent = document.getElementById('content-staged');" in template
    assert "const newContent = doc.getElementById('content-staged');" in template
    assert "stagedContent.innerHTML = newContent.innerHTML;" in template
    assert "document.querySelectorAll('#staged-torrents-body tr[data-state=\"approved\"]')" in template
