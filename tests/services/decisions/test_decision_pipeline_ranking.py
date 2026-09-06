from app.siftarr.services.decisions.decision_pipeline import get_best_passing, release_rank_key
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease


def evaluation(title: str, seeders: int, score: int, passed: bool = True):
    return ReleaseEvaluation(
        release=ProwlarrRelease(
            title=title,
            size=1024,
            seeders=seeders,
            leechers=0,
            download_url="https://example.test/torrent",
            indexer="test",
        ),
        passed=passed,
        total_score=score,
        matches=[],
    )


def test_equal_adjusted_scores_prefer_larger_reported_swarm():
    weak = evaluation("Show S01E01 AV1", 1, 100)
    healthy = evaluation("Show S01E01 HEVC", 20, 100)
    assert get_best_passing([weak, healthy]) is healthy
    assert sorted([weak, healthy], key=release_rank_key) == [healthy, weak]


def test_health_is_a_tie_break_not_an_unbounded_quality_override():
    preferred = evaluation("Show S01E01 AV1", 5, 100)
    popular = evaluation("Show S01E01 x264", 5000, 30)
    assert get_best_passing([popular, preferred]) is preferred


def test_rejected_candidates_never_win():
    rejected = evaluation("Wrong Show S01E01", 100, 500, passed=False)
    eligible = evaluation("Show S01E01", 5, 10)
    assert get_best_passing([rejected, eligible]) is eligible
