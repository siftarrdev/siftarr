from pathlib import Path


def test_base_navigation_includes_stats_desktop_and_mobile():
    body = (Path(__file__).resolve().parents[2] / "app/siftarr/templates/base.html").read_text()

    assert body.count('href="/stats"') == 2
    assert body.count("Stats") >= 2
    assert "current_path.startswith('/stats')" in body
