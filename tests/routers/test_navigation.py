from pathlib import Path


def test_base_navigation_includes_stats_desktop_and_mobile():
    body = (Path(__file__).resolve().parents[2] / "app/siftarr/templates/base.html").read_text()

    assert body.count('href="/stats"') == 2
    assert body.count("Stats") >= 2
    assert "current_path.startswith('/stats')" in body
    assert 'aria-label="Primary mobile navigation"' in body
    mobile_nav = body[
        body.index('aria-label="Primary mobile navigation"') : body.index('id="user-area"')
    ]
    assert "overflow-x-auto" not in mobile_nav
    assert "grid w-full grid-cols-4 gap-1 md:hidden" in body
