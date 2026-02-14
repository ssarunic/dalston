"""Unit tests for RetentionService."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from dalston.common.constants import (
    SYSTEM_POLICY_DEFAULT,
    SYSTEM_POLICY_KEEP,
    SYSTEM_POLICY_ZERO_RETENTION,
)
from dalston.common.models import RetentionMode, RetentionScope
from dalston.gateway.services.retention import (
    RetentionPolicyInUseError,
    RetentionPolicyNotFoundError,
    RetentionService,
)


def make_mock_db():
    """Create a properly configured mock async database session."""
    db = AsyncMock()
    return db


def mock_execute_result(scalar_value=None, scalars_value=None):
    """Create a mock execute result.

    Args:
        scalar_value: Value to return from scalar_one_or_none()
        scalars_value: List to return from scalars().all()
    """
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_value
    if scalars_value is not None:
        result.scalars.return_value.all.return_value = scalars_value
    return result


class TestRetentionServiceCreatePolicy:
    """Tests for RetentionService.create_policy method."""

    @pytest.fixture
    def retention_service(self) -> RetentionService:
        return RetentionService()

    @pytest.fixture
    def mock_db(self):
        return make_mock_db()

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_create_auto_delete_policy(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test creating an auto_delete policy with hours."""
        # get_policy_by_name does 2 queries, both return None (no duplicate)
        mock_db.execute.side_effect = [
            mock_execute_result(scalar_value=None),  # tenant query
            mock_execute_result(scalar_value=None),  # system query
        ]

        policy = await retention_service.create_policy(
            db=mock_db,
            tenant_id=tenant_id,
            name="short-term",
            mode=RetentionMode.AUTO_DELETE,
            hours=24,
            scope=RetentionScope.ALL,
        )

        assert policy.name == "short-term"
        assert policy.mode == "auto_delete"
        assert policy.hours == 24
        assert policy.scope == "all"
        assert policy.tenant_id == tenant_id
        assert policy.is_system is False
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_keep_policy(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test creating a keep policy without hours."""
        mock_db.execute.side_effect = [
            mock_execute_result(scalar_value=None),
            mock_execute_result(scalar_value=None),
        ]

        policy = await retention_service.create_policy(
            db=mock_db,
            tenant_id=tenant_id,
            name="forever",
            mode=RetentionMode.KEEP,
        )

        assert policy.name == "forever"
        assert policy.mode == "keep"
        assert policy.hours is None
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_none_policy(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test creating a none policy without hours."""
        mock_db.execute.side_effect = [
            mock_execute_result(scalar_value=None),
            mock_execute_result(scalar_value=None),
        ]

        policy = await retention_service.create_policy(
            db=mock_db,
            tenant_id=tenant_id,
            name="no-retention",
            mode=RetentionMode.NONE,
        )

        assert policy.name == "no-retention"
        assert policy.mode == "none"
        assert policy.hours is None

    @pytest.mark.asyncio
    async def test_create_auto_delete_without_hours_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test that auto_delete mode requires hours."""
        with pytest.raises(ValueError, match="hours is required when mode is"):
            await retention_service.create_policy(
                db=mock_db,
                tenant_id=tenant_id,
                name="invalid",
                mode=RetentionMode.AUTO_DELETE,
                hours=None,
            )

    @pytest.mark.asyncio
    async def test_create_keep_with_hours_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test that keep mode cannot have hours."""
        with pytest.raises(ValueError, match="hours must be null when mode is"):
            await retention_service.create_policy(
                db=mock_db,
                tenant_id=tenant_id,
                name="invalid",
                mode=RetentionMode.KEEP,
                hours=24,
            )

    @pytest.mark.asyncio
    async def test_create_none_with_hours_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test that none mode cannot have hours."""
        with pytest.raises(ValueError, match="hours must be null when mode is"):
            await retention_service.create_policy(
                db=mock_db,
                tenant_id=tenant_id,
                name="invalid",
                mode=RetentionMode.NONE,
                hours=1,
            )

    @pytest.mark.asyncio
    async def test_create_with_hours_less_than_one_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test that hours must be at least 1."""
        with pytest.raises(ValueError, match="hours must be at least 1"):
            await retention_service.create_policy(
                db=mock_db,
                tenant_id=tenant_id,
                name="invalid",
                mode=RetentionMode.AUTO_DELETE,
                hours=0,
            )

    @pytest.mark.asyncio
    async def test_create_duplicate_name_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test that duplicate policy names raise an error."""
        existing_policy = MagicMock()
        existing_policy.name = "duplicate"

        # First query returns existing tenant policy
        mock_db.execute.return_value = mock_execute_result(scalar_value=existing_policy)

        with pytest.raises(
            ValueError, match="Policy with name 'duplicate' already exists"
        ):
            await retention_service.create_policy(
                db=mock_db,
                tenant_id=tenant_id,
                name="duplicate",
                mode=RetentionMode.AUTO_DELETE,
                hours=24,
            )


class TestRetentionServiceListPolicies:
    """Tests for RetentionService.list_policies method."""

    @pytest.fixture
    def retention_service(self) -> RetentionService:
        return RetentionService()

    @pytest.fixture
    def mock_db(self):
        return make_mock_db()

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_list_returns_tenant_and_system_policies(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test that list returns both tenant and system policies."""
        tenant_policy = MagicMock()
        tenant_policy.name = "custom"
        tenant_policy.is_system = False

        system_policy = MagicMock()
        system_policy.name = "default"
        system_policy.is_system = True

        mock_db.execute.return_value = mock_execute_result(
            scalars_value=[system_policy, tenant_policy]
        )

        policies = await retention_service.list_policies(mock_db, tenant_id)

        assert len(policies) == 2
        assert policies[0].is_system is True
        assert policies[1].is_system is False


class TestRetentionServiceGetPolicy:
    """Tests for RetentionService.get_policy method."""

    @pytest.fixture
    def retention_service(self) -> RetentionService:
        return RetentionService()

    @pytest.fixture
    def mock_db(self):
        return make_mock_db()

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_get_policy_by_id(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test getting a policy by ID."""
        policy_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        mock_policy = MagicMock()
        mock_policy.id = policy_id
        mock_policy.name = "test"

        mock_db.execute.return_value = mock_execute_result(scalar_value=mock_policy)

        result = await retention_service.get_policy(mock_db, policy_id, tenant_id)

        assert result is mock_policy
        assert result.id == policy_id

    @pytest.mark.asyncio
    async def test_get_policy_not_found(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test getting a nonexistent policy returns None."""
        policy_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        mock_db.execute.return_value = mock_execute_result(scalar_value=None)

        result = await retention_service.get_policy(mock_db, policy_id, tenant_id)

        assert result is None


class TestRetentionServiceGetPolicyByName:
    """Tests for RetentionService.get_policy_by_name method."""

    @pytest.fixture
    def retention_service(self) -> RetentionService:
        return RetentionService()

    @pytest.fixture
    def mock_db(self):
        return make_mock_db()

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_get_tenant_policy_by_name(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test finding tenant policy by name."""
        tenant_policy = MagicMock()
        tenant_policy.name = "custom"
        tenant_policy.tenant_id = tenant_id

        # First query returns tenant policy
        mock_db.execute.return_value = mock_execute_result(scalar_value=tenant_policy)

        result = await retention_service.get_policy_by_name(
            mock_db, tenant_id, "custom"
        )

        assert result is tenant_policy

    @pytest.mark.asyncio
    async def test_fallback_to_system_policy(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test fallback to system policy when tenant policy not found."""
        system_policy = MagicMock()
        system_policy.name = "default"
        system_policy.tenant_id = None
        system_policy.is_system = True

        # First query returns None (no tenant policy), second returns system
        mock_db.execute.side_effect = [
            mock_execute_result(scalar_value=None),
            mock_execute_result(scalar_value=system_policy),
        ]

        result = await retention_service.get_policy_by_name(
            mock_db, tenant_id, "default"
        )

        assert result is system_policy
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_policy_name_not_found(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test policy not found by name returns None."""
        mock_db.execute.side_effect = [
            mock_execute_result(scalar_value=None),
            mock_execute_result(scalar_value=None),
        ]

        result = await retention_service.get_policy_by_name(
            mock_db, tenant_id, "nonexistent"
        )

        assert result is None


class TestRetentionServiceDeletePolicy:
    """Tests for RetentionService.delete_policy method."""

    @pytest.fixture
    def retention_service(self) -> RetentionService:
        return RetentionService()

    @pytest.fixture
    def mock_db(self):
        return make_mock_db()

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    def _make_policy(
        self,
        policy_id: UUID,
        tenant_id: UUID | None = None,
        is_system: bool = False,
    ):
        """Create a mock policy."""
        policy = MagicMock()
        policy.id = policy_id
        policy.tenant_id = tenant_id
        policy.is_system = is_system
        return policy

    @pytest.mark.asyncio
    async def test_delete_tenant_policy(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test deleting a tenant policy."""
        policy_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        policy = self._make_policy(policy_id, tenant_id)

        # get_policy makes 1 query
        mock_db.execute.return_value = mock_execute_result(scalar_value=policy)
        # Mock job count and session count
        mock_db.scalar.side_effect = [0, 0]

        await retention_service.delete_policy(mock_db, policy_id, tenant_id)

        mock_db.delete.assert_awaited_once_with(policy)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_policy_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test deleting nonexistent policy raises error."""
        policy_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        mock_db.execute.return_value = mock_execute_result(scalar_value=None)

        with pytest.raises(RetentionPolicyNotFoundError, match="not found"):
            await retention_service.delete_policy(mock_db, policy_id, tenant_id)

    @pytest.mark.asyncio
    async def test_delete_system_policy_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test deleting system policy raises error."""
        policy = self._make_policy(
            SYSTEM_POLICY_DEFAULT, tenant_id=None, is_system=True
        )
        mock_db.execute.return_value = mock_execute_result(scalar_value=policy)

        with pytest.raises(ValueError, match="Cannot delete system policies"):
            await retention_service.delete_policy(
                mock_db, SYSTEM_POLICY_DEFAULT, tenant_id
            )

    @pytest.mark.asyncio
    async def test_delete_policy_in_use_by_jobs_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test deleting policy in use by jobs raises error."""
        policy_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        policy = self._make_policy(policy_id, tenant_id)

        mock_db.execute.return_value = mock_execute_result(scalar_value=policy)
        mock_db.scalar.side_effect = [5, 0]  # 5 jobs using it

        with pytest.raises(RetentionPolicyInUseError, match="5 job\\(s\\)"):
            await retention_service.delete_policy(mock_db, policy_id, tenant_id)

    @pytest.mark.asyncio
    async def test_delete_policy_in_use_by_sessions_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test deleting policy in use by sessions raises error."""
        policy_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        policy = self._make_policy(policy_id, tenant_id)

        mock_db.execute.return_value = mock_execute_result(scalar_value=policy)
        mock_db.scalar.side_effect = [0, 3]  # 3 sessions using it

        with pytest.raises(RetentionPolicyInUseError, match="3 session\\(s\\)"):
            await retention_service.delete_policy(mock_db, policy_id, tenant_id)

    @pytest.mark.asyncio
    async def test_delete_other_tenant_policy_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test deleting another tenant's policy raises not found."""
        policy_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        other_tenant_id = UUID("99999999-9999-9999-9999-999999999999")
        policy = self._make_policy(policy_id, other_tenant_id)

        mock_db.execute.return_value = mock_execute_result(scalar_value=policy)

        with pytest.raises(RetentionPolicyNotFoundError, match="not found"):
            await retention_service.delete_policy(mock_db, policy_id, tenant_id)


class TestRetentionServiceResolvePolicy:
    """Tests for RetentionService.resolve_policy method."""

    @pytest.fixture
    def retention_service(self) -> RetentionService:
        return RetentionService()

    @pytest.fixture
    def mock_db(self):
        return make_mock_db()

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_resolve_by_name(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test resolving policy by name."""
        policy = MagicMock()
        policy.name = "short-term"

        mock_db.execute.return_value = mock_execute_result(scalar_value=policy)

        result = await retention_service.resolve_policy(
            mock_db, tenant_id, policy_name="short-term"
        )

        assert result is policy

    @pytest.mark.asyncio
    async def test_resolve_named_policy_not_found_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test resolving nonexistent named policy raises error."""
        mock_db.execute.side_effect = [
            mock_execute_result(scalar_value=None),
            mock_execute_result(scalar_value=None),
        ]

        with pytest.raises(
            RetentionPolicyNotFoundError, match="'nonexistent' not found"
        ):
            await retention_service.resolve_policy(
                mock_db, tenant_id, policy_name="nonexistent"
            )

    @pytest.mark.asyncio
    async def test_resolve_default_when_no_name(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test resolving to system default when no name provided."""
        default_policy = MagicMock()
        default_policy.id = SYSTEM_POLICY_DEFAULT
        default_policy.name = "default"
        default_policy.is_system = True

        mock_db.execute.return_value = mock_execute_result(scalar_value=default_policy)

        result = await retention_service.resolve_policy(mock_db, tenant_id)

        assert result is default_policy

    @pytest.mark.asyncio
    async def test_resolve_default_not_found_raises(
        self, retention_service: RetentionService, mock_db, tenant_id
    ):
        """Test error when system default policy not found."""
        mock_db.execute.return_value = mock_execute_result(scalar_value=None)

        with pytest.raises(RetentionPolicyNotFoundError, match="System default"):
            await retention_service.resolve_policy(mock_db, tenant_id)


class TestSystemPolicyConstants:
    """Test system policy UUID constants."""

    def test_system_policy_uuids(self):
        """Test that system policy UUIDs are well-formed."""
        assert SYSTEM_POLICY_DEFAULT == UUID("00000000-0000-0000-0000-000000000001")
        assert SYSTEM_POLICY_ZERO_RETENTION == UUID(
            "00000000-0000-0000-0000-000000000002"
        )
        assert SYSTEM_POLICY_KEEP == UUID("00000000-0000-0000-0000-000000000003")

    def test_system_policies_are_distinct(self):
        """Test that all system policies have unique UUIDs."""
        policies = [
            SYSTEM_POLICY_DEFAULT,
            SYSTEM_POLICY_ZERO_RETENTION,
            SYSTEM_POLICY_KEEP,
        ]
        assert len(set(policies)) == 3
