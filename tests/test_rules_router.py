"""Tests for rules router behavior."""

from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.responses import RedirectResponse

from app.siftarr.models.rule import RuleType, TVTarget
from app.siftarr.routers import rules
from app.siftarr.services.rule_service import RuleImportPreview


class TestRulesRouter:
    """Focused tests for size-limit rule routing."""

    def test_validate_rule_input_rejects_missing_tv_target_for_tv_size_rule(self):
        """TV size rules should require explicit episode-vs-pack targeting."""
        with pytest.raises(HTTPException) as exc_info:
            rules._validate_rule_input(
                RuleType.SIZE_LIMIT,
                "size_limit",
                1.0,
                5.0,
                "tv",
                None,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "TV size-limit rules must target episodes or season packs."

    @pytest.mark.asyncio
    async def test_create_rule_passes_tv_target_to_service(self, monkeypatch):
        """Create handler should pass parsed TV target through to the service."""
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
            tv_target="season_pack",
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
            tv_target=TVTarget.SEASON_PACK,
            description="desc",
        )

    @pytest.mark.asyncio
    async def test_update_rule_passes_tv_target_to_service(self, monkeypatch):
        """Update handler should pass parsed TV target through to the service."""
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
            tv_target="season_pack",
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
            tv_target=TVTarget.SEASON_PACK,
            description="desc",
        )

    @pytest.mark.asyncio
    async def test_export_rules_returns_json_attachment(self, monkeypatch):
        """Export endpoint should return the versioned JSON payload."""
        service = MagicMock()
        service.export_rules_json = AsyncMock(return_value='{"version":1,"rules":[]}')
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        response = await rules.export_rules(db=AsyncMock())

        assert response.media_type == "application/json"
        assert (
            response.headers["content-disposition"] == 'attachment; filename="siftarr-rules.json"'
        )

    @pytest.mark.asyncio
    async def test_import_preview_surfaces_validation_errors(self, monkeypatch):
        """Preview endpoint should render clear validation feedback."""
        service = MagicMock()
        service.ensure_default_rules = AsyncMock()
        service.get_all_rules_by_type = AsyncMock(return_value=[])
        service.preview_import_rules.side_effect = ValueError("Invalid JSON: bad payload")
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        captured = {}
        monkeypatch.setattr(
            rules.templates,
            "TemplateResponse",
            lambda request, template, context: (
                captured.setdefault("context", context) or MagicMock()
            ),
        )

        await rules.import_rules_preview(
            request=MagicMock(),
            import_payload="bad",
            db=AsyncMock(),
        )

        context = cast(dict, captured["context"])
        assert context["import_error"] == "Invalid JSON: bad payload"

    @pytest.mark.asyncio
    async def test_import_preview_accepts_uploaded_json_file(self, monkeypatch):
        """Preview should accept uploaded JSON files in addition to pasted payloads."""
        service = MagicMock()
        service.ensure_default_rules = AsyncMock()
        service.get_all_rules_by_type = AsyncMock(return_value=[])
        preview = RuleImportPreview(version=1, replace_count=1, rules=[])
        service.preview_import_rules = MagicMock(return_value=preview)
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        captured = {}
        monkeypatch.setattr(
            rules.templates,
            "TemplateResponse",
            lambda request, template, context: (
                captured.setdefault("context", context) or MagicMock()
            ),
        )

        upload = rules.UploadFile(
            filename="rules.json",
            file=BytesIO(b'{"version":1,"rules":[{"name":"x"}]}'),
        )
        await rules.import_rules_preview(
            request=MagicMock(),
            import_payload=None,
            import_file=upload,
            db=AsyncMock(),
        )

        service.preview_import_rules.assert_called_once_with('{"version":1,"rules":[{"name":"x"}]}')
        context = cast(dict, captured["context"])
        assert context["import_preview"] == preview

    @pytest.mark.asyncio
    async def test_import_preview_rejects_non_json_upload(self):
        """Preview should reject uploads without a .json filename."""
        upload = rules.UploadFile(filename="rules.txt", file=BytesIO(b"{}"))

        with pytest.raises(HTTPException) as exc_info:
            await rules.import_rules_preview(
                request=MagicMock(),
                import_payload=None,
                import_file=upload,
                db=AsyncMock(),
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Uploaded import file must be a .json file."

    @pytest.mark.asyncio
    async def test_import_apply_requires_confirmation(self):
        """Applying an import should require explicit confirmation."""
        with pytest.raises(HTTPException) as exc_info:
            await rules.import_rules_apply(
                import_payload='{"version":1,"rules":[]}',
                confirm_replace="no",
                db=AsyncMock(),
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_import_apply_replaces_rules_after_preview_validation(self, monkeypatch):
        """Apply endpoint should validate then replace the current ruleset."""
        preview = RuleImportPreview(version=1, replace_count=1, rules=[])
        service = MagicMock()
        service.preview_import_rules = MagicMock(return_value=preview)
        service.replace_rules_from_preview = AsyncMock()
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        response = await rules.import_rules_apply(
            import_payload='{"version":1,"rules":[{"name":"x"}]}',
            confirm_replace="yes",
            db=AsyncMock(),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/rules"
        service.replace_rules_from_preview.assert_awaited_once_with(preview)
