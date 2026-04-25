"""Tests for rules router behavior."""

from io import BytesIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.responses import RedirectResponse

from app.siftarr.models.rule import RuleType, TVTarget
from app.siftarr.routers import rules
from app.siftarr.services.rule_service import RuleImportPreview


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
    async def test_list_rules_context_includes_unified_ordered_and_grouped_rules(self, monkeypatch):
        """Rules page should expose the future unified list while grouped data remains."""
        exclusion = self._rule(1, "Reject CAM", RuleType.EXCLUSION, 2)
        scorer = self._rule(2, "Prefer x265", RuleType.SCORER, 1)
        size_limit = self._rule(3, "Movie Size", RuleType.SIZE_LIMIT, 3)
        ordered_rules = [scorer, exclusion, size_limit]

        service = MagicMock()
        service.ensure_default_rules = AsyncMock()
        service.get_all_rules = AsyncMock(return_value=ordered_rules)
        service.get_all_rules_by_type = AsyncMock(
            side_effect=lambda rule_type: {
                RuleType.EXCLUSION: [exclusion],
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

        await rules.list_rules(request=MagicMock(), db=AsyncMock())

        context = cast(dict, captured["context"])
        assert context["rules"] == ordered_rules
        assert context["exclusion_rules"] == [exclusion]
        assert context["requirement_rules"] == []
        assert context["scorer_rules"] == [scorer]
        assert context["size_limit_rules"] == [size_limit]

    def test_rules_template_renders_unified_table_with_row_actions(self):
        """Rules template should show one unified list without rule-type tabs."""
        exclusion = self._rule(1, "Reject CAM", RuleType.EXCLUSION, 2, pattern="CAM")
        scorer = self._rule(2, "Prefer x265", RuleType.SCORER, 1, pattern="x265", score=25)
        size_limit = self._rule(
            3,
            "Movie Size",
            RuleType.SIZE_LIMIT,
            3,
            min_size_gb=1.0,
            max_size_gb=5.0,
            media_scope="movie",
        )

        html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [scorer, exclusion, size_limit],
                "exclusion_rules": [exclusion],
                "requirement_rules": [],
                "scorer_rules": [scorer],
                "size_limit_rules": [size_limit],
            }
        )

        assert "All Rules" in html
        assert "Priority" in html
        assert "Pattern / size range" in html
        assert "+25 points" in html
        assert "Min 1.0 GB" in html
        assert 'action="/rules/2/toggle"' in html
        assert 'href="#rule-wizard-edit-2"' in html
        assert 'href="/rules/2/edit"' in html
        assert 'action="/rules/2/delete"' in html
        assert "Delete this rule?" in html
        assert "showRuleTab" not in html
        assert "rule-tab" not in html

    def test_rules_template_renders_large_rule_wizard_for_create_and_edit(self):
        """Create and edit actions should open prefilled modal rule wizards."""
        scorer = self._rule(
            2,
            "Prefer x265",
            RuleType.SCORER,
            1,
            pattern="x265",
            score=25,
            description="Prefer efficient encodes",
        )

        html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [scorer],
                "exclusion_rules": [],
                "requirement_rules": [],
                "scorer_rules": [scorer],
                "size_limit_rules": [],
            }
        )

        assert 'href="#rule-wizard-new-exclusion"' in html
        assert 'id="rule-wizard-new-scorer"' in html
        assert 'action="/rules"' in html
        assert 'name="rule_type" value="scorer"' in html
        assert 'href="#rule-wizard-edit-2"' in html
        assert 'id="rule-wizard-edit-2"' in html
        assert 'action="/rules/2"' in html
        assert 'value="Prefer x265"' in html
        assert 'value="x265"' in html
        assert "Rule type cannot be changed after creation." in html
        assert "Test before saving" in html
        assert "Test Entered Rule" in html
        assert "data-rule-wizard-test-button" in html
        assert "max-w-[75vw]" in html

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
            title=["Movie.2024.1080p.x265", "Large.Movie.2024.1080p"],
            media_type=["movie", "movie"],
            size_gb=["1.5", "3.0"],
            db=AsyncMock(),
        )

        context = cast(dict, captured["context"])
        results = context["test_results"]
        assert len(results) == 2
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

    def test_static_post_routes_are_registered_before_dynamic_rule_update(self):
        """Static POST routes should not be captured by /rules/{rule_id}."""
        post_paths = [
            route.path
            for route in rules.router.routes
            if isinstance(route, APIRoute) and "POST" in route.methods
        ]

        dynamic_index = post_paths.index("/rules/{rule_id}")
        assert post_paths.index("/rules/test") < dynamic_index
        assert post_paths.index("/rules/import-preview") < dynamic_index
        assert post_paths.index("/rules/import-apply") < dynamic_index

    @pytest.mark.asyncio
    async def test_test_rule_normalizes_blank_size_values(self, monkeypatch):
        """Blank size fields from repeated form rows should not cause 422-style parsing failures."""
        scorer = self._rule(
            2,
            "Prefer x265",
            RuleType.SCORER,
            1,
            pattern="x265",
            score=25,
        )

        service = MagicMock()
        service.ensure_default_rules = AsyncMock()
        service.get_all_rules = AsyncMock(return_value=[scorer])
        service.get_all_rules_by_type = AsyncMock(
            side_effect=lambda rule_type: {
                RuleType.EXCLUSION: [],
                RuleType.REQUIREMENT: [],
                RuleType.SCORER: [scorer],
                RuleType.SIZE_LIMIT: [],
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
            title=["Movie.2024.1080p.x265", "Movie.2024.1080p"],
            media_type=["movie", "movie"],
            size_gb=["", "  "],
            db=AsyncMock(),
        )

        context = cast(dict, captured["context"])
        assert context["test_rows"] == [
            {"title": "Movie.2024.1080p.x265", "media_type": "movie", "size_gb": None},
            {"title": "Movie.2024.1080p", "media_type": "movie", "size_gb": None},
        ]
        assert [result["size_gb"] for result in context["test_results"]] == [None, None]

    def test_rules_template_renders_multi_title_tester_with_preserved_results(self):
        """Rules template should preserve submitted rows and compare per-title results."""
        html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [],
                "exclusion_rules": [],
                "requirement_rules": [],
                "scorer_rules": [],
                "size_limit_rules": [],
                "test_rows": [
                    {"title": "Movie.2024.1080p.x265", "media_type": "movie", "size_gb": 1.5},
                    {"title": "Large.Movie.2024.1080p", "media_type": "movie", "size_gb": 3.0},
                ],
                "test_results": [
                    {
                        "title": "Movie.2024.1080p.x265",
                        "media_type": "movie",
                        "size_gb": 1.5,
                        "passed": True,
                        "rejection_reason": None,
                        "total_score": 25,
                        "matched_rules": [SimpleNamespace(rule_name="Prefer x265", score_delta=25)],
                        "rejection_rules": [],
                    },
                    {
                        "title": "Large.Movie.2024.1080p",
                        "media_type": "movie",
                        "size_gb": 3.0,
                        "passed": False,
                        "rejection_reason": "Size 3.00 GB above maximum 2.00 GB",
                        "total_score": 0,
                        "matched_rules": [],
                        "rejection_rules": [SimpleNamespace(rule_name="Movie Size", score_delta=0)],
                    },
                ],
            }
        )

        assert "Multi-title Rule Tester" in html
        assert 'aria-label="Release title 1"' in html
        assert 'aria-label="Release title 2"' in html
        assert 'value="Movie.2024.1080p.x265"' in html
        assert 'value="1.5"' in html
        assert "Rule test results" in html
        assert "Prefer x265" in html
        assert "+25" in html
        assert "Size 3.00 GB above maximum 2.00 GB" in html
        assert "Movie Size" in html
        assert "addRuleTestRow" in html

    def test_rules_template_moves_import_export_into_modal(self):
        """Import/export controls should be hidden in a large modal until opened."""
        html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [],
                "exclusion_rules": [],
                "requirement_rules": [],
                "scorer_rules": [],
                "size_limit_rules": [],
            }
        )

        assert "openRuleImportExportModal" in html
        assert 'data-modal-open="rule-import-export-modal">Import / Export</button>' in html
        assert 'id="rule-import-export-modal"' in html
        assert (
            'class="rule-modal fixed inset-0 z-50 items-center justify-center bg-black/70' in html
        )
        assert 'role="dialog" aria-modal="true" tabindex="-1"' in html
        assert 'href="/rules/export"' in html
        assert 'action="/rules/import-preview"' in html

        preview_html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [],
                "exclusion_rules": [],
                "requirement_rules": [],
                "scorer_rules": [],
                "size_limit_rules": [],
                "import_payload": '{"version":1,"rules":[]}',
                "import_preview": RuleImportPreview(version=1, replace_count=0, rules=[]),
            }
        )
        assert 'action="/rules/import-apply"' in preview_html

    def test_rules_template_reopens_import_export_modal_for_preview_errors(self):
        """Import validation feedback should render with the modal visible after POST."""
        html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [],
                "exclusion_rules": [],
                "requirement_rules": [],
                "scorer_rules": [],
                "size_limit_rules": [],
                "import_payload": "bad",
                "import_error": "Invalid JSON: bad payload",
            }
        )

        assert 'id="rule-import-export-modal" class="rule-modal fixed inset-0 z-50' in html
        assert "is-open" in html
        assert "Invalid JSON: bad payload" in html
        assert ">bad</textarea>" in html

    def test_rules_template_includes_accessible_modal_controls(self):
        """Rules modals should expose dialog semantics and keyboard/backdrop close hooks."""
        scorer = self._rule(2, "Prefer x265", RuleType.SCORER, 1, pattern="x265", score=25)

        html = rules.templates.get_template("rules.html").render(
            {
                "request": SimpleNamespace(url=SimpleNamespace(path="/rules")),
                "rules": [scorer],
                "exclusion_rules": [],
                "requirement_rules": [],
                "scorer_rules": [scorer],
                "size_limit_rules": [],
            }
        )

        assert 'role="dialog" aria-modal="true"' in html
        assert 'aria-labelledby="rule-wizard-edit-2-title"' in html
        assert 'data-modal-close aria-label="Close rule wizard" tabindex="-1"' in html
        assert 'data-modal-open="rule-wizard-edit-2"' in html
        assert "event.key === 'Escape'" in html
        assert "trapModalFocus" in html

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
