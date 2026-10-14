"""Tests for rules router behavior."""

from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.siftarr.database import get_db
from app.siftarr.models.rule import RuleType, TVTarget
from app.siftarr.routers import rules
from app.siftarr.services.decisions.rule_service import RuleImportPreview
from tests.factories import make_rule


@pytest.fixture
async def client(db):
    app = FastAPI()
    app.include_router(rules.router)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def test_rules_page_renders_rules_wizards_and_import_export(client, db):
    await make_rule(db, name="Reject CAM", pattern="CAM", priority=2)
    scorer = await make_rule(
        db, name="Prefer x265", rule_type=RuleType.SCORER, pattern="x265", score=25, priority=1
    )
    await make_rule(
        db,
        name="Movie Size",
        rule_type=RuleType.SIZE_LIMIT,
        pattern="size_limit",
        media_scope="movie",
        min_size_gb=1.0,
        max_size_gb=5.0,
        priority=3,
    )
    await db.commit()

    response = await client.get("/rules")

    assert response.status_code == 200
    html = response.text
    for text in ("Reject CAM", "Prefer x265", "Movie Size", "+25 points", "Min 1.0 GB"):
        assert text in html
    assert f'action="/rules/{scorer.id}/toggle"' in html
    assert f'action="/rules/{scorer.id}/delete"' in html
    assert f'id="rule-wizard-edit-{scorer.id}"' in html
    assert 'id="rule-wizard-new-scorer"' in html
    assert 'id="rule-import-export-modal"' in html
    assert 'href="/rules/export"' in html
    assert 'action="/rules/import-preview"' in html
    assert "Multi-title Rule Tester" in html


async def test_static_post_routes_are_not_captured_by_dynamic_rule_update(client):
    """POST /rules/import-preview must reach its handler, not /rules/{rule_id}."""
    response = await client.post("/rules/import-preview", data={"import_payload": "bad"})

    assert response.status_code == 200
    assert "Invalid JSON" in response.text


async def test_rule_tester_results_render_per_title(client, db):
    await make_rule(db, name="Prefer x265", rule_type=RuleType.SCORER, pattern="x265", score=25)
    await db.commit()

    response = await client.post(
        "/rules/test",
        data={
            "title": ["Movie.2024.1080p.x265", "Movie.2024.1080p"],
            "media_type": ["movie", "movie"],
            "size_gb": ["1.5", ""],
        },
    )

    assert response.status_code == 200
    assert "Rule test results" in response.text
    assert "Prefer x265" in response.text
    assert 'value="Movie.2024.1080p.x265"' in response.text


class TestRulesRouter:
    """Focused tests for size-limit rule routing."""

    @staticmethod
    def _rule(
        rule_id: int,
        name: str,
        rule_type: RuleType,
        priority: int,
        pattern: str = "test",
        media_scope: str = "both",
        score: int = 0,
        min_size_gb: float | None = None,
        max_size_gb: float | None = None,
        is_enabled: bool = True,
        tv_target: TVTarget | None = None,
        description: str | None = None,
    ) -> MagicMock:
        rule = MagicMock()
        rule.id = rule_id
        rule.name = name
        rule.rule_type = rule_type
        rule.priority = priority
        rule.pattern = pattern
        rule.media_scope = media_scope
        rule.score = score
        rule.min_size_gb = min_size_gb
        rule.max_size_gb = max_size_gb
        rule.is_enabled = is_enabled
        rule.tv_target = tv_target
        rule.description = description
        return rule

    @pytest.mark.asyncio
    async def test_test_rule_accepts_multiple_rows_with_media_type_and_size(self, monkeypatch):
        """Rule testing should evaluate each submitted title row independently."""
        size_limit = self._rule(
            1,
            "Movie Size",
            RuleType.SIZE_LIMIT,
            1,
            pattern="size_limit",
            media_scope="movie",
            min_size_gb=1.0,
            max_size_gb=2.0,
        )
        scorer = self._rule(
            2,
            "Prefer x265",
            RuleType.SCORER,
            2,
            pattern="x265",
            score=25,
        )
        all_rules = [size_limit, scorer]

        service = MagicMock()
        service.ensure_default_rules = AsyncMock()
        service.get_all_rules = AsyncMock(return_value=all_rules)
        service.get_all_rules_by_type = AsyncMock(
            side_effect=lambda rule_type: {
                RuleType.EXCLUSION: [],
                RuleType.REQUIREMENT: [],
                RuleType.SCORER: [scorer],
                RuleType.SIZE_LIMIT: [size_limit],
            }[rule_type]
        )
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        captured = {}
        monkeypatch.setattr(
            rules.templates,
            "TemplateResponse",
            lambda request, template, context: (
                captured.setdefault("context", context) or MagicMock()
            ),
        )

        await rules.test_rule(
            request=MagicMock(),
            title=["Movie.2024.1080p.x265", "Large.Movie.2024.1080p", "Unsized.Movie.2024"],
            media_type=["movie", "movie", "movie"],
            size_gb=["1.5", "3.0", "  "],
            db=AsyncMock(),
        )

        context = cast(dict, captured["context"])
        results = context["test_results"]
        assert len(results) == 3
        assert results[0]["title"] == "Movie.2024.1080p.x265"
        assert results[0]["media_type"] == "movie"
        assert results[0]["size_gb"] == 1.5
        assert results[0]["passed"] is True
        assert results[0]["total_score"] == 25
        assert [match.rule_name for match in results[0]["matched_rules"]] == [
            "Movie Size",
            "Prefer x265",
        ]
        assert results[1]["passed"] is False
        assert results[1]["rejection_reason"] == "Size 3.00 GB above maximum 2.00 GB"
        assert context["test_result"] == results[0]
        # Blank size fields from repeated form rows normalise to None instead of failing.
        assert context["test_rows"][2] == {
            "title": "Unsized.Movie.2024",
            "media_type": "movie",
            "size_gb": None,
        }
        assert results[2]["size_gb"] is None

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
    async def test_create_and_update_pass_tv_target_to_service(self, monkeypatch):
        """Create and update handlers should pass the parsed TV target through to the service."""
        existing_rule = MagicMock()
        existing_rule.rule_type = RuleType.SIZE_LIMIT

        service = MagicMock()
        service.create_rule = AsyncMock()
        service.get_rule_by_id = AsyncMock(return_value=existing_rule)
        service.update_rule = AsyncMock()
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        created = await rules.create_rule(
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
        updated = await rules.update_rule(
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

        assert created.status_code == updated.status_code == 303
        assert service.create_rule.await_args_list[0].kwargs["tv_target"] == TVTarget.SEASON_PACK
        assert service.update_rule.await_args_list[0].kwargs["tv_target"] == TVTarget.SEASON_PACK

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
        service.get_all_rules = AsyncMock(return_value=[])
        service.get_all_rules_by_type = AsyncMock(return_value=[])
        service.preview_import_rules_with_existing = AsyncMock(
            side_effect=ValueError("Invalid JSON: bad payload")
        )
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
        service.get_all_rules = AsyncMock(return_value=[])
        service.get_all_rules_by_type = AsyncMock(return_value=[])
        preview = RuleImportPreview(version=1, replace_count=1, rules=[])
        service.preview_import_rules_with_existing = AsyncMock(return_value=preview)
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

        service.preview_import_rules_with_existing.assert_awaited_once_with(
            '{"version":1,"rules":[{"name":"x"}]}'
        )
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
        service = MagicMock()
        service.replace_rules_from_payload_selection = AsyncMock()
        monkeypatch.setattr(rules, "RuleService", lambda db: service)

        response = await rules.import_rules_apply(
            import_payload='{"version":1,"rules":[{"name":"x"}]}',
            confirm_replace="yes",
            keep_rule=["imported:0"],
            db=AsyncMock(),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/rules"
        service.replace_rules_from_payload_selection.assert_awaited_once_with(
            '{"version":1,"rules":[{"name":"x"}]}', ["imported:0"]
        )
