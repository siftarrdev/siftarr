"""Template assertions for dashboard UI."""

from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.siftarr.routers.dashboard import templates

APP_DIR = Path(__file__).resolve().parents[3] / "app" / "siftarr"


@pytest.mark.parametrize(
    "title",
    [
        "Movie's title",
        r"Backslash \\ and 'quotes'",
        "');window.titleExecuted=true;//",
        '"><img src=x onerror="window.titleExecuted=true">',
    ],
)
def test_replace_buttons_keep_titles_out_of_javascript(title):
    class ReplaceButtonParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.buttons = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "button" and "openReplaceModal" in attributes.get("onclick", ""):
                self.buttons.append(attributes)

    html = templates.env.get_template("dashboard.html").render(
        url_for=lambda name, path: f"/static{path}",
        static_version="test",
        stats={},
        staging_mode_enabled=True,
        staged_torrents=[
            {"id": 4, "request_id": 9, "title": title, "size": 1024, "status": "staged"}
        ],
        staged_request_statuses={9: "staged"},
        movie_identity_mismatch_warnings={},
        replace_staged_torrent_ids={4},
    )
    parser = ReplaceButtonParser()
    parser.feed(html)

    assert len(parser.buttons) == 2  # Mobile and desktop layouts.
    for button in parser.buttons:
        assert button["data-torrent-title"] == title
        assert button["onclick"] == "openReplaceModalFromElement(this, 4, 9, '/?tab=downloading')"
        assert "onerror" not in button


def test_dashboard_entry_cache_busts_imported_modules():
    """The import map versions every static ES module dependency."""
    js = (APP_DIR / "static/js/dashboard.js").read_text(encoding="utf-8")
    template = (APP_DIR / "templates/dashboard.html").read_text(encoding="utf-8")

    assert "import '/static/js/dashboard/details.js';" in js
    assert "import '/static/js/dashboard/search_sse.js';" in js
    assert '<script type="importmap">' in template
    assert '"/static/js/dashboard/details.js"' in template
    assert "dashboard/details.js') }}?v={{ static_version }}" in template
    assert '"/static/js/dashboard/core/state.js"' in template


def test_dashboard_modals_exports_inline_handler_names():
    """Inline deny handlers should match exported modal functions."""
    js = (APP_DIR / "static/js/dashboard/modals.js").read_text(encoding="utf-8")

    for name in ("openDenyModal", "submitDenyRequest", "closeDenyModal"):
        assert f"function {name}(" in js
        assert f"{name}," in js
    assert "Accept: 'application/json'" in js
    assert "await refreshCurrentTabContent();" in js
    assert "bindDenyModalHandlers," in js
    assert "toggle.dataset.selectAllBound === 'true'" in js


def test_tailwind_stylesheet_is_cache_busted():
    """An unversioned tailwind.css strands browsers on stale utility classes."""
    base = (APP_DIR / "templates/base.html").read_text(encoding="utf-8")

    assert "/static/css/tailwind.css?v={{ asset_version() }}" in base
    assert 'href="/static/css/tailwind.css"' not in base
