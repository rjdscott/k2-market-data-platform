"""Raw Exchange-Specific to V2 Transformation UDFs.

This module provides Spark UDFs to transform raw exchange-specific trade data
into the unified V2 schema. Implements the Medallion Architecture pattern:

Bronze Layer (Raw):
- Stores immutable raw bytes from exchange (Binance/Kraken specific schemas)
- Preserves original data for replay and audit

Silver Layer (V2 Unified):
- Transforms exchange-specific formats to unified V2 schema
- Validates data quality
- Routes invalid records to DLQ

Industry Best Practice (Netflix, Uber, Databricks):
- Keep Bronze as close to source as possible
- Transform in Silver layer for business use

Usage:
    from k2.spark.udfs.raw_to_v2_transformation import (
        deserialize_binance_raw_to_v2,
        deserialize_kraken_raw_to_v2
    )

    # Binance transformation
    df = bronze_df.withColumn("trade", deserialize_binance_raw_to_v2(col("raw_bytes")))

    # Kraken transformation
    df = bronze_df.withColumn("trade", deserialize_kraken_raw_to_v2(col("raw_bytes")))
"""

import struct
import json
import uuid
from decimal import Decimal
from typing import Optional
from io import BytesIO

from pyspark.sql.functions import udf
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    DecimalType,
    ArrayType,
    IntegerType,
)


# V2 Unified Trade Schema (Target Schema for Silver Layer)
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


def extract_schema_id(raw_bytes: bytes) -> int:
    """Extract Schema Registry schema ID from raw bytes header.

    Args:
        raw_bytes: Full Kafka value (5-byte header + Avro payload)

    Returns:
        Schema ID as integer

    Raises:
        ValueError: If magic byte invalid or bytes too short
    """
    if not raw_bytes or len(raw_bytes) < 5:
        raise ValueError(f"raw_bytes too short: {len(raw_bytes) if raw_bytes else 0} bytes (need ≥5)")

    magic_byte = raw_bytes[0]
    if magic_byte != 0x00:
        raise ValueError(f"Invalid magic byte: {hex(magic_byte)} (expected 0x00)")

    schema_id = struct.unpack(">I", raw_bytes[1:5])[0]  # Big-endian int32
    return schema_id


def get_avro_payload(raw_bytes: bytes) -> bytes:
    """Strip Schema Registry 5-byte header and return Avro payload.

    Args:
        raw_bytes: Full Kafka value (5-byte header + Avro payload)

    Returns:
        Avro payload bytes (without header)
    """
    if not raw_bytes or len(raw_bytes) < 6:
        raise ValueError(f"raw_bytes too short: {len(raw_bytes) if raw_bytes else 0} bytes (need ≥6)")

    # Validate magic byte
    extract_schema_id(raw_bytes)  # Will raise if invalid

    return raw_bytes[5:]  # Return payload after 5-byte header


@udf(returnType=TRADE_V2_SCHEMA)
def deserialize_binance_raw_to_v2(raw_bytes: bytes) -> Optional[tuple]:
    """Deserialize Binance raw trade and transform to V2 unified schema.

    Binance Raw Schema:
        - event_type: string
        - event_time_ms: long
        - symbol: string
        - trade_id: long
        - price: string
        - quantity: string
        - trade_time_ms: long
        - is_buyer_maker: boolean
        - is_best_match: boolean (nullable)
        - ingestion_timestamp: long

    V2 Unified Schema Mapping:
        - message_id: Generated UUID (deduplication)
        - trade_id: "BINANCE-{trade_id}"
        - symbol: symbol (BTCUSDT)
        - exchange: "BINANCE"
        - asset_class: "crypto"
        - timestamp: trade_time_ms * 1000 (convert ms → microseconds)
        - price: Decimal(price)
        - quantity: Decimal(quantity)
        - currency: Extracted from symbol (USDT from BTCUSDT)
        - side: "SELL" if is_buyer_maker else "BUY" (buyer maker = aggressor sold)
        - trade_conditions: [] (empty for crypto)
        - source_sequence: None (Binance doesn't provide)
        - ingestion_timestamp: ingestion_timestamp
        - platform_sequence: None
        - vendor_data: JSON with Binance-specific fields

    Args:
        raw_bytes: Full Kafka value (5-byte Schema Registry header + Avro payload)

    Returns:
        Tuple matching TRADE_V2_SCHEMA or None on failure

    Raises:
        ValueError: On deserialization failure (caught by validation logic)
    """
    try:
        import avro.schema
        import avro.io

        # Extract schema ID and payload
        schema_id = extract_schema_id(raw_bytes)
        avro_payload = get_avro_payload(raw_bytes)

        # Deserialize Avro (Binance raw schema)
        # Schema is hardcoded since we know the structure
        # In production, fetch from Schema Registry
        bytes_reader = BytesIO(avro_payload)
        decoder = avro.io.BinaryDecoder(bytes_reader)

        # For now, use raw reader (schema-less)
        # TODO: Fetch schema from registry for proper validation
        from fastavro import schemaless_reader

        # Read using fastavro (faster than avro-python3)
        record = schemaless_reader(bytes_reader, None)  # Read without schema validation

        # Transform Binance raw → V2
        message_id = str(uuid.uuid4())
        trade_id = f"BINANCE-{record['trade_id']}"
        symbol = record["symbol"]
        exchange = "BINANCE"
        asset_class = "crypto"
        timestamp_us = record["trade_time_ms"] * 1000  # ms → microseconds
        price = Decimal(record["price"])
        quantity = Decimal(record["quantity"])

        # Extract currency from symbol (last 3-4 chars typically)
        # BTCUSDT → USDT, ETHUSDT → USDT, BTCUSD → USD
        if symbol.endswith("USDT"):
            currency = "USDT"
        elif symbol.endswith("USD"):
            currency = "USD"
        elif symbol.endswith("BTC"):
            currency = "BTC"
        elif symbol.endswith("ETH"):
            currency = "ETH"
        else:
            currency = symbol[-4:]  # Last 4 chars as fallback

        # Side: is_buyer_maker = true → aggressor was seller → SELL
        #       is_buyer_maker = false → aggressor was buyer → BUY
        side = "SELL" if record["is_buyer_maker"] else "BUY"

        trade_conditions = []  # Empty for crypto
        source_sequence = None
        ingestion_timestamp_us = record["ingestion_timestamp"]
        platform_sequence = None

        # Vendor data (Binance-specific fields)
        vendor_data_dict = {
            "event_type": record["event_type"],
            "event_time_ms": record["event_time_ms"],
            "is_buyer_maker": record["is_buyer_maker"],
            "is_best_match": record.get("is_best_match"),
        }
        vendor_data_json = json.dumps(vendor_data_dict)

        return (
            message_id,
            trade_id,
            symbol,
            exchange,
            asset_class,
            timestamp_us,
            price,
            quantity,
            currency,
            side,
            trade_conditions,
            source_sequence,
            ingestion_timestamp_us,
            platform_sequence,
            vendor_data_json,
            schema_id,
        )

    except Exception as e:
        raise ValueError(f"Binance raw deserialization failed: {e}")


@udf(returnType=TRADE_V2_SCHEMA)
def deserialize_kraken_raw_to_v2(raw_bytes: bytes) -> Optional[tuple]:
    """Deserialize Kraken raw trade and transform to V2 unified schema.

    Kraken Raw Schema:
        - channel_id: long
        - price: string
        - volume: string
        - timestamp: string (seconds.microseconds)
        - side: string ('b' or 's')
        - order_type: string ('l' or 'm')
        - misc: string
        - pair: string (XBT/USD)
        - ingestion_timestamp: long

    V2 Unified Schema Mapping:
        - message_id: Generated UUID
        - trade_id: "KRAKEN-{timestamp}-{hash}" (Kraken doesn't provide trade ID)
        - symbol: Normalized pair (XBT/USD → BTCUSD, ETH/USD → ETHUSD)
        - exchange: "KRAKEN"
        - asset_class: "crypto"
        - timestamp: Parse from timestamp string → microseconds
        - price: Decimal(price)
        - quantity: Decimal(volume)
        - currency: Extracted from pair (USD from XBT/USD)
        - side: "BUY" if side == 'b' else "SELL"
        - trade_conditions: [order_type] (limit/market)
        - source_sequence: None
        - ingestion_timestamp: ingestion_timestamp
        - platform_sequence: None
        - vendor_data: JSON with Kraken-specific fields

    Args:
        raw_bytes: Full Kafka value (5-byte Schema Registry header + Avro payload)

    Returns:
        Tuple matching TRADE_V2_SCHEMA or None on failure

    Raises:
        ValueError: On deserialization failure
    """
    try:
        import avro.schema
        import avro.io
        import hashlib

        # Extract schema ID and payload
        schema_id = extract_schema_id(raw_bytes)
        avro_payload = get_avro_payload(raw_bytes)

        # Deserialize Avro (Kraken raw schema)
        bytes_reader = BytesIO(avro_payload)

        # Read using fastavro
        from fastavro import schemaless_reader

        record = schemaless_reader(bytes_reader, None)

        # Transform Kraken raw → V2
        message_id = str(uuid.uuid4())

        # Generate trade_id (Kraken doesn't provide one)
        timestamp_str = record["timestamp"]
        pair = record["pair"]
        trade_id_hash = hashlib.md5(f"{timestamp_str}:{pair}:{record['price']}".encode()).hexdigest()[:8]
        trade_id = f"KRAKEN-{timestamp_str}-{trade_id_hash}"

        # Normalize symbol: XBT/USD → BTCUSD, ETH/USD → ETHUSD
        pair_normalized = pair.replace("XBT", "BTC").replace("/", "")
        symbol = pair_normalized

        exchange = "KRAKEN"
        asset_class = "crypto"

        # Parse timestamp: "1705584123.123456" → microseconds
        timestamp_float = float(timestamp_str)
        timestamp_us = int(timestamp_float * 1_000_000)

        price = Decimal(record["price"])
        quantity = Decimal(record["volume"])

        # Extract currency from pair (after /)
        if "/" in pair:
            currency = pair.split("/")[1].replace("XBT", "BTC")
        else:
            currency = "USD"  # Fallback

        # Side: 'b' = buy, 's' = sell
        side = "BUY" if record["side"] == "b" else "SELL"

        # Trade conditions: order type
        order_type_map = {"l": "limit", "m": "market"}
        trade_conditions = [order_type_map.get(record["order_type"], record["order_type"])]

        source_sequence = None
        ingestion_timestamp_us = record["ingestion_timestamp"]
        platform_sequence = None

        # Vendor data (Kraken-specific fields)
        vendor_data_dict = {
            "channel_id": record["channel_id"],
            "order_type": record["order_type"],
            "misc": record["misc"],
            "pair": pair,  # Original pair (XBT/USD)
        }
        vendor_data_json = json.dumps(vendor_data_dict)

        return (
            message_id,
            trade_id,
            symbol,
            exchange,
            asset_class,
            timestamp_us,
            price,
            quantity,
            currency,
            side,
            trade_conditions,
            source_sequence,
            ingestion_timestamp_us,
            platform_sequence,
            vendor_data_json,
            schema_id,
        )

    except Exception as e:
        raise ValueError(f"Kraken raw deserialization failed: {e}")


# Helper UDF to extract schema ID without full deserialization (for DLQ)
@udf(returnType=IntegerType())
def extract_schema_id_udf(raw_bytes: bytes) -> Optional[int]:
    """Extract Schema Registry schema ID from raw bytes (for DLQ tracking).

    Args:
        raw_bytes: Full Kafka value (5-byte header + payload)

    Returns:
        Schema ID or None if invalid
    """
    try:
        return extract_schema_id(raw_bytes)
    except Exception:
        return None
