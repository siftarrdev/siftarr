from app.siftarr.config import Settings
from app.siftarr.services.releases.release_parser import tv_release_identity_rejection_reason


def test_complete_series_marker_is_not_part_of_series_identity():
    assert (
        tv_release_identity_rejection_reason(
            request_title="Community",
            request_year=2009,
            release_title="Community Complete Series 1080p x265",
        )
        is None
    )


def test_explicit_regional_alias_is_year_scoped_and_does_not_accept_other_show(monkeypatch):
    settings = Settings(
        secret_key="test-only", trusted_tv_title_aliases={"top gear|2002": ["Top Gear UK"]}
    )
    monkeypatch.setattr("app.siftarr.config.get_settings", lambda: settings)
    assert (
        tv_release_identity_rejection_reason(
            request_title="Top Gear", request_year=2002, release_title="Top Gear UK S14E01 1080p"
        )
        is None
    )
    for title, year in [
        ("Top Gear US S14E01 1080p", 2002),
        ("Top Gear UK S14E01 1080p", 2010),
        ("Top Gear UK 2010 S14E01 1080p", 2002),
        ("Top Gear Extra Gear S01E01", 2002),
    ]:
        assert tv_release_identity_rejection_reason(
            request_title="Top Gear", request_year=year, release_title=title
        )
