"""Tests for RuleService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.arbitratarr.models.rule import Rule, RuleType
from app.arbitratarr.services.rule_service import DEFAULT_RULES, RuleService


class TestRuleService:
    """Test cases for RuleService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

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
        with patch.object(service, 'get_rules_by_type', return_value=[]) as mock_get:
            await service.get_exclusions()
            mock_get.assert_called_once_with(RuleType.EXCLUSION)

    @pytest.mark.asyncio
    async def test_get_requirements(self, mock_db, service):
        """Test getting requirement rules."""
        with patch.object(service, 'get_rules_by_type', return_value=[]) as mock_get:
            await service.get_requirements()
            mock_get.assert_called_once_with(RuleType.REQUIREMENT)

    @pytest.mark.asyncio
    async def test_get_scorers(self, mock_db, service):
        """Test getting scorer rules."""
        with patch.object(service, 'get_rules_by_type', return_value=[]) as mock_get:
            await service.get_scorers()
            mock_get.assert_called_once_with(RuleType.SCORER)

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

        result = await service.create_rule(
            name="Test Rule",
            rule_type=RuleType.EXCLUSION,
            pattern="CAM|TS",
            score=0,
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

        result = await service.update_rule(
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

        result = await service.update_rule(rule_id=1, score=50)

        assert mock_rule.name == "Original"
        assert mock_rule.pattern == "OriginalPattern"
        assert mock_rule.score == 50

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

        with patch.object(service, 'get_all_rules', return_value=[]) as mock_get:
            with patch.object(service, 'create_rule', return_value=MagicMock(spec=Rule)) as mock_create:
                mock_get.return_value = []
                result = await service.seed_default_rules()

                assert len(result) == len(DEFAULT_RULES)
                assert mock_create.call_count == len(DEFAULT_RULES)

    @pytest.mark.asyncio
    async def test_seed_default_rules_already_exists(self, mock_db, service):
        """Test seeding when rules already exist."""
        existing_rules = [MagicMock(spec=Rule)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = existing_rules
        mock_db.execute.return_value = mock_result

        with patch.object(service, 'get_all_rules', return_value=existing_rules) as mock_get:
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

        result = await service.toggle_rule(1)

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
        assert len(DEFAULT_RULES) == 5

        exclusion_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.EXCLUSION]
        assert len(exclusion_rules) == 1

        requirement_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.REQUIREMENT]
        assert len(requirement_rules) == 1

        scorer_rules = [r for r in DEFAULT_RULES if r["rule_type"] == RuleType.SCORER]
        assert len(scorer_rules) == 3

    def test_default_rules_have_required_fields(self):
        """Test that each default rule has all required fields."""
        required_fields = {"name", "rule_type", "pattern", "score", "priority", "description"}

        for rule in DEFAULT_RULES:
            assert required_fields.issubset(rule.keys())
            assert isinstance(rule["name"], str)
            assert isinstance(rule["pattern"], str)
            assert isinstance(rule["score"], int)
            assert isinstance(rule["priority"], int)