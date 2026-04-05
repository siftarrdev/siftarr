"""Database models for Arbitratarr."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


from arbitratarr.models.pending_queue import PendingQueue  # noqa: E402
from arbitratarr.models.release import Release  # noqa: E402
from arbitratarr.models.request import Request  # noqa: E402
from arbitratarr.models.rule import Rule, RuleType  # noqa: E402
from arbitratarr.models.settings import Settings  # noqa: E402
from arbitratarr.models.staged_torrent import StagedTorrent  # noqa: E402

__all__ = [
    "Base",
    "Request",
    "Release",
    "Rule",
    "RuleType",
    "Settings",
    "PendingQueue",
    "StagedTorrent",
]
