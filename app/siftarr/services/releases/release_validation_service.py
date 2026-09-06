"""Shared hard validation at release handoff and approval boundaries."""

from dataclasses import dataclass

from app.siftarr.models.request import MediaType, Request
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.releases.release_disposition_service import ReleaseDispositionService
from app.siftarr.services.releases.release_parser import (
    movie_release_identity_rejection_reason,
    tv_release_identity_rejection_reason,
)


@dataclass(frozen=True)
class ReleaseWarning:
    code: str
    message: str


def identity_warning(request: object, release: object) -> ReleaseWarning | None:
    media_type = getattr(request, "media_type", None)
    request_title = getattr(request, "title", None)
    if not isinstance(request_title, str):
        return None
    request_year = getattr(request, "year", None)
    args = {
        "request_title": request_title,
        "request_year": request_year if isinstance(request_year, int) else None,
        "release_title": str(getattr(release, "title", "")),
    }
    reason = (
        tv_release_identity_rejection_reason(**args)
        if media_type == MediaType.TV
        else movie_release_identity_rejection_reason(**args)
    )
    return ReleaseWarning("identity", reason) if reason else None


def snapshot_warnings(release: object) -> list[ReleaseWarning]:
    warnings: list[ReleaseWarning] = []
    if getattr(release, "passed_rules", None) is False:
        warnings.append(
            ReleaseWarning(
                "rules",
                str(
                    getattr(release, "rejection_reason", None)
                    or "Release no longer passes current rules"
                ),
            )
        )
    observed_seeders = getattr(release, "seeders", getattr(release, "seeders_snapshot", None))
    if observed_seeders is not None and int(observed_seeders or 0) <= 0:
        warnings.append(
            ReleaseWarning(
                "seeders",
                "Last observed seeder count is zero; this is not a fresh indexer health check",
            )
        )
    return warnings


async def apply_dispositions(
    db, request: Request, evaluations: list[ReleaseEvaluation]
) -> list[ReleaseEvaluation]:
    """Mark blocked evaluations before ranking while retaining them as evidence."""
    blocked = await ReleaseDispositionService(db).blocked_keys(
        request.id,
        [item.release for item in evaluations],
        media_type=request.media_type,
    )
    for evaluation in evaluations:
        disposition = blocked.get(id(evaluation.release))
        if disposition is not None:
            evaluation.passed = False
            reason = disposition.reason or "Previously rejected for this request"
            evaluation.rejection_reason = f"Release blocked: {reason}"
    return evaluations
