"""Schema management for K2 platform.

This module provides utilities for loading Avro schemas and registering them
with the Confluent Schema Registry.

Avro schemas provide:
- Strong typing and validation
- Schema evolution with compatibility checks
- Efficient binary serialization
- Self-describing data format

Schema files are located in this directory:
- trade.avsc: Trade execution events
- quote.avsc: Best bid/ask quote events
- reference_data.avsc: Company reference data

Usage:
    from k2.schemas import load_avro_schema, register_schemas

    # Load schema as string
    schema_str = load_avro_schema('trade')

    # Register all schemas with Schema Registry
    schema_ids = register_schemas('http://localhost:8081')
"""

import json
from pathlib import Path

import structlog
from confluent_kafka.schema_registry import Schema, SchemaRegistryClient
from confluent_kafka.schema_registry.error import SchemaRegistryError

logger = structlog.get_logger()

SCHEMA_DIR = Path(__file__).parent


def load_avro_schema(schema_name: str, version: str = "v1") -> str:
    """Load Avro schema from .avsc file.

    Supports both v1 (legacy ASX-specific) and v2 (industry-standard) schemas.

    Args:
        schema_name: Name of schema file without extension (e.g., 'trade', 'quote')
        version: Schema version - 'v1' (default) or 'v2'

    Returns:
        Schema definition as JSON string

    Raises:
        FileNotFoundError: If schema file doesn't exist
        json.JSONDecodeError: If schema file is not valid JSON
        ValueError: If version is not 'v1' or 'v2'

    Examples:
        >>> # Load v1 schema (legacy)
        >>> schema_v1 = load_avro_schema('trade', version='v1')
        >>> # Load v2 schema (industry-standard)
        >>> schema_v2 = load_avro_schema('trade', version='v2')
    """
    if version not in ("v1", "v2"):
        raise ValueError(f"Invalid version: {version}. Must be 'v1' or 'v2'")

    # Determine filename based on version
    if version == "v2":
        filename = f"{schema_name}_v2.avsc"
    else:
        filename = f"{schema_name}.avsc"

    schema_path = SCHEMA_DIR / filename

    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {schema_path}\nAvailable schemas: {list_available_schemas()}",
        )

    schema_str = schema_path.read_text()

    # Validate JSON
    try:
        json.loads(schema_str)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in schema file", schema=schema_name, error=str(e))
        raise

    logger.debug("Schema loaded", schema=schema_name, version=version, path=str(schema_path))

    return schema_str


def list_available_schemas() -> list[str]:
    """List all available Avro schemas.

    Returns:
        List of schema names (without .avsc extension)
    """
    schema_files = SCHEMA_DIR.glob("*.avsc")
    return [f.stem for f in schema_files]


def register_schemas(schema_registry_url: str = None, version: str = "v2") -> dict[str, int]:
    """Register all Avro schemas with Schema Registry using asset-class-level subjects.

    This function:
    1. Reads topic configuration to determine asset classes
    2. Loads .avsc files from the schemas directory
    3. Registers schemas with asset-class-level subject naming
    4. Returns mapping of subject name to schema ID

    Schema subject naming: market.{asset_class}.{data_type}-value
    Examples:
        - market.equities.trades-value (shared by ASX, NYSE, etc.)
        - market.crypto.quotes-value (shared by Binance, Coinbase, etc.)

    This approach uses shared schemas across exchanges within the same asset class,
    simplifying schema management and evolution.

    Args:
        schema_registry_url: Schema Registry base URL (defaults to config)
        version: Schema version to register - 'v1' or 'v2' (default: 'v2')

    Returns:
        Dictionary mapping subject names to schema IDs

    Raises:
        SchemaRegistryError: If registration fails
        ValueError: If version is not 'v1' or 'v2'
    """
    from k2.common.config import config
    from k2.kafka import get_topic_builder

    if schema_registry_url is None:
        schema_registry_url = config.kafka.schema_registry_url

    if version not in ("v1", "v2"):
        raise ValueError(f"Invalid version: {version}. Must be 'v1' or 'v2'")

    logger.info(
        "Registering schemas with asset-class-level subjects",
        registry_url=schema_registry_url,
        version=version,
    )

    client = SchemaRegistryClient({"url": schema_registry_url})
    topic_builder = get_topic_builder()

    schema_ids = {}

    # Get schema registry config
    subject_pattern = topic_builder.config["schema_registry"]["subject_pattern"]

    # Register schemas for each asset class
    for asset_class in topic_builder.config["asset_classes"].keys():
        for data_type_name, data_type_cfg in topic_builder.config["data_types"].items():
            schema_name = data_type_cfg["schema_name"]

            try:
                # Load schema with specified version
                schema_str = load_avro_schema(schema_name, version=version)
                schema = Schema(schema_str, schema_type="AVRO")

                # Build subject name using asset-class-level pattern
                subject = subject_pattern.format(asset_class=asset_class, data_type=data_type_name)

                # Register schema
                schema_id = client.register_schema(subject, schema)

                schema_ids[subject] = schema_id

                logger.info(
                    "Schema registered successfully",
                    schema=schema_name,
                    subject=subject,
                    schema_id=schema_id,
                    asset_class=asset_class,
                    data_type=data_type_name,
                )

            except SchemaRegistryError as e:
                logger.error(
                    "Failed to register schema",
                    schema=schema_name,
                    subject=subject,
                    asset_class=asset_class,
                    data_type=data_type_name,
                    error=str(e),
                )
                raise

            except Exception as e:
                logger.error(
                    "Unexpected error registering schema",
                    schema=schema_name,
                    subject=subject,
                    asset_class=asset_class,
                    data_type=data_type_name,
                    error=str(e),
                )
                raise

    logger.info("All schemas registered", count=len(schema_ids))

    return schema_ids


def get_schema_registry_client(schema_registry_url: str = None) -> SchemaRegistryClient:
    """Create Schema Registry client.

    Args:
        schema_registry_url: Schema Registry base URL (defaults to config)

    Returns:
        Configured SchemaRegistryClient instance
    """
    from k2.common.config import config

    if schema_registry_url is None:
        schema_registry_url = config.kafka.schema_registry_url

    return SchemaRegistryClient({"url": schema_registry_url})
