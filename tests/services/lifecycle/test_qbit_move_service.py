"""Tests for qBittorrent move/retention lifecycle service."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import Settings
from app.siftarr.models._base import Base
from app.siftarr.models.activity_log import ActivityLog, EventType
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.lifecycle.qbit_move_service import QbitMoveService


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "qbittorrent_move_enabled": True,
        "qbittorrent_move_completed_dir": "/downloads",
        "qbittorrent_move_movie_root": "/media/movies",
        "qbittorrent_move_tv_root": "/media/tv",
        "qbittorrent_move_unmanaged_fallback_enabled": False,
        "qbittorrent_move_retention_weeks": 6,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _add_managed(session, *, media_type=MediaType.TV, title="Example Show"):
    request = Request(
        external_id=f"{media_type.value}-1",
        media_type=media_type,
        title=title,
        status=RequestStatus.DOWNLOADING,
    )
    session.add(request)
    await session.flush()
    staged = StagedTorrent(
        request_id=request.id,
        torrent_path="/tmp/test.torrent",
        json_path="/tmp/test.json",
        original_filename="test.torrent",
        title=f"{title}.S01.1080p" if media_type == MediaType.TV else title,
        size=1,
        indexer="idx",
        status="approved",
        info_hash="abc123",
    )
    session.add(staged)
    await session.commit()
    return request, staged


@pytest.mark.asyncio
async def test_moves_managed_torrent_using_request_metadata_first(session_maker):
    async with session_maker() as session:
        _request, staged = await _add_managed(session)
        qbit = AsyncMock()
        qbit.get_completed_torrents.return_value = [
            {
                "hash": "abc123",
                "name": "Bad.Fallback.Name.S01E01.1080p",
                "save_path": "/downloads",
                "seeding_time": 10,
            }
        ]
        qbit.set_torrent_location.return_value = True
        qbit.delete_torrents.return_value = True

        result = await QbitMoveService(session, qbit, _settings()).run()

        assert result.moved == 1
        qbit.set_torrent_location.assert_awaited_once_with(
            "abc123", "/media/tv/Example Show", move=True
        )
        await session.refresh(staged)
        assert staged.move_status == "moved"
        assert staged.moved_path == "/media/tv/Example Show"
        log = (
            await session.execute(
                select(ActivityLog).where(ActivityLog.request_id == staged.request_id)
            )
        ).scalar_one()
        assert log.event_type == EventType.REQUEST_STATUS_CHANGED.value


@pytest.mark.asyncio
async def test_unmanaged_fallback_only_moves_completed_under_completed_dir(session_maker):
    async with session_maker() as session:
        qbit = AsyncMock()
        qbit.get_completed_torrents.return_value = [
            {
                "hash": "tvhash",
                "name": "Some.Show.2020.S02.1080p",
                "save_path": "/downloads/complete",
                "seeding_time": 0,
            },
            {
                "hash": "outside",
                "name": "Other.Movie.2020.1080p",
                "save_path": "/other",
                "seeding_time": 0,
            },
        ]
        qbit.set_torrent_location.return_value = True

        result = await QbitMoveService(
            session,
            qbit,
            _settings(qbittorrent_move_unmanaged_fallback_enabled=True),
        ).run()

        assert result.moved == 1
        qbit.set_torrent_location.assert_awaited_once_with(
            "tvhash", "/media/tv/Some Show", move=True
        )


@pytest.mark.asyncio
async def test_managed_destination_sanitizes_path_like_titles(session_maker):
    async with session_maker() as session:
        _request, staged = await _add_managed(session, title="../../Escape")
        qbit = AsyncMock()
        qbit.get_completed_torrents.return_value = [
            {"hash": "abc123", "name": "Escape.S01E01", "save_path": "/downloads"}
        ]

        qbit.set_torrent_location.return_value = True
        result = await QbitMoveService(session, qbit, _settings()).run()

        assert result.moved == 1
        qbit.set_torrent_location.assert_awaited_once_with("abc123", "/media/tv/Escape", move=True)
        await session.refresh(staged)
        assert staged.move_status == "moved"


@pytest.mark.asyncio
async def test_retention_deletes_old_completed_torrents_without_files(session_maker):
    async with session_maker() as session:
        qbit = AsyncMock()
        old_seconds = 7 * 24 * 60 * 60 + 1
        qbit.get_completed_torrents.return_value = [
            {
                "hash": "old",
                "name": "Old",
                "save_path": "/media/movies",
                "seeding_time": old_seconds,
            },
            {"hash": "new", "name": "New", "save_path": "/media/movies", "seeding_time": 10},
        ]
        qbit.delete_torrents.return_value = True

        result = await QbitMoveService(
            session, qbit, _settings(qbittorrent_move_retention_weeks=1)
        ).run()

        assert result.removed == 1
        qbit.delete_torrents.assert_awaited_once_with(["old"], delete_files=False)


@pytest.mark.asyncio
async def test_disabled_service_skips_qbit(session_maker):
    async with session_maker() as session:
        qbit = AsyncMock()
        result = await QbitMoveService(
            session, qbit, _settings(qbittorrent_move_enabled=False)
        ).run()

        assert (result.moved, result.removed, result.errors) == (0, 0, 0)
        qbit.get_completed_torrents.assert_not_awaited()
