"""One local request per movie or show, with every Seerr request linked to it."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.request_overseerr_link import RequestOverseerrLink
from app.siftarr.models.season import Season
from app.siftarr.services.admin import settings_service
from app.siftarr.services.lifecycle.overseerr_sync_service import (
    approve_overseerr_request_best_effort,
)
from app.siftarr.services.lifecycle.request_link_service import add_overseerr_link


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


def _seerr_request(request_id: int, tmdb_id: int, media_type: str = "tv") -> dict[str, Any]:
    return {
        "id": request_id,
        "status": "approved",
        "media": {"tmdbId": tmdb_id, "mediaType": media_type, "status": "pending"},
    }


async def _prepare(ov_req, *_args, **_kwargs):
    media = ov_req["media"]
    return settings_service.PreparedOverseerrImport(
        external_id=str(media["tmdbId"]),
        media_type=MediaType.TV if media["mediaType"] == "tv" else MediaType.MOVIE,
        tmdb_id=media["tmdbId"],
        tvdb_id=None,
        title=f"Media {media['tmdbId']}",
        year=None,
        requester_username=None,
        requester_email=None,
        overseerr_request_id=ov_req["id"],
        media_details=None,
    )


async def _run_import(
    db, upstream: list[dict[str, Any]], *, deleted_upstream_ids: frozenset[int] = frozenset()
):
    class FakeOverseerrService:
        def __init__(self, settings=None):
            self.settings = settings

        async def get_all_requests(self, status=None):
            return upstream

        async def request_exists(self, request_id):
            return False if request_id in deleted_upstream_ids else None

        def normalize_request_status(self, status):
            return str(status).lower()

        def normalize_media_status(self, status):
            return str(status).lower() if status is not None else "unknown"

    class NoopPlexService:
        def __init__(self, settings=None):
            pass

    class NoopEpisodeSync:
        def __init__(self, *_args, **_kwargs):
            pass

        async def sync_request(self, _request_id):
            return []

    with patch(
        "app.siftarr.services.lifecycle.episode_sync_service.EpisodeSyncService",
        NoopEpisodeSync,
    ):
        return await settings_service.import_overseerr_requests(
            db,
            MagicMock(overseerr_sync_concurrency=1),
            overseerr_service_cls=FakeOverseerrService,
            plex_service_cls=NoopPlexService,
            evaluate_imported_request_func=AsyncMock(return_value=None),
            prepare_overseerr_import_func=_prepare,
            logger=MagicMock(),
        )


async def _add_request(db, *, tmdb_id: int, overseerr_request_id: int, **fields) -> RequestModel:
    fields.setdefault("media_type", MediaType.TV)
    fields.setdefault("status", RequestStatus.PENDING)
    request = RequestModel(
        external_id=f"{tmdb_id}-{overseerr_request_id}",
        tmdb_id=tmdb_id,
        title="Show",
        **fields,
    )
    db.add(request)
    await db.flush()
    await add_overseerr_link(db, request, overseerr_request_id)
    await db.commit()
    return request


async def _links(db) -> dict[int, int]:
    rows = await db.execute(
        select(RequestOverseerrLink.overseerr_request_id, RequestOverseerrLink.request_id)
    )
    return dict(rows.tuples().all())


@pytest.mark.asyncio
async def test_second_season_request_links_to_existing_show(session):
    show = await _add_request(session, tmdb_id=136315, overseerr_request_id=32)

    await _run_import(session, [_seerr_request(32, 136315), _seerr_request(264, 136315)])

    rows = (await session.execute(select(RequestModel))).scalars().all()
    assert [row.id for row in rows] == [show.id]
    assert await _links(session) == {32: show.id, 264: show.id}


@pytest.mark.asyncio
async def test_two_new_requests_for_same_show_in_one_sync_create_one_row(session):
    await _run_import(session, [_seerr_request(279, 211288), _seerr_request(294, 211288)])

    rows = (await session.execute(select(RequestModel))).scalars().all()
    assert len(rows) == 1
    assert set(await _links(session)) == {279, 294}


@pytest.mark.asyncio
async def test_new_request_reactivates_denied_show(session):
    show = await _add_request(
        session,
        tmdb_id=66134,
        overseerr_request_id=102,
        status=RequestStatus.DENIED,
        rejection_reason="Removed from Overseerr",
    )
    season = Season(request_id=show.id, season_number=1, status=RequestStatus.DENIED)
    session.add(season)
    await session.flush()
    session.add_all(
        [
            Episode(season_id=season.id, episode_number=1, status=RequestStatus.DENIED),
            Episode(season_id=season.id, episode_number=2, status=RequestStatus.COMPLETED),
        ]
    )
    await session.commit()

    await _run_import(session, [_seerr_request(400, 66134)])

    await session.refresh(show)
    assert show.status == RequestStatus.PENDING
    assert show.rejection_reason is None
    episodes = (await session.execute(select(Episode).order_by(Episode.episode_number))).scalars()
    assert [ep.status for ep in episodes] == [RequestStatus.PENDING, RequestStatus.COMPLETED]
    assert (await session.execute(select(RequestModel))).scalars().all() == [show]


@pytest.mark.asyncio
async def test_movie_and_show_with_same_tmdb_number_stay_separate(session):
    await _add_request(session, tmdb_id=500, overseerr_request_id=1, media_type=MediaType.MOVIE)

    await _run_import(session, [_seerr_request(1, 500, "movie"), _seerr_request(2, 500, "tv")])

    rows = (await session.execute(select(RequestModel))).scalars().all()
    assert sorted(row.media_type for row in rows) == [MediaType.MOVIE, MediaType.TV]


@pytest.mark.asyncio
async def test_deleting_one_season_request_keeps_show_active(session):
    show = await _add_request(session, tmdb_id=211288, overseerr_request_id=279)
    await add_overseerr_link(session, show, 294)
    await session.commit()

    await _run_import(session, [_seerr_request(294, 211288)], deleted_upstream_ids=frozenset({279}))

    await session.refresh(show)
    assert show.status == RequestStatus.PENDING
    assert show.overseerr_request_id == 294
    assert await _links(session) == {294: show.id}


@pytest.mark.asyncio
async def test_deleting_every_linked_request_denies_show_without_reimport(session):
    show = await _add_request(session, tmdb_id=211288, overseerr_request_id=279)
    await add_overseerr_link(session, show, 294)
    await session.commit()

    await _run_import(session, [], deleted_upstream_ids=frozenset({279, 294}))
    await session.refresh(show)
    assert show.status == RequestStatus.DENIED

    # Seerr can still list a request it has deleted for a short time; it must not
    # come back as a new row.
    await _run_import(session, [_seerr_request(294, 211288)])
    assert len((await session.execute(select(RequestModel))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_approval_approves_every_linked_seerr_request(session):
    show = await _add_request(session, tmdb_id=136315, overseerr_request_id=32)
    await add_overseerr_link(session, show, 264)
    await session.commit()

    service = MagicMock()
    service.approve_request = AsyncMock(return_value=True)
    with patch(
        "app.siftarr.services.lifecycle.overseerr_sync_service.OverseerrService",
        return_value=service,
    ):
        assert await approve_overseerr_request_best_effort(session, show, reason="test")

    approved = sorted(call.args[0] for call in service.approve_request.await_args_list)
    assert approved == [32, 264]


@pytest.mark.asyncio
async def test_webhook_season_request_links_to_existing_show(session):
    from fastapi import BackgroundTasks

    from app.siftarr.routers.webhooks import OverseerrWebhookPayload, receive_overseerr_webhook

    show = await _add_request(session, tmdb_id=211288, overseerr_request_id=279)
    payload = OverseerrWebhookPayload.model_validate(
        {
            "event": "mediarequested",
            "media": {"media_type": "tv", "tmdbid": 211288, "requested_seasons": [4]},
            "request": {"id": 294},
        }
    )
    background = BackgroundTasks()

    result = await receive_overseerr_webhook(payload, background, session)

    assert result == {"status": "linked", "request_id": show.id}
    assert len((await session.execute(select(RequestModel))).scalars().all()) == 1
    assert await _links(session) == {279: show.id, 294: show.id}
    # The show is already active, so the new link must not start another search.
    assert background.tasks == []
