"""Unit tests for configuration management.

Tests configuration loading, validation, and environment variable overrides.
"""
import pytest
from pydantic import ValidationError

from k2.common.config import (
    KafkaConfig,
    IcebergConfig,
    DatabaseConfig,
    ObservabilityConfig,
    K2Config,
)


class TestKafkaConfig:
    """Test Kafka configuration."""

    def test_default_values(self):
        """Test default Kafka configuration values."""
        config = KafkaConfig()
        assert config.bootstrap_servers == "localhost:9092"
        assert config.schema_registry_url == "http://localhost:8081"

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("K2_KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
        monkeypatch.setenv("K2_KAFKA_SCHEMA_REGISTRY_URL", "http://registry:8081")

        config = KafkaConfig()
        assert config.bootstrap_servers == "kafka:29092"
        assert config.schema_registry_url == "http://registry:8081"

    def test_bootstrap_servers_validation(self):
        """Test bootstrap servers validation."""
        # Empty value should fail
        with pytest.raises(ValidationError):
            KafkaConfig(bootstrap_servers="")

        # Whitespace-only should fail
        with pytest.raises(ValidationError):
            KafkaConfig(bootstrap_servers="   ")

    def test_schema_registry_url_validation(self):
        """Test schema registry URL validation."""
        # Must start with http:// or https://
        with pytest.raises(ValidationError):
            KafkaConfig(schema_registry_url="invalid-url")

        # Valid HTTP URL
        config = KafkaConfig(schema_registry_url="http://localhost:8081")
        assert config.schema_registry_url == "http://localhost:8081"

        # Valid HTTPS URL
        config = KafkaConfig(schema_registry_url="https://registry.example.com")
        assert config.schema_registry_url == "https://registry.example.com"

    def test_trailing_slash_removal(self):
        """Test trailing slash is removed from URLs."""
        config = KafkaConfig(schema_registry_url="http://localhost:8081/")
        assert config.schema_registry_url == "http://localhost:8081"


class TestIcebergConfig:
    """Test Iceberg configuration."""

    def test_default_values(self):
        """Test default Iceberg configuration values."""
        config = IcebergConfig()
        assert config.catalog_uri == "http://localhost:8181"
        assert config.s3_endpoint == "http://localhost:9000"
        assert config.s3_access_key == "admin"
        assert config.s3_secret_key == "password"
        assert config.warehouse == "s3://warehouse/"

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("K2_ICEBERG_CATALOG_URI", "http://iceberg:8181")
        monkeypatch.setenv("K2_ICEBERG_S3_ENDPOINT", "http://minio:9000")
        monkeypatch.setenv("K2_ICEBERG_S3_ACCESS_KEY", "testkey")
        monkeypatch.setenv("K2_ICEBERG_S3_SECRET_KEY", "testsecret")
        monkeypatch.setenv("K2_ICEBERG_WAREHOUSE", "s3://testbucket/data")

        config = IcebergConfig()
        assert config.catalog_uri == "http://iceberg:8181"
        assert config.s3_endpoint == "http://minio:9000"
        assert config.s3_access_key == "testkey"
        assert config.s3_secret_key == "testsecret"
        assert config.warehouse == "s3://testbucket/data"

    def test_url_validation(self):
        """Test URL validation for catalog and S3 endpoint."""
        # Invalid catalog URI
        with pytest.raises(ValidationError):
            IcebergConfig(catalog_uri="invalid-uri")

        # Invalid S3 endpoint
        with pytest.raises(ValidationError):
            IcebergConfig(s3_endpoint="not-a-url")

        # Valid URLs
        config = IcebergConfig(
            catalog_uri="https://iceberg.example.com",
            s3_endpoint="https://s3.amazonaws.com"
        )
        assert config.catalog_uri == "https://iceberg.example.com"
        assert config.s3_endpoint == "https://s3.amazonaws.com"

    def test_warehouse_validation(self):
        """Test warehouse path validation."""
        # Must be S3 path
        with pytest.raises(ValidationError):
            IcebergConfig(warehouse="/local/path")

        with pytest.raises(ValidationError):
            IcebergConfig(warehouse="http://example.com/path")

        # Valid S3 path
        config = IcebergConfig(warehouse="s3://my-bucket/warehouse/")
        assert config.warehouse == "s3://my-bucket/warehouse/"


class TestDatabaseConfig:
    """Test database configuration."""

    def test_default_values(self):
        """Test default database configuration values."""
        config = DatabaseConfig()
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.user == "iceberg"
        assert config.password == "iceberg"
        assert config.database == "iceberg_catalog"

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("K2_DB_HOST", "postgres")
        monkeypatch.setenv("K2_DB_PORT", "5433")
        monkeypatch.setenv("K2_DB_USER", "testuser")
        monkeypatch.setenv("K2_DB_PASSWORD", "testpass")
        monkeypatch.setenv("K2_DB_DATABASE", "testdb")

        config = DatabaseConfig()
        assert config.host == "postgres"
        assert config.port == 5433
        assert config.user == "testuser"
        assert config.password == "testpass"
        assert config.database == "testdb"

    def test_port_validation(self):
        """Test port number validation."""
        # Port too low
        with pytest.raises(ValidationError):
            DatabaseConfig(port=0)

        # Port too high
        with pytest.raises(ValidationError):
            DatabaseConfig(port=70000)

        # Valid port
        config = DatabaseConfig(port=5432)
        assert config.port == 5432

    def test_connection_string(self):
        """Test connection string generation."""
        config = DatabaseConfig(
            host="postgres",
            port=5432,
            user="myuser",
            password="mypassword",
            database="mydb"
        )
        expected = "postgresql://myuser:mypassword@postgres:5432/mydb"
        assert config.connection_string == expected


class TestObservabilityConfig:
    """Test observability configuration."""

    def test_default_values(self):
        """Test default observability configuration values."""
        config = ObservabilityConfig()
        assert config.log_level == "INFO"
        assert config.prometheus_port == 9090
        assert config.enable_metrics is True

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("K2_OBSERVABILITY_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("K2_OBSERVABILITY_PROMETHEUS_PORT", "9091")
        monkeypatch.setenv("K2_OBSERVABILITY_ENABLE_METRICS", "false")

        config = ObservabilityConfig()
        assert config.log_level == "DEBUG"
        assert config.prometheus_port == 9091
        assert config.enable_metrics is False

    def test_log_level_validation(self):
        """Test log level validation."""
        # Valid log levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = ObservabilityConfig(log_level=level)
            assert config.log_level == level

        # Invalid log level
        with pytest.raises(ValidationError):
            ObservabilityConfig(log_level="INVALID")

    def test_prometheus_port_validation(self):
        """Test Prometheus port validation."""
        # Port too low (below 1024)
        with pytest.raises(ValidationError):
            ObservabilityConfig(prometheus_port=80)

        # Port too high
        with pytest.raises(ValidationError):
            ObservabilityConfig(prometheus_port=70000)

        # Valid port
        config = ObservabilityConfig(prometheus_port=9091)
        assert config.prometheus_port == 9091


class TestK2Config:
    """Test main K2 configuration."""

    def test_default_values(self):
        """Test default K2 configuration values."""
        config = K2Config()
        assert config.environment == "local"
        assert isinstance(config.kafka, KafkaConfig)
        assert isinstance(config.iceberg, IcebergConfig)
        assert isinstance(config.database, DatabaseConfig)
        assert isinstance(config.observability, ObservabilityConfig)

    def test_environment_validation(self):
        """Test environment validation."""
        # Valid environments
        for env in ["local", "test", "staging", "production"]:
            config = K2Config(environment=env)
            assert config.environment == env

        # Invalid environment
        with pytest.raises(ValidationError):
            K2Config(environment="invalid")

    def test_nested_config_access(self):
        """Test accessing nested configuration."""
        config = K2Config()

        # Kafka config
        assert config.kafka.bootstrap_servers == "localhost:9092"
        assert config.kafka.schema_registry_url == "http://localhost:8081"

        # Iceberg config
        assert config.iceberg.catalog_uri == "http://localhost:8181"
        assert config.iceberg.warehouse == "s3://warehouse/"

        # Database config
        assert config.database.host == "localhost"
        assert config.database.port == 5432

        # Observability config
        assert config.observability.log_level == "INFO"
        assert config.observability.enable_metrics is True

    def test_env_override_nested(self, monkeypatch):
        """Test environment variable override for nested configs."""
        # Set various env vars
        monkeypatch.setenv("K2_ENVIRONMENT", "production")
        monkeypatch.setenv("K2_KAFKA_BOOTSTRAP_SERVERS", "kafka1:9092,kafka2:9092")
        monkeypatch.setenv("K2_ICEBERG_CATALOG_URI", "https://iceberg.prod.example.com")
        monkeypatch.setenv("K2_DB_HOST", "postgres.prod.example.com")
        monkeypatch.setenv("K2_OBSERVABILITY_LOG_LEVEL", "WARNING")

        config = K2Config()
        assert config.environment == "production"
        assert config.kafka.bootstrap_servers == "kafka1:9092,kafka2:9092"
        assert config.iceberg.catalog_uri == "https://iceberg.prod.example.com"
        assert config.database.host == "postgres.prod.example.com"
        assert config.observability.log_level == "WARNING"


class TestConfigSingleton:
    """Test global config singleton."""

    def test_singleton_import(self):
        """Test importing the global config singleton."""
        from k2.common.config import config

        assert isinstance(config, K2Config)
        assert config.environment in ["local", "test", "staging", "production"]

    def test_singleton_is_same_instance(self):
        """Test that importing config multiple times gives same instance."""
        from k2.common.config import config as config1
        from k2.common.config import config as config2

        assert config1 is config2
