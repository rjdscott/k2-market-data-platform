"""
Unit tests for K2 Market Data Platform configuration management.

Tests cover:
- Configuration loading and validation
- Environment variable handling
- Default value management
- Error handling for invalid configurations
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from k2.common.config import Config, ConfigError


class TestConfig:
    """Test suite for configuration management."""

    def test_default_config_loading(self):
        """Test loading default configuration values."""
        config = Config()

        # Verify default values
        assert config.kafka.brokers == "localhost:9092"
        assert config.kafka.schema_registry_url == "http://localhost:8081"
        assert config.storage.warehouse == "s3://k2-lakehouse/warehouse"
        assert config.api.host == "0.0.0.0"
        assert config.api.port == 8000

    def test_config_from_yaml_file(self):
        """Test loading configuration from YAML file."""
        config_data = {
            "kafka": {
                "brokers": "kafka.example.com:9092",
                "schema_registry_url": "http://schema-registry.example.com:8081",
            },
            "storage": {"warehouse": "s3://custom-bucket/warehouse"},
            "api": {"port": 9000},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            config = Config.from_file(config_file)

            assert config.kafka.brokers == "kafka.example.com:9092"
            assert config.kafka.schema_registry_url == "http://schema-registry.example.com:8081"
            assert config.storage.warehouse == "s3://custom-bucket/warehouse"
            assert config.api.port == 9000
            # Should still have default for non-specified values
            assert config.api.host == "0.0.0.0"

        finally:
            os.unlink(config_file)

    def test_config_from_environment_variables(self):
        """Test loading configuration from environment variables."""
        env_vars = {
            "K2_KAFKA_BROKERS": "env-kafka:9092",
            "K2_STORAGE_WAREHOUSE": "s3://env-bucket/warehouse",
            "K2_API_PORT": "8080",
        }

        with patch.dict(os.environ, env_vars):
            config = Config.from_env()

            assert config.kafka.brokers == "env-kafka:9092"
            assert config.storage.warehouse == "s3://env-bucket/warehouse"
            assert config.api.port == 8080

    def test_config_precedence(self):
        """Test configuration precedence: env vars > file > defaults."""
        config_data = {"kafka": {"brokers": "file-kafka:9092"}, "api": {"port": 8000}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name

        env_vars = {
            "K2_API_PORT": "9000"  # This should override file
        }

        try:
            with patch.dict(os.environ, env_vars):
                config = Config.from_file(config_file, from_env=True)

                # File value (no env override)
                assert config.kafka.brokers == "file-kafka:9092"
                # Env value overrides file
                assert config.api.port == 9000
                # Default value (no file or env)
                assert config.kafka.schema_registry_url == "http://localhost:8081"

        finally:
            os.unlink(config_file)

    def test_config_validation(self):
        """Test configuration validation."""
        # Test invalid port
        with pytest.raises(ConfigError) as exc_info:
            Config(api_port="invalid_port")

        assert "port" in str(exc_info.value).lower()

        # Test invalid URL
        with pytest.raises(ConfigError) as exc_info:
            Config(kafka_schema_registry_url="not-a-url")

        assert "schema_registry_url" in str(exc_info.value)

    def test_config_merge(self):
        """Test merging multiple configuration sources."""
        base_config = Config()

        # Create partial config
        partial_data = {
            "kafka": {"brokers": "merged-kafka:9092"},
            "storage": {"warehouse": "s3://merged-bucket/warehouse"},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(partial_data, f)
            config_file = f.name

        try:
            merged_config = base_config.merge_from_file(config_file)

            # Merged values
            assert merged_config.kafka.brokers == "merged-kafka:9092"
            assert merged_config.storage.warehouse == "s3://merged-bucket/warehouse"
            # Original values preserved
            assert merged_config.kafka.schema_registry_url == base_config.kafka.schema_registry_url
            assert merged_config.api.port == base_config.api.port

        finally:
            os.unlink(config_file)

    def test_config_sensitive_data_handling(self):
        """Test handling of sensitive configuration data."""
        config_data = {
            "kafka": {"sasl_username": "test_user", "sasl_password": "test_password"},
            "storage": {"access_key": "test_access_key", "secret_key": "test_secret_key"},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            config = Config.from_file(config_file)

            # Values should be loaded
            assert config.kafka.sasl_username == "test_user"
            assert config.kafka.sasl_password == "test_password"

            # But should not appear in string representation
            config_str = str(config)
            assert "test_password" not in config_str
            assert "test_secret_key" not in config_str

        finally:
            os.unlink(config_file)

    def test_config_reload(self):
        """Test configuration reloading."""
        config_data = {"api": {"port": 8000}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            config = Config.from_file(config_file)
            assert config.api.port == 8000

            # Update file
            updated_data = {"api": {"port": 9000}}

            with open(config_file, "w") as f:
                yaml.dump(updated_data, f)

            # Reload config
            config.reload()
            assert config.api.port == 9000

        finally:
            os.unlink(config_file)

    def test_config_watch_changes(self):
        """Test watching for configuration changes."""
        config_data = {"api": {"port": 8000}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            config = Config.from_file(config_file)
            changes_detected = []

            def change_handler(key, old_value, new_value):
                changes_detected.append((key, old_value, new_value))

            config.watch_changes(change_handler)

            # Update file
            updated_data = {"api": {"port": 9000}}

            with open(config_file, "w") as f:
                yaml.dump(updated_data, f)

            # Trigger file check
            config.check_for_changes()

            # Verify change was detected
            assert len(changes_detected) == 1
            key, old_value, new_value = changes_detected[0]
            assert key == "api.port"
            assert old_value == 8000
            assert new_value == 9000

        finally:
            os.unlink(config_file)


class TestConfigSections:
    """Test specific configuration sections."""

    def test_kafka_config(self):
        """Test Kafka configuration section."""
        config = Config(kafka_brokers="kafka1:9092,kafka2:9092")

        assert config.kafka.brokers == "kafka1:9092,kafka2:9092"
        assert config.kafka.schema_registry_url == "http://localhost:8081"
        assert config.kafka.group_id_prefix == "k2-"

    def test_storage_config(self):
        """Test storage configuration section."""
        config = Config(
            storage_warehouse="s3://test-bucket/warehouse", storage_catalog_backend="jdbc"
        )

        assert config.storage.warehouse == "s3://test-bucket/warehouse"
        assert config.storage.catalog_backend == "jdbc"
        assert config.storage.catalog_uri == "jdbc:sqlite::memory:"

    def test_api_config(self):
        """Test API configuration section."""
        config = Config(api_host="127.0.0.1", api_port=8080, api_workers=4)

        assert config.api.host == "127.0.0.1"
        assert config.api.port == 8080
        assert config.api.workers == 4
        assert config.api.enable_cors is True

    def test_monitoring_config(self):
        """Test monitoring configuration section."""
        config = Config(monitoring_metrics_enabled=True, monitoring_tracing_enabled=False)

        assert config.monitoring.metrics_enabled is True
        assert config.monitoring.tracing_enabled is False
        assert config.monitoring.prometheus_port == 9090


class TestConfigEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_config_file(self):
        """Test loading empty configuration file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")  # Empty file
            config_file = f.name

        try:
            config = Config.from_file(config_file)
            # Should load with all defaults
            assert config.kafka.brokers == "localhost:9092"

        finally:
            os.unlink(config_file)

    def test_malformed_config_file(self):
        """Test loading malformed YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [")  # Invalid YAML
            config_file = f.name

        try:
            with pytest.raises(ConfigError):
                Config.from_file(config_file)

        finally:
            os.unlink(config_file)

    def test_nonexistent_config_file(self):
        """Test loading non-existent configuration file."""
        with pytest.raises(ConfigError) as exc_info:
            Config.from_file("/nonexistent/config.yaml")

        assert "not found" in str(exc_info.value).lower()

    def test_config_with_unknown_fields(self):
        """Test configuration with unknown fields."""
        config_data = {
            "kafka": {"brokers": "localhost:9092"},
            "unknown_section": {"unknown_field": "value"},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            # Should not raise error, just ignore unknown fields
            config = Config.from_file(config_file)
            assert config.kafka.brokers == "localhost:9092"

        finally:
            os.unlink(config_file)

    def test_config_with_circular_references(self):
        """Test handling of circular references in configuration."""
        # This would be more relevant for complex nested configs
        # For now, just test that the system doesn't crash
        config = Config()
        config_dict = config.to_dict()

        # Should be serializable without circular references
        import json

        json_str = json.dumps(config_dict)
        assert json_str is not None
