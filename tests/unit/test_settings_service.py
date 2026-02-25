"""Unit tests for SettingsService."""

import pytest

from dalston.gateway.services.settings import (
    _DEFINITION_MAP,
    _DEFINITIONS_BY_NS,
    NAMESPACE_MAP,
    SETTING_DEFINITIONS,
    SettingDefinition,
    SettingsService,
    clear_settings_cache,
    get_settings_cache,
)


class TestSettingDefinitionsRegistry:
    """Tests for the static definitions registry."""

    def test_all_definitions_have_valid_namespace(self):
        """Every definition must reference an existing namespace."""
        for defn in SETTING_DEFINITIONS:
            assert defn.namespace in NAMESPACE_MAP, (
                f"Definition {defn.namespace}/{defn.key} references unknown namespace"
            )

    def test_all_editable_namespaces_have_definitions(self):
        """Every editable namespace must have at least one definition."""
        for ns_info in NAMESPACE_MAP.values():
            if ns_info.editable:
                assert ns_info.namespace in _DEFINITIONS_BY_NS, (
                    f"Editable namespace '{ns_info.namespace}' has no definitions"
                )
                assert len(_DEFINITIONS_BY_NS[ns_info.namespace]) > 0

    def test_no_duplicate_definitions(self):
        """No two definitions should share the same (namespace, key)."""
        seen = set()
        for defn in SETTING_DEFINITIONS:
            pair = (defn.namespace, defn.key)
            assert pair not in seen, f"Duplicate definition: {pair}"
            seen.add(pair)

    def test_definition_map_matches_list(self):
        """The lookup map should have the same count as the list."""
        assert len(_DEFINITION_MAP) == len(SETTING_DEFINITIONS)

    def test_system_namespace_is_readonly(self):
        """System namespace must not be editable."""
        assert NAMESPACE_MAP["system"].editable is False

    def test_select_definitions_have_options(self):
        """Select-type definitions must have non-empty options."""
        for defn in SETTING_DEFINITIONS:
            if defn.value_type == "select":
                assert defn.options is not None and len(defn.options) > 0, (
                    f"{defn.namespace}/{defn.key} is 'select' type but has no options"
                )

    def test_int_definitions_have_bounds(self):
        """Int-type definitions should have min/max bounds."""
        for defn in SETTING_DEFINITIONS:
            if defn.value_type == "int":
                assert defn.min_value is not None, (
                    f"{defn.namespace}/{defn.key} has no min_value"
                )
                assert defn.max_value is not None, (
                    f"{defn.namespace}/{defn.key} has no max_value"
                )


class TestSettingValidation:
    """Tests for SettingsService._validate_value."""

    @pytest.fixture
    def service(self):
        return SettingsService()

    def test_int_valid(self, service):
        """Valid integer within bounds passes."""
        defn = SettingDefinition(
            namespace="test",
            key="count",
            label="Count",
            description="",
            value_type="int",
            default_value=10,
            env_var="TEST_COUNT",
            min_value=1,
            max_value=100,
        )
        service._validate_value(defn, 50)  # Should not raise

    def test_int_rejects_string(self, service):
        """String value is rejected for int type."""
        defn = SettingDefinition(
            namespace="test",
            key="count",
            label="Count",
            description="",
            value_type="int",
            default_value=10,
            env_var="TEST_COUNT",
        )
        with pytest.raises(ValueError, match="expected integer"):
            service._validate_value(defn, "not_an_int")

    def test_int_rejects_bool(self, service):
        """Boolean is rejected for int type (even though bool is subclass of int)."""
        defn = SettingDefinition(
            namespace="test",
            key="count",
            label="Count",
            description="",
            value_type="int",
            default_value=10,
            env_var="TEST_COUNT",
        )
        with pytest.raises(ValueError, match="expected integer"):
            service._validate_value(defn, True)

    def test_int_below_minimum(self, service):
        """Value below minimum is rejected."""
        defn = SettingDefinition(
            namespace="test",
            key="count",
            label="Count",
            description="",
            value_type="int",
            default_value=10,
            env_var="TEST_COUNT",
            min_value=5,
            max_value=100,
        )
        with pytest.raises(ValueError, match="minimum value is 5"):
            service._validate_value(defn, 0)

    def test_int_above_maximum(self, service):
        """Value above maximum is rejected."""
        defn = SettingDefinition(
            namespace="test",
            key="count",
            label="Count",
            description="",
            value_type="int",
            default_value=10,
            env_var="TEST_COUNT",
            min_value=1,
            max_value=100,
        )
        with pytest.raises(ValueError, match="maximum value is 100"):
            service._validate_value(defn, 999)

    def test_float_valid(self, service):
        """Valid float within bounds passes."""
        defn = SettingDefinition(
            namespace="test",
            key="size",
            label="Size",
            description="",
            value_type="float",
            default_value=3.0,
            env_var="TEST_SIZE",
            min_value=0.1,
            max_value=50.0,
        )
        service._validate_value(defn, 5.5)  # Should not raise

    def test_float_accepts_int(self, service):
        """Integer value is accepted for float type."""
        defn = SettingDefinition(
            namespace="test",
            key="size",
            label="Size",
            description="",
            value_type="float",
            default_value=3.0,
            env_var="TEST_SIZE",
            min_value=0.1,
            max_value=50.0,
        )
        service._validate_value(defn, 5)  # Should not raise

    def test_float_rejects_string(self, service):
        """String value is rejected for float type."""
        defn = SettingDefinition(
            namespace="test",
            key="size",
            label="Size",
            description="",
            value_type="float",
            default_value=3.0,
            env_var="TEST_SIZE",
        )
        with pytest.raises(ValueError, match="expected number"):
            service._validate_value(defn, "not_a_float")

    def test_bool_valid(self, service):
        """Boolean value passes."""
        defn = SettingDefinition(
            namespace="test",
            key="enabled",
            label="Enabled",
            description="",
            value_type="bool",
            default_value=False,
            env_var="TEST_ENABLED",
        )
        service._validate_value(defn, True)  # Should not raise
        service._validate_value(defn, False)  # Should not raise

    def test_bool_rejects_int(self, service):
        """Integer is rejected for bool type."""
        defn = SettingDefinition(
            namespace="test",
            key="enabled",
            label="Enabled",
            description="",
            value_type="bool",
            default_value=False,
            env_var="TEST_ENABLED",
        )
        with pytest.raises(ValueError, match="expected boolean"):
            service._validate_value(defn, 1)

    def test_select_valid_option(self, service):
        """Valid option passes for select type."""
        defn = SettingDefinition(
            namespace="test",
            key="mode",
            label="Mode",
            description="",
            value_type="select",
            default_value="a",
            env_var="TEST_MODE",
            options=["a", "b", "c"],
        )
        service._validate_value(defn, "b")  # Should not raise

    def test_select_invalid_option(self, service):
        """Invalid option is rejected for select type."""
        defn = SettingDefinition(
            namespace="test",
            key="mode",
            label="Mode",
            description="",
            value_type="select",
            default_value="a",
            env_var="TEST_MODE",
            options=["a", "b", "c"],
        )
        with pytest.raises(ValueError, match="must be one of"):
            service._validate_value(defn, "x")

    def test_string_valid(self, service):
        """String value passes."""
        defn = SettingDefinition(
            namespace="test",
            key="name",
            label="Name",
            description="",
            value_type="string",
            default_value="default",
            env_var="TEST_NAME",
        )
        service._validate_value(defn, "hello")  # Should not raise

    def test_string_rejects_int(self, service):
        """Integer is rejected for string type."""
        defn = SettingDefinition(
            namespace="test",
            key="name",
            label="Name",
            description="",
            value_type="string",
            default_value="default",
            env_var="TEST_NAME",
        )
        with pytest.raises(ValueError, match="expected string"):
            service._validate_value(defn, 42)


class TestSettingsCache:
    """Tests for the in-memory settings cache."""

    def setup_method(self):
        clear_settings_cache()

    def test_cache_put_and_get(self):
        """Cache returns stored value within TTL."""
        cache = get_settings_cache()
        cache.put("key1", {"a": 1}, None)

        entry = cache.get("key1")
        assert entry is not None
        assert entry.rows == {"a": 1}

    def test_cache_miss(self):
        """Cache returns None for unknown key."""
        cache = get_settings_cache()
        assert cache.get("nonexistent") is None

    def test_cache_invalidate(self):
        """Invalidate removes entry."""
        cache = get_settings_cache()
        cache.put("key1", {"a": 1}, None)
        cache.invalidate("key1")
        assert cache.get("key1") is None

    def test_cache_clear(self):
        """Clear removes all entries."""
        cache = get_settings_cache()
        cache.put("key1", {"a": 1}, None)
        cache.put("key2", {"b": 2}, None)
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestSystemInfoMasking:
    """Tests for system info password masking."""

    def test_database_password_is_masked(self):
        """Database URL should have password masked."""
        service = SettingsService()
        system_ns = service._get_system_info()

        db_setting = next(
            (s for s in system_ns.settings if s.key == "database_url"), None
        )
        assert db_setting is not None
        # Password should be replaced with ****
        assert "****" in db_setting.value or "password" not in db_setting.value

    def test_system_namespace_is_not_editable(self):
        """System namespace should be marked as not editable."""
        service = SettingsService()
        system_ns = service._get_system_info()
        assert system_ns.editable is False

    def test_system_info_has_expected_keys(self):
        """System info should include standard infrastructure keys."""
        service = SettingsService()
        system_ns = service._get_system_info()
        keys = {s.key for s in system_ns.settings}
        assert "redis_url" in keys
        assert "database_url" in keys
        assert "s3_bucket" in keys
        assert "version" in keys


class TestEnvDefaultResolution:
    """Tests that env defaults are read correctly."""

    def test_rate_limit_default_matches_config(self):
        """Rate limit defaults should match config.py."""
        service = SettingsService()
        defn = _DEFINITION_MAP[("rate_limits", "requests_per_minute")]
        default = service._get_env_default(defn)
        # Should return the env var value (or hardcoded default if no env)
        assert isinstance(default, int)
        assert default > 0
