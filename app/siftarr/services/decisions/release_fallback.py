"""Consider oversized exact episodes only when normal eligible choices are absent."""

from collections.abc import Sequence

from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    is_exact_single_episode_release,
)


def _episode_key(evaluation: ReleaseEvaluation) -> tuple[int, int] | None:
    coverage = cached_parse_release_coverage(evaluation.release.title)
    season, episode = coverage.season_number, coverage.episode_number
    if season is None or episode is None:
        return None
    if not is_exact_single_episode_release(evaluation.release.title, season, episode):
        return None
    return season, episode


def apply_episode_size_fallback(
    evaluations: Sequence[ReleaseEvaluation], engine: RuleEngine
) -> list[ReleaseEvaluation]:
    """Run after identity and disposition filtering, before TV winner selection.

    Only the explicit maximum-size failure is reconsidered. In particular a durable
    rejection cannot be undone by re-evaluating the release against regex rules.
    The engine remains responsible for the fallback ceiling and every other rule.
    """
    available = {
        key
        for evaluation in evaluations
        if evaluation.passed
        and evaluation.release.seeders > 0
        and (key := _episode_key(evaluation)) is not None
    }
    result: list[ReleaseEvaluation] = []
    for evaluation in evaluations:
        key = _episode_key(evaluation)
        reason = evaluation.rejection_reason or ""
        if (
            not evaluation.passed
            and key is not None
            and key not in available
            and evaluation.release.seeders > 0
            and reason.startswith("Size ")
            and " above maximum " in reason
        ):
            fallback = engine.evaluate(evaluation.release, allow_episode_size_fallback=True)
            if fallback.passed:
                evaluation = fallback
        result.append(evaluation)
    return result
