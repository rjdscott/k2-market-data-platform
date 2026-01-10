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
from typing import Dict
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema
from confluent_kafka.schema_registry.error import SchemaRegistryError
import structlog

logger = structlog.get_logger()

SCHEMA_DIR = Path(__file__).parent


def load_avro_schema(schema_name: str) -> str:
    """
    Load Avro schema from .avsc file.

    Args:
        schema_name: Name of schema file without extension (e.g., 'trade')

    Returns:
        Schema definition as JSON string

    Raises:
        FileNotFoundError: If schema file doesn't exist
        json.JSONDecodeError: If schema file is not valid JSON
    """
    schema_path = SCHEMA_DIR / f"{schema_name}.avsc"

    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {schema_path}\n"
            f"Available schemas: {list_available_schemas()}"
        )

    schema_str = schema_path.read_text()

    # Validate JSON
    try:
        json.loads(schema_str)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in schema file", schema=schema_name, error=str(e))
        raise

    logger.debug("Schema loaded", schema=schema_name, path=str(schema_path))

    return schema_str


def list_available_schemas() -> list[str]:
    """
    List all available Avro schemas.

    Returns:
        List of schema names (without .avsc extension)
    """
    schema_files = SCHEMA_DIR.glob("*.avsc")
    return [f.stem for f in schema_files]


def register_schemas(
    schema_registry_url: str = None
) -> Dict[str, int]:
    """
    Register all Avro schemas with Schema Registry.

    This function:
    1. Loads all .avsc files from the schemas directory
    2. Registers them with the Schema Registry
    3. Returns mapping of schema name to schema ID

    Schema subjects are named: market.{schema_name}s.raw-value
    For example: market.trades.raw-value

    Args:
        schema_registry_url: Schema Registry base URL (defaults to config)

    Returns:
        Dictionary mapping schema names to schema IDs

    Raises:
        SchemaRegistryError: If registration fails
    """
    from k2.common.config import config

    if schema_registry_url is None:
        schema_registry_url = config.kafka.schema_registry_url

    logger.info("Registering schemas", registry_url=schema_registry_url)

    client = SchemaRegistryClient({'url': schema_registry_url})

    schema_ids = {}

    for schema_name in list_available_schemas():
        try:
            # Load schema
            schema_str = load_avro_schema(schema_name)
            schema = Schema(schema_str, schema_type='AVRO')

            # Determine subject name
            # Convention: topic-value for value schemas
            if schema_name == 'reference_data':
                subject = f"market.{schema_name}-value"
            else:
                subject = f"market.{schema_name}s.raw-value"

            # Register schema
            schema_id = client.register_schema(subject, schema)

            schema_ids[schema_name] = schema_id

            logger.info(
                "Schema registered successfully",
                schema=schema_name,
                subject=subject,
                schema_id=schema_id,
            )

        except SchemaRegistryError as e:
            logger.error(
                "Failed to register schema",
                schema=schema_name,
                error=str(e),
            )
            raise

        except Exception as e:
            logger.error(
                "Unexpected error registering schema",
                schema=schema_name,
                error=str(e),
            )
            raise

    logger.info("All schemas registered", count=len(schema_ids))

    return schema_ids


def get_schema_registry_client(
    schema_registry_url: str = None
) -> SchemaRegistryClient:
    """
    Create Schema Registry client.

    Args:
        schema_registry_url: Schema Registry base URL (defaults to config)

    Returns:
        Configured SchemaRegistryClient instance
    """
    from k2.common.config import config

    if schema_registry_url is None:
        schema_registry_url = config.kafka.schema_registry_url

    return SchemaRegistryClient({'url': schema_registry_url})
