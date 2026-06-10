"""Database models for Siftarr."""

from app.siftarr.models._base import Base
from app.siftarr.models.activity_log import ActivityLog, EventType
from app.siftarr.models.app_setting import AppSetting
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.rule import Rule, RuleType
from app.siftarr.models.search_history import SearchRun, SearchRunCandidate
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.models.stats_metrics import (
    StatsReleaseFact,
    StatsRuleOutcome,
    StatsTimingEvent,
)

__all__ = [
    "ActivityLog",
    "AppSetting",
    "Base",
    "Episode",
    "EventType",
    "MediaType",
    "Request",
    "RequestStatus",
    "Release",
    "Rule",
    "RuleType",
    "Season",
    "SearchRun",
    "SearchRunCandidate",
    "StagedTorrent",
    "StatsReleaseFact",
    "StatsRuleOutcome",
    "StatsTimingEvent",
]
