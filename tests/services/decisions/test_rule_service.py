"""Tests for RuleService."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.config import reload_settings
from app.siftarr.models.rule import Rule, RuleType, TVTarget
from app.siftarr.services.decisions.rule_service import RuleImportPreview, RuleService


class TestRuleService:
    """Test cases for RuleService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        return db

    @pytest.fixture
    def service(self, mock_db):
        """Create a RuleService instance."""
        return RuleService(mock_db)

    @pytest.fixture
    def real_service(self, db):
        return RuleService(db)

    @pytest.mark.asyncio
    async def test_create_and_query_rules(self, real_service):
        """Created rules are persisted, ordered by priority, and filterable by type."""
        low = await real_service.create_rule(
            name="Low", rule_type=RuleType.SCORER, pattern="x265", score=5, priority=2
        )
        first = await real_service.create_rule(
            name="First", rule_type=RuleType.EXCLUSION, pattern="CAM|TS", priority=1
        )
        disabled = await real_service.create_rule(
            name="Off", rule_type=RuleType.EXCLUSION, pattern="SCR", priority=3, is_enabled=False
        )

        assert [r.name for r in await real_service.get_all_rules()] == ["First", "Low", "Off"]
        assert [r.id for r in await real_service.get_all_rules_by_type(RuleType.EXCLUSION)] == [
            first.id,
            disabled.id,
        ]
        assert (await real_service.get_rule_by_id(low.id)) is not None
        assert await real_service.get_rule_by_id(999) is None

    @pytest.mark.asyncio
    async def test_update_toggle_and_delete_rule(self, real_service):
        rule = await real_service.create_rule(
            name="Old", rule_type=RuleType.SCORER, pattern="x265", score=10
        )

        updated = await real_service.update_rule(rule.id, score=50)
        assert updated is not None
        assert (updated.name, updated.pattern, updated.score) == ("Old", "x265", 50)

        toggled = await real_service.toggle_rule(rule.id)
        assert toggled is not None
        assert toggled.is_enabled is False

        assert await real_service.delete_rule(rule.id) is True
        assert await real_service.get_rule_by_id(rule.id) is None

    @pytest.mark.asyncio
    async def test_missing_rule_operations_return_empty_results(self, real_service):
        assert await real_service.update_rule(rule_id=999, name="New") is None
        assert await real_service.toggle_rule(999) is None
        assert await real_service.delete_rule(999) is False

    @pytest.mark.asyncio
    async def test_seed_default_rules(self, mock_db, service):
        """Without a configured file, default seeding leaves rules empty."""
        mock_result_empty = MagicMock()
        mock_result_empty.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result_empty

        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch.object(service, "get_all_rules", return_value=[]):
            result = await service.seed_default_rules()

            assert result == []
            mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_default_rules_from_configured_file(
        self, mock_db, service, monkeypatch, tmp_path
    ):
        """Configured rules.json should seed through import preview schema."""
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "name": "Imported scorer",
                            "rule_type": "scorer",
                            "media_scope": "movie",
                            "tv_target": None,
                            "pattern": "1080p",
                            "score": 25,
                            "min_size_gb": None,
                            "max_size_gb": None,
                            "priority": 1,
                            "is_enabled": True,
                            "description": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("SIFTARR_DEFAULT_RULES_PATH", str(rules_path))
        reload_settings()

        try:
            with patch.object(service, "get_all_rules", return_value=[]):
                result = await service.seed_default_rules()
        finally:
            monkeypatch.delenv("SIFTARR_DEFAULT_RULES_PATH", raising=False)
            reload_settings()

        assert len(result) == 1
        assert result[0].name == "Imported scorer"
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_seed_default_rules_without_configured_file_leaves_rules_empty(
        self, mock_db, service, monkeypatch
    ):
        monkeypatch.delenv("SIFTARR_DEFAULT_RULES_PATH", raising=False)
        reload_settings()

        with patch.object(service, "get_all_rules", return_value=[]):
            result = await service.seed_default_rules()

        assert result == []
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_default_rules_does_not_replace_existing_rules(
        self, mock_db, service, monkeypatch, tmp_path
    ):
        rules_path = tmp_path / "missing.json"
        monkeypatch.setenv("SIFTARR_DEFAULT_RULES_PATH", str(rules_path))
        reload_settings()
        existing_rules = [MagicMock(spec=Rule)]

        try:
            with patch.object(service, "get_all_rules", return_value=existing_rules):
                result = await service.seed_default_rules()
        finally:
            monkeypatch.delenv("SIFTARR_DEFAULT_RULES_PATH", raising=False)
            reload_settings()

        assert result == existing_rules
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_default_rules_fails_for_invalid_configured_file(
        self, service, monkeypatch, tmp_path
    ):
        rules_path = tmp_path / "rules.json"
        rules_path.write_text('{"version": 1, "rules": []}', encoding="utf-8")
        monkeypatch.setenv("SIFTARR_DEFAULT_RULES_PATH", str(rules_path))
        reload_settings()

        try:
            with (
                patch.object(service, "get_all_rules", return_value=[]),
                pytest.raises(RuntimeError, match="Configured default rules file is invalid"),
            ):
                await service.seed_default_rules()
        finally:
            monkeypatch.delenv("SIFTARR_DEFAULT_RULES_PATH", raising=False)
            reload_settings()

    @pytest.mark.asyncio
    async def test_seed_default_rules_fails_for_missing_configured_file(
        self, service, monkeypatch, tmp_path
    ):
        rules_path = tmp_path / "missing.json"
        monkeypatch.setenv("SIFTARR_DEFAULT_RULES_PATH", str(rules_path))
        reload_settings()

        try:
            with (
                patch.object(service, "get_all_rules", return_value=[]),
                pytest.raises(RuntimeError, match="Configured default rules file is unreadable"),
            ):
                await service.seed_default_rules()
        finally:
            monkeypatch.delenv("SIFTARR_DEFAULT_RULES_PATH", raising=False)
            reload_settings()

    @pytest.mark.asyncio
    async def test_seed_default_rules_already_exists(self, mock_db, service):
        """Test seeding when rules already exist."""
        existing_rules = [MagicMock(spec=Rule)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = existing_rules
        mock_db.execute.return_value = mock_result

        with patch.object(service, "get_all_rules", return_value=existing_rules):
            result = await service.seed_default_rules()

            assert result == existing_rules
            mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_export_rules_json_includes_versioned_tv_targeting(self, service):
        """Export should include the versioned schema and TV targeting fields."""
        exported_rule = MagicMock(spec=Rule)
        exported_rule.name = "TV Seasons Size"
        exported_rule.rule_type = RuleType.SIZE_LIMIT
        exported_rule.media_scope = "tv"
        exported_rule.tv_target = TVTarget.SEASON_PACK
        exported_rule.pattern = "size_limit"
        exported_rule.score = 0
        exported_rule.min_size_gb = 2.0
        exported_rule.max_size_gb = 15.0
        exported_rule.priority = 8
        exported_rule.is_enabled = True
        exported_rule.description = None

        with patch.object(service, "get_all_rules", return_value=[exported_rule]):
            payload = await service.export_rules_json()

        data = json.loads(payload)
        assert data["version"] == 1
        assert data["rules"][0]["tv_target"] == "season_pack"

    def test_preview_import_rules_returns_summary(self, service):
        """Preview should return the parsed replacement summary."""
        payload = json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "TV Episodes",
                        "rule_type": "size_limit",
                        "media_scope": "tv",
                        "tv_target": "episode",
                        "pattern": "size_limit",
                        "score": 0,
                        "min_size_gb": 0.1,
                        "max_size_gb": 1.5,
                        "priority": 7,
                        "is_enabled": True,
                        "description": None,
                    }
                ],
            }
        )

        preview = service.preview_import_rules(payload)
        assert isinstance(preview, RuleImportPreview)
        assert preview.replace_count == 1
        assert preview.rules[0]["tv_target"] == TVTarget.EPISODE

    @pytest.mark.asyncio
    async def test_preview_import_rules_with_existing_builds_diff_rows(self, service):
        """Preview should include existing/imported rows and status labels."""
        unchanged = MagicMock(spec=Rule)
        unchanged.name = "Keep same"
        unchanged.rule_type = RuleType.EXCLUSION
        unchanged.media_scope = "both"
        unchanged.tv_target = None
        unchanged.pattern = "CAM"
        unchanged.score = 0
        unchanged.min_size_gb = None
        unchanged.max_size_gb = None
        unchanged.priority = 0
        unchanged.is_enabled = True
        unchanged.description = None

        changed = MagicMock(spec=Rule)
        changed.name = "Prefer 1080p"
        changed.rule_type = RuleType.SCORER
        changed.media_scope = "movie"
        changed.tv_target = None
        changed.pattern = "1080p"
        changed.score = 10
        changed.min_size_gb = None
        changed.max_size_gb = None
        changed.priority = 1
        changed.is_enabled = True
        changed.description = None

        existing_only = MagicMock(spec=Rule)
        existing_only.name = "Local only"
        existing_only.rule_type = RuleType.REQUIREMENT
        existing_only.media_scope = "both"
        existing_only.tv_target = None
        existing_only.pattern = "WEB"
        existing_only.score = 0
        existing_only.min_size_gb = None
        existing_only.max_size_gb = None
        existing_only.priority = 2
        existing_only.is_enabled = True
        existing_only.description = None

        payload = json.dumps(
            {
                "version": 1,
                "rules": [
                    RuleService.serialize_rule(unchanged),
                    {**RuleService.serialize_rule(changed), "score": 25},
                    {
                        "name": "Imported only",
                        "rule_type": "exclusion",
                        "media_scope": "both",
                        "tv_target": None,
                        "pattern": "TS",
                        "score": 0,
                        "min_size_gb": None,
                        "max_size_gb": None,
                        "priority": 3,
                        "is_enabled": True,
                        "description": None,
                    },
                ],
            }
        )

        with patch.object(
            service, "get_all_rules", return_value=[unchanged, changed, existing_only]
        ):
            preview = await service.preview_import_rules_with_existing(payload)

        assert [row["status"] for row in preview.diff_rows] == [
            "unchanged",
            "changed",
            "new/imported-only",
            "existing-only",
        ]
        changed_row = preview.diff_rows[1]
        assert any(field["name"] == "score" and field["changed"] for field in changed_row["fields"])

    def test_build_selected_import_preview_accepts_existing_and_imported_choices(self, service):
        """Selections from both sides become the ordered replacement ruleset."""
        existing_rules = [
            {
                "name": "Existing keep",
                "rule_type": "requirement",
                "media_scope": "both",
                "tv_target": None,
                "pattern": "WEB",
                "score": 0,
                "min_size_gb": None,
                "max_size_gb": None,
                "priority": 5,
                "is_enabled": True,
                "description": None,
            }
        ]
        payload = json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "Imported keep",
                        "rule_type": "scorer",
                        "media_scope": "movie",
                        "tv_target": None,
                        "pattern": "2160p",
                        "score": 20,
                        "min_size_gb": None,
                        "max_size_gb": None,
                        "priority": 9,
                        "is_enabled": True,
                        "description": None,
                    }
                ],
            }
        )
        preview = service.preview_import_rules(payload)

        selected = service.build_selected_import_preview(
            preview, existing_rules, ["existing:0", "imported:0"]
        )

        assert [rule["name"] for rule in selected.rules] == ["Existing keep", "Imported keep"]
        assert [rule["priority"] for rule in selected.rules] == [0, 1]
        assert selected.rules[0]["rule_type"] == RuleType.REQUIREMENT

    def test_build_selected_import_preview_rejects_invalid_choice(self, service):
        payload = json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "Imported keep",
                        "rule_type": "scorer",
                        "media_scope": "movie",
                        "tv_target": None,
                        "pattern": "2160p",
                        "score": 20,
                        "min_size_gb": None,
                        "max_size_gb": None,
                        "priority": 9,
                        "is_enabled": True,
                        "description": None,
                    }
                ],
            }
        )
        preview = service.preview_import_rules(payload)

        with pytest.raises(ValueError, match="Invalid rule selection index"):
            service.build_selected_import_preview(preview, [], ["imported:9"])

    @pytest.mark.parametrize(
        ("version", "overrides", "message"),
        [
            (1, {"tv_target": None}, "must set tv_target"),
            (1, {"score": "0"}, "field 'score' must be an integer"),
            (1, {"is_enabled": "true"}, "field 'is_enabled' must be a boolean"),
            (
                1,
                {
                    "rule_type": "exclusion",
                    "media_scope": "both",
                    "tv_target": None,
                    "pattern": "[invalid",
                    "min_size_gb": None,
                    "max_size_gb": None,
                },
                "not a valid regex",
            ),
            (1, {"extra_field": "nope"}, "unsupported field"),
            (True, {}, "Unsupported rule import version"),
        ],
        ids=[
            "tv-size-without-target",
            "stringified-number",
            "non-boolean-flag",
            "invalid-regex",
            "unknown-field",
            "boolean-version",
        ],
    )
    def test_preview_import_rules_rejects_invalid_payload(
        self, service, version, overrides, message
    ):
        rule = {
            "name": "TV Episodes",
            "rule_type": "size_limit",
            "media_scope": "tv",
            "tv_target": "episode",
            "pattern": "size_limit",
            "score": 0,
            "min_size_gb": 0.1,
            "max_size_gb": 1.5,
            "priority": 7,
            "is_enabled": True,
            "description": None,
            **overrides,
        }
        payload = json.dumps({"version": version, "rules": [rule]})

        with pytest.raises(ValueError, match=message):
            service.preview_import_rules(payload)

    @pytest.mark.asyncio
    async def test_replace_rules_from_preview_replaces_current_rules(self, mock_db, service):
        """Applying a preview should delete current rows and replace them with imported rules."""
        existing_rule = MagicMock(spec=Rule)
        preview = RuleImportPreview(
            version=1,
            replace_count=1,
            rules=[
                {
                    "name": "Imported Rule",
                    "rule_type": RuleType.SCORER,
                    "media_scope": "movie",
                    "tv_target": None,
                    "pattern": "1080p",
                    "score": 10,
                    "min_size_gb": None,
                    "max_size_gb": None,
                    "priority": 1,
                    "is_enabled": True,
                    "description": None,
                }
            ],
        )

        with patch.object(service, "get_all_rules", return_value=[existing_rule]):
            mock_db.flush = AsyncMock()
            result = await service.replace_rules_from_preview(preview)

        mock_db.delete.assert_awaited_once_with(existing_rule)
        assert len(result) == 1
