"""Centralized configuration management for K2 platform.

This module provides type-safe configuration using Pydantic Settings with
environment variable override support. Configuration is hierarchical:
- KafkaConfig: Kafka and Schema Registry settings
- IcebergConfig: Iceberg catalog and S3 storage settings
- DatabaseConfig: PostgreSQL connection settings
- ObservabilityConfig: Logging and metrics settings
- K2Config: Main configuration aggregating all sub-configs

Environment variables follow the pattern: K2_{COMPONENT}_{PARAMETER}

Examples:
    K2_KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    K2_ICEBERG_CATALOG_URI=http://iceberg-rest:8181
    K2_DB_HOST=postgres

Usage:
    from k2.common.config import config

    # Access configuration
    print(config.kafka.bootstrap_servers)
    print(config.iceberg.catalog_uri)

    # Configuration is validated on load
    # Invalid values will raise ValidationError
"""

from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KafkaConfig(BaseSettings):
    """Kafka and Schema Registry configuration.

    Controls connection to Kafka brokers and Schema Registry for
    Avro schema management and event streaming.
    """

    model_config = SettingsConfigDict(env_prefix="K2_KAFKA_", case_sensitive=False, extra="ignore")

    bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka bootstrap servers (comma-separated for multiple brokers)",
    )

    schema_registry_url: str = Field(
        default="http://localhost:8081",
        description="Confluent Schema Registry URL for Avro schema management",
    )

    @field_validator("bootstrap_servers")
    @classmethod
    def validate_bootstrap_servers(cls, v: str) -> str:
        """Ensure bootstrap servers is not empty."""
        if not v or not v.strip():
            raise ValueError("bootstrap_servers cannot be empty")
        return v.strip()

    @field_validator("schema_registry_url")
    @classmethod
    def validate_schema_registry_url(cls, v: str) -> str:
        """Ensure schema registry URL is a valid HTTP(S) URL."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("schema_registry_url must start with http:// or https://")
        return v.rstrip("/")


class IcebergConfig(BaseSettings):
    """Apache Iceberg lakehouse configuration.

    Controls connection to Iceberg REST catalog and S3-compatible storage
    (MinIO in development, S3 in production).
    """

    model_config = SettingsConfigDict(
        env_prefix="K2_ICEBERG_", case_sensitive=False, extra="ignore",
    )

    catalog_uri: str = Field(
        default="http://localhost:8181", description="Iceberg REST catalog URI",
    )

    s3_endpoint: str = Field(default="http://localhost:9000", description="S3/MinIO endpoint URL")

    s3_access_key: str = Field(default="admin", description="S3/MinIO access key ID")

    s3_secret_key: str = Field(default="password", description="S3/MinIO secret access key")

    warehouse: str = Field(
        default="s3://warehouse/", description="Iceberg warehouse location (S3 path)",
    )

    @field_validator("catalog_uri", "s3_endpoint")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URLs are valid HTTP(S) URLs."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v.rstrip("/")

    @field_validator("warehouse")
    @classmethod
    def validate_warehouse(cls, v: str) -> str:
        """Ensure warehouse path is valid S3 path."""
        if not v.startswith("s3://"):
            raise ValueError("warehouse must be an S3 path (s3://bucket/path)")
        return v


class DatabaseConfig(BaseSettings):
    """PostgreSQL database configuration.

    Used for Iceberg catalog metadata and sequence tracking.
    """

    model_config = SettingsConfigDict(env_prefix="K2_DB_", case_sensitive=False, extra="ignore")

    host: str = Field(default="localhost", description="PostgreSQL host")

    port: int = Field(default=5432, description="PostgreSQL port", ge=1, le=65535)

    user: str = Field(default="iceberg", description="PostgreSQL username")

    password: str = Field(default="iceberg", description="PostgreSQL password")

    database: str = Field(default="iceberg_catalog", description="PostgreSQL database name")

    @property
    def connection_string(self) -> str:
        """Generate PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class ObservabilityConfig(BaseSettings):
    """Logging and metrics configuration.

    Controls structured logging level and Prometheus metrics collection.
    """

    model_config = SettingsConfigDict(
        env_prefix="K2_OBSERVABILITY_", case_sensitive=False, extra="ignore",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    prometheus_port: int = Field(
        default=9090, description="Prometheus metrics port", ge=1024, le=65535,
    )

    enable_metrics: bool = Field(default=True, description="Enable Prometheus metrics collection")


class BinanceConfig(BaseSettings):
    """Binance WebSocket streaming configuration.

    Controls connection to Binance WebSocket API for real-time trade streaming.
    """

    model_config = SettingsConfigDict(
        env_prefix="K2_BINANCE_", case_sensitive=False, extra="ignore"
    )

    enabled: bool = Field(default=False, description="Enable Binance streaming")

    websocket_url: str = Field(
        default="wss://stream.binance.com:9443/stream",
        description="Binance WebSocket stream URL",
    )

    failover_urls: list[str] = Field(
        default=["wss://stream.binance.us:9443/stream"],
        description="Failover WebSocket URLs (tried in order if primary fails)",
    )

    symbols: list[str] = Field(
        default=["BTCUSDT", "ETHUSDT"],
        description="List of symbols to stream (e.g., BTCUSDT, ETHBTC)",
    )

    reconnect_delay: int = Field(
        default=5, description="Initial reconnect delay in seconds (exponential backoff)"
    )

    max_reconnect_attempts: int = Field(
        default=10, description="Maximum number of reconnect attempts before giving up"
    )

    health_check_interval: int = Field(
        default=30, description="Health check interval in seconds (check for stale connection)"
    )

    health_check_timeout: int = Field(
        default=60,
        description="Max seconds without message before triggering reconnect (0 = disabled)",
    )

    ssl_verify: bool = Field(
        default=True,
        description="Enable SSL certificate verification (REQUIRED for production)",
    )

    custom_ca_bundle: Optional[str] = Field(
        default=None,
        description="Path to custom CA certificate bundle (for corporate proxies)",
    )

    connection_max_lifetime_hours: int = Field(
        default=4,
        description="Maximum connection lifetime before rotation (hours, prevents memory accumulation)",
        ge=1,
        le=24,
    )

    @field_validator("websocket_url")
    @classmethod
    def validate_websocket_url(cls, v: str) -> str:
        """Ensure WebSocket URL starts with wss:// or ws://."""
        if not v.startswith(("wss://", "ws://")):
            raise ValueError("websocket_url must start with wss:// or ws://")
        return v.rstrip("/")


class K2Config(BaseSettings):
    """Main K2 platform configuration.

    Aggregates all sub-configurations into a single config object.
    Automatically loads from environment variables with K2_ prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="K2_",
        case_sensitive=False,
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    environment: Literal["local", "test", "staging", "production"] = Field(
        default="local", description="Deployment environment",
    )

    # Sub-configurations
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    iceberg: IcebergConfig = Field(default_factory=IcebergConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    binance: BinanceConfig = Field(default_factory=BinanceConfig)


# Global singleton instance
# Import this in other modules: from k2.common.config import config
config = K2Config()
