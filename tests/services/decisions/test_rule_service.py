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

    @pytest.mark.asyncio
    async def test_get_all_rules(self, mock_db, service):
        """Test getting all rules."""
        mock_rules = [
            MagicMock(spec=Rule, id=1, name="Rule 1"),
            MagicMock(spec=Rule, id=2, name="Rule 2"),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_rules
        mock_db.execute.return_value = mock_result

        result = await service.get_all_rules()

        assert len(result) == 2
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_rules_by_type(self, mock_db, service):
        """Test getting rules filtered by type."""
        mock_rules = [
            MagicMock(spec=Rule, id=1, rule_type=RuleType.EXCLUSION),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_rules
        mock_db.execute.return_value = mock_result

        result = await service.get_rules_by_type(RuleType.EXCLUSION)

        assert len(result) == 1
        assert result[0].rule_type == RuleType.EXCLUSION

    @pytest.mark.asyncio
    async def test_get_exclusions(self, mock_db, service):
        """Test getting exclusion rules."""
        with patch.object(service, "get_rules_by_type", return_value=[]) as mock_get:
            await service.get_exclusions()
            mock_get.assert_called_once_with(RuleType.EXCLUSION)

    @pytest.mark.asyncio
    async def test_get_requirements(self, mock_db, service):
        """Test getting requirement rules."""
        with patch.object(service, "get_rules_by_type", return_value=[]) as mock_get:
            await service.get_requirements()
            mock_get.assert_called_once_with(RuleType.REQUIREMENT)

    @pytest.mark.asyncio
    async def test_get_scorers(self, mock_db, service):
        """Test getting scorer rules."""
        with patch.object(service, "get_rules_by_type", return_value=[]) as mock_get:
            await service.get_scorers()
            mock_get.assert_called_once_with(RuleType.SCORER)

    @pytest.mark.asyncio
    async def test_get_size_limits(self, mock_db, service):
        """Test getting size limit rules."""
        with patch.object(service, "get_rules_by_type", return_value=[]) as mock_get:
            await service.get_size_limits()
            mock_get.assert_called_once_with(RuleType.SIZE_LIMIT)

    @pytest.mark.asyncio
    async def test_get_rule_by_id(self, mock_db, service):
        """Test getting a rule by ID."""
        mock_rule = MagicMock(spec=Rule, id=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        mock_db.execute.return_value = mock_result

        result = await service.get_rule_by_id(1)

        assert result == mock_rule

    @pytest.mark.asyncio
    async def test_get_rule_by_id_not_found(self, mock_db, service):
        """Test getting a non-existent rule."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.get_rule_by_id(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_create_rule(self, mock_db, service):
        """Test creating a new rule."""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await service.create_rule(
            name="Test Rule",
            rule_type=RuleType.EXCLUSION,
            pattern="CAM|TS",
            score=0,
            min_size_gb=None,
            max_size_gb=None,
            tv_target=None,
            priority=1,
            is_enabled=True,
            description="Test description",
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_rule(self, mock_db, service):
        """Test updating an existing rule."""
        mock_rule = MagicMock(spec=Rule)
        mock_rule.name = "Old Name"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        mock_db.execute.return_value = mock_result

        await service.update_rule(
            rule_id=1,
            name="New Name",
            pattern="NEWPATTERN",
        )

        assert mock_rule.name == "New Name"
        assert mock_rule.pattern == "NEWPATTERN"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_rule_not_found(self, mock_db, service):
        """Test updating a non-existent rule."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.update_rule(rule_id=999, name="New Name")

        assert result is None

    @pytest.mark.asyncio
    async def test_update_rule_partial(self, mock_db, service):
        """Test partial update of a rule (only some fields)."""
        mock_rule = MagicMock(spec=Rule)
        mock_rule.name = "Original"
        mock_rule.pattern = "OriginalPattern"
        mock_rule.score = 10

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        mock_db.execute.return_value = mock_result

        await service.update_rule(rule_id=1, score=50)

        assert mock_rule.name == "Original"
        assert mock_rule.pattern == "OriginalPattern"
        assert mock_rule.score == 50

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_upsert_size_limit_rule_updates_tv_target_and_description(self, mock_db, service):
        """Upsert should persist TV target on existing size rules."""
        existing_rule = MagicMock(spec=Rule)

        with patch.object(service, "get_size_limit_rule_by_scope", return_value=existing_rule):
            result = await service.upsert_size_limit_rule(
                media_scope="tv",
                min_size_gb=2.5,
                max_size_gb=8.0,
                tv_target=TVTarget.SEASON_PACK,
            )

        assert result == existing_rule
        assert existing_rule.tv_target == TVTarget.SEASON_PACK
        assert existing_rule.description == "min 2.5 GB, max 8.0 GB, TV season packs only"
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(existing_rule)

    @pytest.mark.asyncio
    async def test_delete_rule(self, mock_db, service):
        """Test deleting a rule."""
        mock_rule = MagicMock(spec=Rule, id=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        mock_db.execute.return_value = mock_result

        result = await service.delete_rule(1)

        assert result is True
        mock_db.delete.assert_called_once_with(mock_rule)
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_rule_not_found(self, mock_db, service):
        """Test deleting a non-existent rule."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.delete_rule(999)

        assert result is False
        mock_db.delete.assert_not_called()

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
    async def test_toggle_rule(self, mock_db, service):
        """Test toggling a rule's enabled status."""
        mock_rule = MagicMock(spec=Rule)
        mock_rule.is_enabled = True
        mock_rule.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        mock_db.execute.return_value = mock_result

        await service.toggle_rule(1)

        assert mock_rule.is_enabled is False
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_toggle_rule_not_found(self, mock_db, service):
        """Test toggling a non-existent rule."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.toggle_rule(999)

        assert result is None

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

    def test_preview_import_rules_rejects_missing_tv_target_for_tv_size_rule(self, service):
        """Preview validation should reject TV size rules without explicit targeting."""
        payload = json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "TV Size",
                        "rule_type": "size_limit",
                        "media_scope": "tv",
                        "tv_target": None,
                        "pattern": "size_limit",
                        "score": 0,
                        "min_size_gb": 1.0,
                        "max_size_gb": 2.0,
                        "priority": 1,
                        "is_enabled": True,
                        "description": None,
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="must set tv_target"):
            service.preview_import_rules(payload)

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

        with patch.object(service, "get_all_rules", return_value=[unchanged, changed, existing_only]):
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

    def test_preview_import_rules_rejects_stringified_numeric_fields(self, service):
        """Numeric fields must be real JSON numbers, not strings."""
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
                        "score": "0",
                        "min_size_gb": 0.1,
                        "max_size_gb": 1.5,
                        "priority": 7,
                        "is_enabled": True,
                        "description": None,
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="field 'score' must be an integer"):
            service.preview_import_rules(payload)

    def test_preview_import_rules_rejects_non_boolean_enabled_flag(self, service):
        """Boolean fields must be true booleans."""
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
                        "is_enabled": "true",
                        "description": None,
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="field 'is_enabled' must be a boolean"):
            service.preview_import_rules(payload)

    def test_preview_import_rules_rejects_invalid_regex_for_non_size_rule(self, service):
        """Preview should fail invalid regexes before apply time."""
        payload = json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "Bad Regex",
                        "rule_type": "exclusion",
                        "media_scope": "both",
                        "tv_target": None,
                        "pattern": "[invalid",
                        "score": 0,
                        "min_size_gb": None,
                        "max_size_gb": None,
                        "priority": 1,
                        "is_enabled": True,
                        "description": None,
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="not a valid regex"):
            service.preview_import_rules(payload)

    def test_preview_import_rules_rejects_unknown_fields(self, service):
        """Preview should reject unsupported fields to keep the contract strict."""
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
                        "extra_field": "nope",
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="unsupported field"):
            service.preview_import_rules(payload)

    def test_preview_import_rules_rejects_boolean_version_value(self, service):
        """Top-level import version must be integer 1, not boolean true."""
        payload = json.dumps(
            {
                "version": True,
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

        with pytest.raises(ValueError, match="Unsupported rule import version"):
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
