from app.siftarr.services.decisions.release_quality import (
    ReleaseQualityPolicy,
    quality_signals,
)
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease


def release(title: str, **values) -> ProwlarrRelease:
    fields = {
        "title": title,
        "size": 1024,
        "seeders": 5,
        "leechers": 0,
        "download_url": "https://example.test/file",
        "indexer": "test",
    }
    fields.update(values)
    return ProwlarrRelease.model_validate(fields)


def deltas(item: ProwlarrRelease, media_type: str = "movie") -> dict[str, int]:
    return {
        signal.name: signal.score_delta
        for signal in quality_signals(item, ReleaseQualityPolicy(media_type=media_type))
    }


def test_file_count_only_scores_confident_single_items():
    assert deltas(release("Movie.2025.1080p", files=5))["Compact file list"] == 5
    assert deltas(release("Movie.Collection.2025", files=2)) == {}
    assert deltas(release("Show.S01E02.1080p", files=11), "tv")["High file count"] == -10
    assert "High file count" not in deltas(release("Show.S01E01-E03", files=30), "tv")
    assert deltas(release("Movie.2025", files=0)) == {}


def test_norar_suppresses_count_but_real_archive_wins():
    item = release("Movie.2025.NORAR", files=20)
    assert deltas(item) == {}
    archived = item.model_copy(update={"file_paths": ("Movie.part01.rar", "Movie.r00")})
    assert deltas(archived)["Archive main payload"] == -100


def test_bracketed_archive_title_markers_are_recognized():
    assert deltas(release("Movie.2025.(RAR)"))["RAR title evidence"] == -50
    assert "RAR title evidence" not in deltas(release("Movie.2025.[NORAR]"))
    assert "RAR title evidence" not in deltas(release("Movie.2025.[NO RAR]"))


def test_playable_payload_avoids_ancillary_archive_penalty():
    item = release("Movie.2025", file_paths=("Movie.mkv", "subtitles.zip"))
    assert "Archive main payload" not in deltas(item)


def test_sample_does_not_hide_archive_main_payload_and_zip_is_detected():
    rar = release("Movie.2025", file_paths=("Movie.rar", "Sample/sample.mkv"))
    assert deltas(rar)["Archive main payload"] == -100
    zipped = release("Movie.2025", file_paths=("Movie.zip",))
    assert deltas(zipped)["Archive main payload"] == -100


def test_complete_playable_file_list_suppresses_count_suspicion():
    paths = ("Movie.mkv", *(f"Subs/{number}.srt" for number in range(11)))
    item = release("Movie.2025", files=len(paths), file_paths=paths)
    assert "High file count" not in deltas(item)
    incomplete = item.model_copy(update={"files": len(paths) + 1})
    assert deltas(incomplete)["High file count"] == -10


def test_rar_title_and_seed_health_are_bounded_penalties():
    signals = deltas(release("Movie.2025.RAR", seeders=2))
    assert signals == {"RAR title evidence": -50, "Low seed availability": -40}
    assert deltas(release("Movie.2025", seeders=0)) == {}


def test_engine_quality_is_opt_in_and_never_rejects():
    from app.siftarr.services.decisions.rule_engine import RuleEngine

    item = release("Movie.2025", files=2)
    assert RuleEngine().evaluate(item).total_score == 0
    result = RuleEngine(quality_policy=ReleaseQualityPolicy(media_type="movie")).evaluate(item)
    assert result.passed is True
    assert result.total_score == 5
    assert result.matches[-1].rule_id is None


def test_episode_size_fallback_is_explicit_and_does_not_skip_minimum():
    from app.siftarr.models.rule import TVTarget
    from app.siftarr.services.decisions.rule_engine import RuleEngine, SizeLimitRule

    engine = RuleEngine(
        size_limit_rules=[
            SizeLimitRule(1, "episode size", 1024, 2 * 1024**3, TVTarget.EPISODE, "tv")
        ],
        quality_policy=ReleaseQualityPolicy(media_type="tv"),
    )
    large = release("Show.S01E02.1080p", size=int(2.5 * 1024**3))
    assert engine.evaluate(large).passed is False
    assert engine.evaluate(large, allow_episode_size_fallback=True).passed is True
    too_large = large.model_copy(update={"size": 4 * 1024**3})
    assert engine.evaluate(too_large, allow_episode_size_fallback=True).passed is False
    too_small = large.model_copy(update={"size": 512})
    assert engine.evaluate(too_small, allow_episode_size_fallback=True).passed is False
