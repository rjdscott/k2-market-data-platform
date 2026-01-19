"""Avro deserialization UDF for stripping Schema Registry headers.

This module provides a Spark UDF to deserialize Avro-encoded trade data
from Bronze layer raw bytes (including Schema Registry 5-byte header).

Schema Registry Header Format (5 bytes):
    [0]:     Magic byte (0x00)
    [1-4]:   Schema ID (big-endian int32)
    [5-end]: Avro payload

Industry Best Practice:
- Bronze stores raw bytes (immutable source)
- Silver deserializes using Schema Registry
- Failures routed to DLQ for observability

Usage:
    from k2.spark.udfs.avro_deserialization import deserialize_trade_avro

    df = bronze_df.withColumn("trade", deserialize_trade_avro(col("raw_bytes")))
"""

import struct
import json
from typing import Dict, Optional
from io import BytesIO

from pyspark.sql.functions import udf, pandas_udf
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    DecimalType,
    ArrayType,
    IntegerType,
    BinaryType,
)

# V2 Trade Schema (matches trade_v2.avsc)
TRADE_V2_SCHEMA = StructType(
    [
        StructField("message_id", StringType(), False),
        StructField("trade_id", StringType(), False),
        StructField("symbol", StringType(), False),
        StructField("exchange", StringType(), False),
        StructField("asset_class", StringType(), False),
        StructField("timestamp", LongType(), False),  # microseconds since epoch
        StructField("price", DecimalType(18, 8), False),
        StructField("quantity", DecimalType(18, 8), False),
        StructField("currency", StringType(), False),
        StructField("side", StringType(), False),
        StructField("trade_conditions", ArrayType(StringType()), False),
        StructField("source_sequence", LongType(), True),  # nullable
        StructField("ingestion_timestamp", LongType(), False),
        StructField("platform_sequence", LongType(), True),  # nullable
        StructField("vendor_data", StringType(), True),  # JSON string, nullable
        StructField("schema_id", IntegerType(), False),  # Schema Registry ID
    ]
)


# Schema Registry client (singleton pattern for caching)
class SchemaRegistryClient:
    """Simple Schema Registry client with caching.

    Industry best practice: Cache schemas to avoid repeated HTTP calls.
    """

    _instance = None
    _cache: Dict[int, str] = {}  # schema_id → schema_json

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, url: str = "http://schema-registry-1:8081"):
        self.url = url

    def get_schema_json(self, schema_id: int) -> str:
        """Fetch schema from registry by ID (with caching)."""
        if schema_id not in self._cache:
            try:
                import requests

                response = requests.get(f"{self.url}/schemas/ids/{schema_id}")
                response.raise_for_status()
                self._cache[schema_id] = response.json()["schema"]
            except Exception as e:
                raise ValueError(f"Failed to fetch schema ID {schema_id}: {e}")

        return self._cache[schema_id]


@udf(returnType=TRADE_V2_SCHEMA)
def deserialize_trade_avro(raw_bytes: bytes) -> Optional[tuple]:
    """Deserialize Avro trade from raw bytes with Schema Registry header.

    Args:
        raw_bytes: Full Kafka value (5-byte Schema Registry header + Avro payload)

    Returns:
        Tuple of deserialized trade fields (V2 schema + schema_id)
        Returns None if deserialization fails (handled by validation logic)

    Raises:
        ValueError: If magic byte invalid or payload too short
    """
    if not raw_bytes or len(raw_bytes) < 6:
        raise ValueError(f"raw_bytes too short: {len(raw_bytes) if raw_bytes else 0} bytes (need ≥6)")

    # Parse Schema Registry header (5 bytes)
    magic_byte = raw_bytes[0]
    if magic_byte != 0x00:
        raise ValueError(f"Invalid magic byte: {hex(magic_byte)} (expected 0x00)")

    schema_id = struct.unpack(">I", raw_bytes[1:5])[0]  # Big-endian int32
    avro_payload = raw_bytes[5:]

    # Deserialize Avro using avro-python3 library
    try:
        import avro.schema
        import avro.io

        # Fetch schema from registry (cached)
        client = SchemaRegistryClient(url="http://schema-registry-1:8081")
        schema_json = client.get_schema_json(schema_id)
        schema = avro.schema.parse(schema_json)

        # Deserialize Avro binary
        bytes_reader = BytesIO(avro_payload)
        decoder = avro.io.BinaryDecoder(bytes_reader)
        reader = avro.io.DatumReader(schema)
        record = reader.read(decoder)

        # Convert vendor_data map to JSON string (for Spark compatibility)
        vendor_data_json = None
        if record.get("vendor_data"):
            vendor_data_json = json.dumps(record["vendor_data"])

        # Return tuple matching TRADE_V2_SCHEMA (16 fields)
        return (
            record["message_id"],
            record["trade_id"],
            record["symbol"],
            record["exchange"],
            record["asset_class"],
            record["timestamp"],
            record["price"],
            record["quantity"],
            record["currency"],
            record["side"],
            record.get("trade_conditions", []),
            record.get("source_sequence"),  # nullable
            record["ingestion_timestamp"],
            record.get("platform_sequence"),  # nullable
            vendor_data_json,  # JSON string
            schema_id,  # Add schema_id for observability
        )

    except Exception as e:
        # Return None for deserialization failures (will be caught by validation logic)
        # DLQ will capture the error with raw_bytes for debugging
        raise ValueError(f"Avro deserialization failed: {e}")


# Binary helper UDF to extract schema_id without full deserialization
# Useful for DLQ error tracking when deserialization fails
@udf(returnType=IntegerType())
def extract_schema_id(raw_bytes: bytes) -> Optional[int]:
    """Extract Schema Registry schema ID from raw bytes header.

    Args:
        raw_bytes: Full Kafka value (5-byte header + payload)

    Returns:
        Schema ID (int) or None if invalid format
    """
    if not raw_bytes or len(raw_bytes) < 5:
        return None

    magic_byte = raw_bytes[0]
    if magic_byte != 0x00:
        return None

    try:
        schema_id = struct.unpack(">I", raw_bytes[1:5])[0]
        return int(schema_id)
    except Exception:
        return None
