"""Public models and constants for Plex polling workflows."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.siftarr.models.request import MediaType, Request, RequestStatus

ProgressCallback = Callable[[dict[str, object]], Awaitable[None] | None]

# All non-terminal statuses
NON_TERMINAL_STATUSES = [
    RequestStatus.RECEIVED,
    RequestStatus.SEARCHING,
    RequestStatus.PENDING,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.UNRELEASED,
    RequestStatus.STAGED,
    RequestStatus.DOWNLOADING,
]

FULL_RECONCILE_STATUSES = [
    *NON_TERMINAL_STATUSES,
    RequestStatus.AVAILABLE,
    RequestStatus.COMPLETED,
]

NEGATIVE_RECONCILE_STATUSES = {
    RequestStatus.AVAILABLE,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.COMPLETED,
}

type EpisodeKey = tuple[int, int]


@dataclass(frozen=True)
class PollDecision:
    """Immutable polling result produced by the read-only probe stage."""

    request_id: int
    reason: str
    requested_episode_count: int = 0
    completed_episodes: frozenset[EpisodeKey] = field(default_factory=frozenset)
    episode_availability: dict[EpisodeKey, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetedReconcileResult:
    """Outcome for a targeted request-level Plex reconciliation."""

    request_id: int
    matched: bool = False
    reconciled: bool = False
    authoritative: bool = True
    status_before: RequestStatus | None = None
    status_after: RequestStatus | None = None
    reason: str | None = None
    requested_episode_count: int = 0
    completed_episodes: frozenset[EpisodeKey] = field(default_factory=frozenset)

    @property
    def available(self) -> bool:
        """Return whether Plex now authoritatively satisfies any/all requested media."""
        return self.status_after in {
            RequestStatus.AVAILABLE,
            RequestStatus.PARTIALLY_AVAILABLE,
            RequestStatus.COMPLETED,
        }


@dataclass(frozen=True)
class ScanCheckpointAdvance:
    """Checkpoint advancement details recorded for scan-style runs."""

    previous_checkpoint_at: datetime | None = None
    current_checkpoint_at: datetime | None = None
    advanced: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "previous_checkpoint_at": self.previous_checkpoint_at.isoformat()
            if self.previous_checkpoint_at
            else None,
            "current_checkpoint_at": self.current_checkpoint_at.isoformat()
            if self.current_checkpoint_at
            else None,
            "advanced": self.advanced,
        }


@dataclass
class ScanMetrics:
    """Compact scan metrics shared by incremental and full scan entry points."""

    scanned_items: int = 0
    matched_requests: int = 0
    deduped_items: int = 0
    downgraded_requests: int = 0
    skipped_on_error_items: int = 0
    checkpoint: ScanCheckpointAdvance = field(default_factory=ScanCheckpointAdvance)

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned_items": self.scanned_items,
            "matched_requests": self.matched_requests,
            "deduped_items": self.deduped_items,
            "downgraded_requests": self.downgraded_requests,
            "skipped_on_error_items": self.skipped_on_error_items,
            "checkpoint": self.checkpoint.as_dict(),
        }


@dataclass(frozen=True)
class MediaIdentity:
    """Deduplication key for request probe and scan cycles."""

    media_type: MediaType
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    plex_rating_key: str | None = None
    request_id: int | None = None


@dataclass(frozen=True)
class ScanProbeResult:
    """Read-side result emitted before serialized write application."""

    decisions: tuple[PollDecision, ...] = ()
    matched_requests: int = 0
    skipped_on_error_items: int = 0


@dataclass(frozen=True)
class ScanRunResult:
    """Shared result contract for scan-oriented entry points."""

    mode: str
    completed_requests: int = 0
    metrics: ScanMetrics = field(default_factory=ScanMetrics)
    clean_run: bool = True
    last_error: str | None = None


@dataclass(frozen=True)
class RecentScanMatch:
    """Recently-added Plex item and the affected Siftarr requests."""

    media_identity: MediaIdentity
    item: dict[str, object]
    requests: tuple[Request, ...] = ()
