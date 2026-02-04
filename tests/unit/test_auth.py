"""Unit tests for API key authentication."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from dalston.gateway.services.auth import (
    DEFAULT_EXPIRES_AT,
    KEY_PREFIX,
    TOKEN_PREFIX,
    APIKey,
    AuthService,
    Scope,
    SessionToken,
    generate_api_key,
    generate_session_token,
    get_key_prefix,
    hash_api_key,
)


class TestAPIKeyGeneration:
    """Tests for API key generation functions."""

    def test_generate_api_key_has_prefix(self):
        key = generate_api_key()
        assert key.startswith(KEY_PREFIX)

    def test_generate_api_key_length(self):
        key = generate_api_key()
        # dk_ (3 chars) + 43 urlsafe base64 chars = 46 total
        assert len(key) >= 46

    def test_generate_api_key_unique(self):
        keys = [generate_api_key() for _ in range(100)]
        assert len(set(keys)) == 100

    def test_hash_api_key_deterministic(self):
        key = "dk_test_key_12345"
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 == hash2

    def test_hash_api_key_different_for_different_keys(self):
        hash1 = hash_api_key("dk_key1")
        hash2 = hash_api_key("dk_key2")
        assert hash1 != hash2

    def test_hash_api_key_is_hex(self):
        hash_val = hash_api_key("dk_test")
        # SHA256 produces 64 hex characters
        assert len(hash_val) == 64
        assert all(c in "0123456789abcdef" for c in hash_val)

    def test_get_key_prefix(self):
        key = "dk_abc123def456"
        prefix = get_key_prefix(key)
        assert prefix == "dk_abc123d"
        assert len(prefix) == 10


class TestScope:
    """Tests for Scope enum."""

    def test_jobs_read_scope(self):
        assert Scope.JOBS_READ.value == "jobs:read"

    def test_jobs_write_scope(self):
        assert Scope.JOBS_WRITE.value == "jobs:write"

    def test_realtime_scope(self):
        assert Scope.REALTIME.value == "realtime"

    def test_webhooks_scope(self):
        assert Scope.WEBHOOKS.value == "webhooks"

    def test_admin_scope(self):
        assert Scope.ADMIN.value == "admin"


class TestAPIKeyModel:
    """Tests for APIKey dataclass."""

    @pytest.fixture
    def sample_api_key(self) -> APIKey:
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=100,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    def test_is_revoked_false(self, sample_api_key: APIKey):
        assert sample_api_key.is_revoked is False

    def test_is_revoked_true(self, sample_api_key: APIKey):
        sample_api_key.revoked_at = datetime.now(timezone.utc)
        assert sample_api_key.is_revoked is True

    def test_is_expired_false(self, sample_api_key: APIKey):
        # Default expires_at is distant future
        assert sample_api_key.is_expired is False

    def test_is_expired_true(self, sample_api_key: APIKey):
        from datetime import timedelta
        sample_api_key.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert sample_api_key.is_expired is True

    def test_is_expired_future(self, sample_api_key: APIKey):
        from datetime import timedelta
        sample_api_key.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        assert sample_api_key.is_expired is False

    def test_has_scope_direct(self, sample_api_key: APIKey):
        assert sample_api_key.has_scope(Scope.JOBS_READ) is True
        assert sample_api_key.has_scope(Scope.JOBS_WRITE) is True
        assert sample_api_key.has_scope(Scope.REALTIME) is False

    def test_has_scope_admin_grants_all(self, sample_api_key: APIKey):
        sample_api_key.scopes = [Scope.ADMIN]
        assert sample_api_key.has_scope(Scope.JOBS_READ) is True
        assert sample_api_key.has_scope(Scope.JOBS_WRITE) is True
        assert sample_api_key.has_scope(Scope.REALTIME) is True
        assert sample_api_key.has_scope(Scope.WEBHOOKS) is True

    def test_to_dict(self, sample_api_key: APIKey):
        data = sample_api_key.to_dict()
        assert data["id"] == str(sample_api_key.id)
        assert data["name"] == "Test Key"
        assert data["scopes"] == ["jobs:read", "jobs:write"]
        assert data["rate_limit"] == 100
        assert data["expires_at"] == DEFAULT_EXPIRES_AT.isoformat()

    def test_from_dict(self, sample_api_key: APIKey):
        from datetime import timedelta
        custom_expires = datetime.now(timezone.utc) + timedelta(days=90)
        data = {
            "id": str(sample_api_key.id),
            "key_hash": "abc123",
            "prefix": "dk_abc1234",
            "name": "Test Key",
            "tenant_id": str(sample_api_key.tenant_id),
            "scopes": "jobs:read,jobs:write",
            "rate_limit": "100",
            "created_at": sample_api_key.created_at.isoformat(),
            "last_used_at": "",
            "expires_at": custom_expires.isoformat(),
            "revoked_at": "",
        }
        api_key = APIKey.from_dict(data)
        assert api_key.name == "Test Key"
        assert api_key.scopes == [Scope.JOBS_READ, Scope.JOBS_WRITE]
        assert api_key.rate_limit == 100
        assert api_key.last_used_at is None
        assert api_key.revoked_at is None
        assert api_key.expires_at == custom_expires

    def test_from_dict_missing_expires_at_uses_default(self, sample_api_key: APIKey):
        """Test backward compatibility - old keys without expires_at get default."""
        data = {
            "id": str(sample_api_key.id),
            "key_hash": "abc123",
            "prefix": "dk_abc1234",
            "name": "Legacy Key",
            "tenant_id": str(sample_api_key.tenant_id),
            "scopes": "jobs:read",
            "rate_limit": "",
            "created_at": sample_api_key.created_at.isoformat(),
            "last_used_at": "",
            "revoked_at": "",
            # No expires_at field
        }
        api_key = APIKey.from_dict(data)
        assert api_key.expires_at == DEFAULT_EXPIRES_AT


class TestAuthService:
    """Tests for AuthService."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        redis.hset = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.set = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.sadd = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())
        redis.incr = AsyncMock(return_value=1)
        redis.expire = AsyncMock()
        redis.scan = AsyncMock(return_value=(0, []))
        return redis

    @pytest.fixture
    def auth_service(self, mock_redis) -> AuthService:
        return AuthService(mock_redis)

    @pytest.mark.asyncio
    async def test_create_api_key(self, auth_service: AuthService, mock_redis):
        raw_key, api_key = await auth_service.create_api_key(
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        )

        assert raw_key.startswith(KEY_PREFIX)
        assert api_key.name == "Test Key"
        assert Scope.JOBS_READ in api_key.scopes
        assert Scope.JOBS_WRITE in api_key.scopes
        assert Scope.REALTIME in api_key.scopes
        assert mock_redis.hset.called

    @pytest.mark.asyncio
    async def test_create_api_key_with_custom_scopes(
        self, auth_service: AuthService, mock_redis
    ):
        raw_key, api_key = await auth_service.create_api_key(
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN],
        )

        assert api_key.scopes == [Scope.ADMIN]

    @pytest.mark.asyncio
    async def test_create_api_key_with_rate_limit(
        self, auth_service: AuthService, mock_redis
    ):
        raw_key, api_key = await auth_service.create_api_key(
            name="Limited Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            rate_limit=60,
        )

        assert api_key.rate_limit == 60

    @pytest.mark.asyncio
    async def test_create_api_key_default_expires_at(
        self, auth_service: AuthService, mock_redis
    ):
        """Test that keys created without expires_at use distant future default."""
        raw_key, api_key = await auth_service.create_api_key(
            name="Default Expiry Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        )

        assert api_key.expires_at == DEFAULT_EXPIRES_AT

    @pytest.mark.asyncio
    async def test_create_api_key_with_custom_expires_at(
        self, auth_service: AuthService, mock_redis
    ):
        """Test creating key with custom expiration date."""
        from datetime import timedelta
        custom_expires = datetime.now(timezone.utc) + timedelta(days=30)

        raw_key, api_key = await auth_service.create_api_key(
            name="Expiring Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            expires_at=custom_expires,
        )

        assert api_key.expires_at == custom_expires

    @pytest.mark.asyncio
    async def test_validate_api_key_valid(
        self, auth_service: AuthService, mock_redis
    ):
        # Setup mock to return valid key data
        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        mock_redis.hgetall.return_value = {
            "id": str(uuid4()),
            "key_hash": "abc123",
            "prefix": "dk_abc1234",
            "name": "Test Key",
            "tenant_id": str(tenant_id),
            "scopes": "jobs:read,jobs:write",
            "rate_limit": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": "",
            "revoked_at": "",
        }

        api_key = await auth_service.validate_api_key("dk_valid_key")

        assert api_key is not None
        assert api_key.name == "Test Key"

    @pytest.mark.asyncio
    async def test_validate_api_key_invalid(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.hgetall.return_value = {}

        api_key = await auth_service.validate_api_key("dk_invalid_key")

        assert api_key is None

    @pytest.mark.asyncio
    async def test_validate_api_key_revoked(
        self, auth_service: AuthService, mock_redis
    ):
        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        mock_redis.hgetall.return_value = {
            "id": str(uuid4()),
            "key_hash": "abc123",
            "prefix": "dk_abc1234",
            "name": "Revoked Key",
            "tenant_id": str(tenant_id),
            "scopes": "jobs:read",
            "rate_limit": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": "",
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }

        api_key = await auth_service.validate_api_key("dk_revoked_key")

        assert api_key is None

    @pytest.mark.asyncio
    async def test_validate_api_key_wrong_prefix(
        self, auth_service: AuthService, mock_redis
    ):
        api_key = await auth_service.validate_api_key("invalid_key_format")
        assert api_key is None

    @pytest.mark.asyncio
    async def test_validate_api_key_expired(
        self, auth_service: AuthService, mock_redis
    ):
        """Test that expired keys are rejected during validation."""
        from datetime import timedelta
        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        expired_time = datetime.now(timezone.utc) - timedelta(minutes=1)

        mock_redis.hgetall.return_value = {
            "id": str(uuid4()),
            "key_hash": "abc123",
            "prefix": "dk_abc1234",
            "name": "Expired Key",
            "tenant_id": str(tenant_id),
            "scopes": "jobs:read",
            "rate_limit": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": "",
            "expires_at": expired_time.isoformat(),
            "revoked_at": "",
        }

        api_key = await auth_service.validate_api_key("dk_expired_key")

        assert api_key is None

    @pytest.mark.asyncio
    async def test_check_rate_limit_unlimited(
        self, auth_service: AuthService, mock_redis
    ):
        api_key = APIKey(
            id=uuid4(),
            key_hash="abc123",
            prefix="dk_abc1234",
            name="Unlimited Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        allowed, remaining = await auth_service.check_rate_limit(api_key)

        assert allowed is True
        assert remaining == -1

    @pytest.mark.asyncio
    async def test_check_rate_limit_under_limit(
        self, auth_service: AuthService, mock_redis
    ):
        api_key = APIKey(
            id=uuid4(),
            key_hash="abc123",
            prefix="dk_abc1234",
            name="Limited Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=100,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_redis.incr.return_value = 50

        allowed, remaining = await auth_service.check_rate_limit(api_key)

        assert allowed is True
        assert remaining == 50

    @pytest.mark.asyncio
    async def test_check_rate_limit_exceeded(
        self, auth_service: AuthService, mock_redis
    ):
        api_key = APIKey(
            id=uuid4(),
            key_hash="abc123",
            prefix="dk_abc1234",
            name="Limited Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=100,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_redis.incr.return_value = 101

        allowed, remaining = await auth_service.check_rate_limit(api_key)

        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_revoke_api_key(self, auth_service: AuthService, mock_redis):
        key_id = uuid4()
        key_hash = "abc123"

        # Setup mocks
        mock_redis.get.return_value = key_hash
        mock_redis.hgetall.return_value = {
            "id": str(key_id),
            "key_hash": key_hash,
            "prefix": "dk_abc1234",
            "name": "Test Key",
            "tenant_id": str(UUID("00000000-0000-0000-0000-000000000000")),
            "scopes": "jobs:read",
            "rate_limit": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": "",
            "revoked_at": "",
        }

        success = await auth_service.revoke_api_key(key_id)

        assert success is True
        assert mock_redis.hset.called

    @pytest.mark.asyncio
    async def test_revoke_api_key_not_found(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.get.return_value = None

        success = await auth_service.revoke_api_key(uuid4())

        assert success is False

    @pytest.mark.asyncio
    async def test_has_any_api_keys_empty(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.scan.return_value = (0, [])

        has_keys = await auth_service.has_any_api_keys()

        assert has_keys is False

    @pytest.mark.asyncio
    async def test_has_any_api_keys_exists(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.scan.return_value = (0, ["dalston:apikey:hash:abc123"])

        has_keys = await auth_service.has_any_api_keys()

        assert has_keys is True

    @pytest.mark.asyncio
    async def test_list_api_keys_excludes_revoked_by_default(
        self, auth_service: AuthService, mock_redis
    ):
        """Test that list_api_keys excludes revoked keys by default."""
        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        key1_id = uuid4()
        key2_id = uuid4()  # This one will be revoked

        mock_redis.smembers.return_value = {str(key1_id), str(key2_id)}

        # Setup get_api_key_by_id to return different keys
        async def mock_get_by_id(id_key):
            return "hash123"

        mock_redis.get.side_effect = mock_get_by_id

        # Track which key_id is being requested
        call_count = [0]

        async def mock_hgetall(hash_key):
            call_count[0] += 1
            if call_count[0] == 1:
                # First key - active
                return {
                    "id": str(key1_id),
                    "key_hash": "hash1",
                    "prefix": "dk_active",
                    "name": "Active Key",
                    "tenant_id": str(tenant_id),
                    "scopes": "jobs:read",
                    "rate_limit": "",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_used_at": "",
                    "expires_at": DEFAULT_EXPIRES_AT.isoformat(),
                    "revoked_at": "",
                }
            else:
                # Second key - revoked
                return {
                    "id": str(key2_id),
                    "key_hash": "hash2",
                    "prefix": "dk_revoked",
                    "name": "Revoked Key",
                    "tenant_id": str(tenant_id),
                    "scopes": "jobs:read",
                    "rate_limit": "",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_used_at": "",
                    "expires_at": DEFAULT_EXPIRES_AT.isoformat(),
                    "revoked_at": datetime.now(timezone.utc).isoformat(),
                }

        mock_redis.hgetall.side_effect = mock_hgetall

        keys = await auth_service.list_api_keys(tenant_id)

        # Should only return the active key
        assert len(keys) == 1
        assert keys[0].name == "Active Key"

    @pytest.mark.asyncio
    async def test_list_api_keys_includes_revoked_when_requested(
        self, auth_service: AuthService, mock_redis
    ):
        """Test that list_api_keys includes revoked keys when include_revoked=True."""
        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        key1_id = uuid4()
        key2_id = uuid4()  # This one will be revoked

        mock_redis.smembers.return_value = {str(key1_id), str(key2_id)}

        async def mock_get_by_id(id_key):
            return "hash123"

        mock_redis.get.side_effect = mock_get_by_id

        call_count = [0]

        async def mock_hgetall(hash_key):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "id": str(key1_id),
                    "key_hash": "hash1",
                    "prefix": "dk_active",
                    "name": "Active Key",
                    "tenant_id": str(tenant_id),
                    "scopes": "jobs:read",
                    "rate_limit": "",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_used_at": "",
                    "expires_at": DEFAULT_EXPIRES_AT.isoformat(),
                    "revoked_at": "",
                }
            else:
                return {
                    "id": str(key2_id),
                    "key_hash": "hash2",
                    "prefix": "dk_revoked",
                    "name": "Revoked Key",
                    "tenant_id": str(tenant_id),
                    "scopes": "jobs:read",
                    "rate_limit": "",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_used_at": "",
                    "expires_at": DEFAULT_EXPIRES_AT.isoformat(),
                    "revoked_at": datetime.now(timezone.utc).isoformat(),
                }

        mock_redis.hgetall.side_effect = mock_hgetall

        keys = await auth_service.list_api_keys(tenant_id, include_revoked=True)

        # Should return both keys
        assert len(keys) == 2
        key_names = {k.name for k in keys}
        assert "Active Key" in key_names
        assert "Revoked Key" in key_names


class TestMiddlewareHelpers:
    """Tests for authentication middleware helper functions."""

    def test_extract_api_key_from_bearer_header(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_request

        request = MagicMock()
        request.headers = {"authorization": "Bearer dk_test_key_123"}
        request.query_params = {}

        key = extract_api_key_from_request(request)

        assert key == "dk_test_key_123"

    def test_extract_api_key_from_xi_api_key_header(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_request

        request = MagicMock()
        request.headers = {"xi-api-key": "dk_elevenlabs_key"}
        request.query_params = {}

        key = extract_api_key_from_request(request)

        assert key == "dk_elevenlabs_key"

    def test_extract_api_key_from_query_param(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_request

        request = MagicMock()
        request.headers = {}
        request.query_params = {"api_key": "dk_query_key"}

        key = extract_api_key_from_request(request)

        assert key == "dk_query_key"

    def test_extract_api_key_bearer_takes_priority(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_request

        request = MagicMock()
        request.headers = {
            "authorization": "Bearer dk_bearer_key",
            "xi-api-key": "dk_xi_key",
        }
        request.query_params = {"api_key": "dk_query_key"}

        key = extract_api_key_from_request(request)

        assert key == "dk_bearer_key"

    def test_extract_api_key_xi_header_fallback(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_request

        request = MagicMock()
        request.headers = {"xi-api-key": "dk_xi_key"}
        request.query_params = {"api_key": "dk_query_key"}

        key = extract_api_key_from_request(request)

        assert key == "dk_xi_key"

    def test_extract_api_key_none_when_missing(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_request

        request = MagicMock()
        request.headers = {}
        request.query_params = {}

        key = extract_api_key_from_request(request)

        assert key is None

    def test_extract_api_key_from_websocket(self):
        from dalston.gateway.middleware.auth import extract_api_key_from_websocket

        websocket = MagicMock()
        websocket.query_params = {"api_key": "dk_ws_key"}
        websocket.headers = {}

        key = extract_api_key_from_websocket(websocket)

        assert key == "dk_ws_key"

    def test_require_scope_raises_on_missing(self):
        from dalston.gateway.middleware.auth import (
            AuthorizationError,
            require_scope,
        )

        api_key = APIKey(
            id=uuid4(),
            key_hash="abc123",
            prefix="dk_abc1234",
            name="Limited Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        with pytest.raises(AuthorizationError) as exc_info:
            require_scope(api_key, Scope.JOBS_WRITE)

        assert exc_info.value.status_code == 403
        assert "jobs:write" in exc_info.value.detail

    def test_require_scope_passes_with_scope(self):
        from dalston.gateway.middleware.auth import require_scope

        api_key = APIKey(
            id=uuid4(),
            key_hash="abc123",
            prefix="dk_abc1234",
            name="Write Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        # Should not raise
        require_scope(api_key, Scope.JOBS_WRITE)


class TestSessionTokenGeneration:
    """Tests for session token generation functions."""

    def test_generate_session_token_has_prefix(self):
        token = generate_session_token()
        assert token.startswith(TOKEN_PREFIX)

    def test_generate_session_token_length(self):
        token = generate_session_token()
        # tk_ (3 chars) + 43 urlsafe base64 chars = 46 total
        assert len(token) >= 46

    def test_generate_session_token_unique(self):
        tokens = [generate_session_token() for _ in range(100)]
        assert len(set(tokens)) == 100


class TestSessionTokenModel:
    """Tests for SessionToken dataclass."""

    @pytest.fixture
    def sample_session_token(self) -> SessionToken:
        from datetime import timedelta

        return SessionToken(
            token_hash="def456abc789",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            parent_key_id=UUID("12345678-1234-1234-1234-123456789abc"),
            scopes=[Scope.REALTIME],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            created_at=datetime.now(timezone.utc),
        )

    def test_is_expired_false(self, sample_session_token: SessionToken):
        assert sample_session_token.is_expired is False

    def test_is_expired_true(self, sample_session_token: SessionToken):
        from datetime import timedelta

        sample_session_token.expires_at = datetime.now(timezone.utc) - timedelta(
            minutes=1
        )
        assert sample_session_token.is_expired is True

    def test_has_scope_direct(self, sample_session_token: SessionToken):
        assert sample_session_token.has_scope(Scope.REALTIME) is True
        assert sample_session_token.has_scope(Scope.JOBS_READ) is False

    def test_has_scope_no_admin_escalation(self, sample_session_token: SessionToken):
        # Session tokens don't get admin privilege escalation
        sample_session_token.scopes = [Scope.REALTIME]
        assert sample_session_token.has_scope(Scope.ADMIN) is False
        assert sample_session_token.has_scope(Scope.JOBS_READ) is False

    def test_to_dict(self, sample_session_token: SessionToken):
        data = sample_session_token.to_dict()
        assert data["token_hash"] == "def456abc789"
        assert data["scopes"] == "realtime"
        assert data["tenant_id"] == str(sample_session_token.tenant_id)

    def test_from_dict(self, sample_session_token: SessionToken):
        data = {
            "token_hash": "def456abc789",
            "tenant_id": str(sample_session_token.tenant_id),
            "parent_key_id": str(sample_session_token.parent_key_id),
            "scopes": "realtime",
            "expires_at": sample_session_token.expires_at.isoformat(),
            "created_at": sample_session_token.created_at.isoformat(),
        }
        token = SessionToken.from_dict(data)
        assert token.token_hash == "def456abc789"
        assert token.scopes == [Scope.REALTIME]


class TestSessionTokenService:
    """Tests for session token methods in AuthService."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        redis.hset = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.set = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.sadd = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())
        redis.incr = AsyncMock(return_value=1)
        redis.expire = AsyncMock()
        redis.scan = AsyncMock(return_value=(0, []))
        redis.delete = AsyncMock(return_value=1)
        return redis

    @pytest.fixture
    def auth_service(self, mock_redis) -> AuthService:
        return AuthService(mock_redis)

    @pytest.fixture
    def sample_api_key(self) -> APIKey:
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.REALTIME],
            rate_limit=100,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.mark.asyncio
    async def test_create_session_token(
        self, auth_service: AuthService, mock_redis, sample_api_key: APIKey
    ):
        raw_token, session_token = await auth_service.create_session_token(
            api_key=sample_api_key,
        )

        assert raw_token.startswith(TOKEN_PREFIX)
        assert session_token.tenant_id == sample_api_key.tenant_id
        assert session_token.parent_key_id == sample_api_key.id
        assert Scope.REALTIME in session_token.scopes
        assert mock_redis.hset.called
        assert mock_redis.expire.called

    @pytest.mark.asyncio
    async def test_create_session_token_with_custom_ttl(
        self, auth_service: AuthService, mock_redis, sample_api_key: APIKey
    ):
        raw_token, session_token = await auth_service.create_session_token(
            api_key=sample_api_key,
            ttl=300,
        )

        # Verify expire was called with custom TTL
        mock_redis.expire.assert_called()
        call_args = mock_redis.expire.call_args
        assert call_args[0][1] == 300

    @pytest.mark.asyncio
    async def test_create_session_token_scope_validation(
        self, auth_service: AuthService, mock_redis, sample_api_key: APIKey
    ):
        # Try to create token with scope parent doesn't have
        with pytest.raises(ValueError) as exc_info:
            await auth_service.create_session_token(
                api_key=sample_api_key,
                scopes=[Scope.ADMIN],
            )

        assert "Cannot grant scope" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_validate_session_token_valid(
        self, auth_service: AuthService, mock_redis
    ):
        from datetime import timedelta

        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        parent_key_id = UUID("12345678-1234-1234-1234-123456789abc")
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        mock_redis.hgetall.return_value = {
            "token_hash": "abc123",
            "tenant_id": str(tenant_id),
            "parent_key_id": str(parent_key_id),
            "scopes": "realtime",
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        session_token = await auth_service.validate_session_token("tk_valid_token")

        assert session_token is not None
        assert session_token.tenant_id == tenant_id
        assert Scope.REALTIME in session_token.scopes

    @pytest.mark.asyncio
    async def test_validate_session_token_invalid(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.hgetall.return_value = {}

        session_token = await auth_service.validate_session_token("tk_invalid_token")

        assert session_token is None

    @pytest.mark.asyncio
    async def test_validate_session_token_expired(
        self, auth_service: AuthService, mock_redis
    ):
        from datetime import timedelta

        tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        parent_key_id = UUID("12345678-1234-1234-1234-123456789abc")
        expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        mock_redis.hgetall.return_value = {
            "token_hash": "abc123",
            "tenant_id": str(tenant_id),
            "parent_key_id": str(parent_key_id),
            "scopes": "realtime",
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        session_token = await auth_service.validate_session_token("tk_expired_token")

        assert session_token is None

    @pytest.mark.asyncio
    async def test_validate_session_token_wrong_prefix(
        self, auth_service: AuthService, mock_redis
    ):
        # Should return None for dk_ prefix (API key, not session token)
        session_token = await auth_service.validate_session_token("dk_api_key")
        assert session_token is None

    @pytest.mark.asyncio
    async def test_revoke_session_token(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.delete.return_value = 1

        success = await auth_service.revoke_session_token("tk_valid_token")

        assert success is True
        assert mock_redis.delete.called

    @pytest.mark.asyncio
    async def test_revoke_session_token_not_found(
        self, auth_service: AuthService, mock_redis
    ):
        mock_redis.delete.return_value = 0

        success = await auth_service.revoke_session_token("tk_invalid_token")

        assert success is False


class TestMiddlewareSessionTokenSupport:
    """Tests for session token support in middleware."""

    def test_require_scope_works_with_session_token(self):
        from datetime import timedelta

        from dalston.gateway.middleware.auth import require_scope

        session_token = SessionToken(
            token_hash="def456abc789",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            parent_key_id=UUID("12345678-1234-1234-1234-123456789abc"),
            scopes=[Scope.REALTIME],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            created_at=datetime.now(timezone.utc),
        )

        # Should not raise
        require_scope(session_token, Scope.REALTIME)

    def test_require_scope_raises_for_session_token_missing_scope(self):
        from datetime import timedelta

        from dalston.gateway.middleware.auth import (
            AuthorizationError,
            require_scope,
        )

        session_token = SessionToken(
            token_hash="def456abc789",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            parent_key_id=UUID("12345678-1234-1234-1234-123456789abc"),
            scopes=[Scope.REALTIME],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            created_at=datetime.now(timezone.utc),
        )

        with pytest.raises(AuthorizationError) as exc_info:
            require_scope(session_token, Scope.JOBS_READ)

        assert exc_info.value.status_code == 403
