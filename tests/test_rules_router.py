"""Tests for rules router behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.responses import RedirectResponse

from app.siftarr.models.rule import RuleType, SizeLimitMode
from app.siftarr.routers import rules


class TestRulesRouter:
    """Focused tests for size-limit rule routing."""

    def test_validate_rule_input_rejects_per_season_for_non_tv(self):
        """Per-season mode should only be accepted for TV size-limit rules."""
        with pytest.raises(HTTPException) as exc_info:
            rules._validate_rule_input(
                RuleType.SIZE_LIMIT,
                "size_limit",
                1.0,
                5.0,
                SizeLimitMode.PER_SEASON,
                "movie",
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Per-season size mode is only supported for TV rules."

    @pytest.mark.asyncio
    async def test_create_rule_passes_size_limit_mode_to_service(self, monkeypatch):
        """Create handler should pass parsed size-limit mode through to the service."""
        service = MagicMock()
        service.create_rule = AsyncMock()
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        response = await rules.create_rule(
            request=MagicMock(),
            name="TV Pack Limits",
            rule_type="size_limit",
            media_scope="tv",
            pattern="size_limit",
            score=0,
            min_size_gb=2.0,
            max_size_gb=8.0,
            size_limit_mode="per_season",
            description="desc",
            db=AsyncMock(),
        )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 303
        service.create_rule.assert_awaited_once_with(
            name="TV Pack Limits",
            rule_type=RuleType.SIZE_LIMIT,
            media_scope="tv",
            pattern="size_limit",
            score=0,
            min_size_gb=2.0,
            max_size_gb=8.0,
            size_limit_mode=SizeLimitMode.PER_SEASON,
            description="desc",
        )

    @pytest.mark.asyncio
    async def test_update_rule_passes_size_limit_mode_to_service(self, monkeypatch):
        """Update handler should pass parsed size-limit mode through to the service."""
        existing_rule = MagicMock()
        existing_rule.rule_type = RuleType.SIZE_LIMIT

        service = MagicMock()
        service.get_rule_by_id = AsyncMock(return_value=existing_rule)
        service.update_rule = AsyncMock()
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        response = await rules.update_rule(
            request=MagicMock(),
            rule_id=7,
            name="TV Pack Limits",
            media_scope="tv",
            pattern="size_limit",
            score=0,
            min_size_gb=1.5,
            max_size_gb=6.0,
            size_limit_mode="per_season",
            description="desc",
            db=AsyncMock(),
        )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 303
        service.update_rule.assert_awaited_once_with(
            rule_id=7,
            name="TV Pack Limits",
            media_scope="tv",
            pattern="size_limit",
            score=0,
            min_size_gb=1.5,
            max_size_gb=6.0,
            size_limit_mode=SizeLimitMode.PER_SEASON,
            description="desc",
        )
