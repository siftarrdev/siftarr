from app.siftarr.models.rule import TVTarget
from app.siftarr.services.decisions.release_fallback import apply_episode_size_fallback
from app.siftarr.services.decisions.release_quality import ReleaseQualityPolicy
from app.siftarr.services.decisions.rule_engine import RuleEngine, SizeLimitRule
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease


def engine():
    return RuleEngine(
        size_limit_rules=[
            SizeLimitRule(1, "episode", 1024, int(1.5 * 1024**3), TVTarget.EPISODE, "tv")
        ],
        exclusion_patterns=[(2, "CAM", r"\bCAM\b")],
        quality_policy=ReleaseQualityPolicy(media_type="tv"),
    )


def release(title="Show S01E01 1080p", *, size_gb=2.19, seeders=7):
    return ProwlarrRelease(
        title=title,
        size=int(size_gb * 1024**3),
        seeders=seeders,
        leechers=0,
        download_url="https://example.test/torrent",
        indexer="test",
    )


def test_oversize_episode_is_available_only_without_normal_same_episode_choice():
    rules = engine()
    large = rules.evaluate(release())
    normal = rules.evaluate(release(size_gb=1))
    assert not apply_episode_size_fallback([large, normal], rules)[0].passed
    assert apply_episode_size_fallback([large], rules)[0].passed


def test_normal_other_episode_does_not_prevent_fallback():
    rules = engine()
    values = [rules.evaluate(release()), rules.evaluate(release("Show S01E02", size_gb=1))]
    assert apply_episode_size_fallback(values, rules)[0].passed


def test_rejection_and_other_rules_are_never_waived():
    rules = engine()
    rejected = rules.evaluate(release())
    rejected.rejection_reason = "Release is blocked: manually rejected"
    cam = rules.evaluate(release("Show S01E01 1080p CAM"))
    too_large = rules.evaluate(release(size_gb=4))
    no_seeds = rules.evaluate(release(seeders=0))
    assert all(
        not item.passed
        for item in apply_episode_size_fallback([rejected, cam, too_large, no_seeds], rules)
    )


def test_zero_seeder_normal_choice_does_not_prevent_usable_fallback():
    rules = engine()
    values = [rules.evaluate(release()), rules.evaluate(release(size_gb=1, seeders=0))]
    assert apply_episode_size_fallback(values, rules)[0].passed
