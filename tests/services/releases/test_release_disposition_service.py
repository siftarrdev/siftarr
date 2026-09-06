from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.siftarr.models import Base
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.decisions.decision_pipeline import get_best_passing
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_disposition_service import ReleaseDispositionService
from app.siftarr.services.releases.release_validation_service import apply_dispositions
from app.siftarr.services.releases.staging_service import StagingService


def _release(*, info_hash=None):
    return ProwlarrRelease(
        title="Film.2024.1080p",
        size=123,
        seeders=2,
        leechers=0,
        download_url="https://x",
        magnet_url=None,
        info_hash=info_hash,
        indexer="idx",
    )


@pytest.mark.asyncio
async def test_dispositions_are_durable_expiring_and_request_scoped():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        first = Request(external_id="a", media_type=MediaType.MOVIE, title="Film")
        second = Request(external_id="b", media_type=MediaType.MOVIE, title="Film")
        db.add_all([first, second])
        await db.flush()
        service = ReleaseDispositionService(db)
        disposition = await service.record_rejection(
            first.id, _release(), media_type=first.media_type, reason="no"
        )
        await db.commit()
        assert await service.blocked(
            first.id, _release(info_hash="abc"), media_type=first.media_type
        )
        assert await service.blocked(second.id, _release(), media_type=second.media_type) is None
        # Once a known hash is observed, indexer renaming/size changes must not
        # let the same rejected torrent through under another metadata key.
        known = _release(info_hash="a" * 40)
        assert await service.blocked(first.id, known, media_type=first.media_type)
        await db.commit()
        renamed = known.model_copy(
            update={"title": "Renamed Film", "size": 456, "indexer": "other"}
        )
        assert await service.blocked(first.id, renamed, media_type=first.media_type)
        blocked = await service.blocked_keys(first.id, [renamed], media_type=first.media_type)
        assert id(renamed) in blocked
        disposition.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        await db.commit()
        assert await service.blocked(first.id, _release(), media_type=first.media_type) is None
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_reply", ["a" * 40, "Ok."])
async def test_direct_handoff_tracks_one_approved_row_idempotently(client_reply):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        request = Request(external_id="direct", media_type=MediaType.MOVIE, title="Film")
        db.add(request)
        await db.flush()
        release = Release(
            request_id=request.id,
            title="Film.2024.1080p",
            size=123,
            seeders=2,
            leechers=0,
            download_url="magnet:?xt=urn:btih:abc",
            indexer="idx",
            score=1,
            passed_rules=True,
        )
        db.add(release)
        await db.commit()
        qbit = AsyncMock(add_torrent=AsyncMock(return_value=client_reply))
        settings = MagicMock(staging_mode_enabled=False)
        with (
            patch(
                "app.siftarr.services.releases.staging_service.get_settings", return_value=settings
            ),
            patch(
                "app.siftarr.services.releases.staging_service.QbittorrentService",
                return_value=qbit,
            ),
        ):
            await StagingService(db).use_releases(
                request, [release], selection_source="manual", identity_override=True
            )
            request.status = RequestStatus.PENDING
            await StagingService(db).use_releases(
                request, [release], selection_source="manual", identity_override=True
            )
        rows = list(
            (
                await db.execute(
                    select(StagedTorrent).where(StagedTorrent.request_id == request.id)
                )
            ).scalars()
        )
        assert len(rows) == 1
        assert rows[0].status == "approved"
        assert rows[0].source_release_id == release.id
        assert rows[0].identity_override is True
        assert rows[0].info_hash == ("a" * 40 if client_reply != "Ok." else None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_blocked_top_candidate_allows_next_best_to_win():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        request = Request(external_id="next", media_type=MediaType.MOVIE, title="Film")
        db.add(request)
        await db.flush()
        top, alternate = _release(), _release()
        alternate.title = "Film.2024.720p"
        await ReleaseDispositionService(db).record_rejection(
            request.id, top, media_type=request.media_type, reason="operator rejected"
        )
        evaluations = [
            ReleaseEvaluation(release=top, passed=True, total_score=100, matches=[]),
            ReleaseEvaluation(release=alternate, passed=True, total_score=50, matches=[]),
        ]
        await apply_dispositions(db, request, evaluations)
        assert evaluations[0].rejection_reason == "Release blocked: operator rejected"
        assert get_best_passing(evaluations) is evaluations[1]
    await engine.dispose()
