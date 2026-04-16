"""Tests for RuleService."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.rule import Rule, RuleType, TVTarget
from app.siftarr.services.rule_service import DEFAULT_RULES, RuleImportPreview, RuleService


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
        """Test seeding default rules."""
        mock_result_empty = MagicMock()
        mock_result_empty.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result_empty

        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch.object(service, "get_all_rules", return_value=[]):
            result = await service.seed_default_rules()

            assert len(result) == len(DEFAULT_RULES)

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

    def test_default_rules_content(self):
        """Test that DEFAULT_RULES has expected content."""
        assert len(DEFAULT_RULES) == 12

        exclusion_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.EXCLUSION]
        assert len(exclusion_rules) == 1

        requirement_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.REQUIREMENT]
        assert len(requirement_rules) == 1

        scorer_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.SCORER]
        assert len(scorer_rules) == 7

        size_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.SIZE_LIMIT]
        assert len(size_rules) == 3

    def test_default_rules_have_required_fields(self):
        """Test that each default rule has all required fields."""
        required_fields = {
            "name",
            "rule_type",
            "pattern",
            "score",
            "priority",
            "description",
            "media_scope",
            "is_enabled",
            "min_size_gb",
            "max_size_gb",
            "tv_target",
        }

        for rule in DEFAULT_RULES:
            assert required_fields.issubset(rule.keys())
            assert isinstance(rule["name"], str)
            assert isinstance(rule["pattern"], str)
            assert isinstance(rule["score"], int)
            assert isinstance(rule["priority"], int)

    def test_default_rules_match_live_snapshot_expectations(self):
        """Bundled defaults should match the approved 12-rule live snapshot."""
        assert [rule["name"] for rule in DEFAULT_RULES] == [
            "Reject Camera/TS/Screener",
            "Require HD Resolution",
            "Prefer x265/HEVC",
            "Prefer MeGusta",
            "Prefer LAMA/SPiCYLAMA",
            "Movies Size Limit",
            "Tv Episode Size",
            "TV Seasons Size",
            "1080p TV",
            "720p TV",
            "1080p Movie",
            "4k Movie",
        ]
        assert DEFAULT_RULES[6]["tv_target"] == TVTarget.EPISODE
        assert DEFAULT_RULES[7]["tv_target"] == TVTarget.SEASON_PACK

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
