"""Unit tests for Avro schema validation.

Tests ensure schemas are well-formed and contain required fields for
ordering, partitioning, and data quality tracking.
"""

import json

import avro.schema
import pytest

from k2.schemas import list_available_schemas, load_avro_schema


@pytest.mark.unit
class TestSchemas:
    """Validate Avro schemas are well-formed and contain required fields."""

    def test_trade_schema_valid(self):
        """Trade schema should parse without errors."""
        schema_str = load_avro_schema("trade")
        schema_dict = json.loads(schema_str)

        # Parse with Avro library
        schema = avro.schema.parse(schema_str)

        assert schema.name == "Trade"
        assert schema.namespace == "com.k2.market_data"

        # Check critical fields exist
        field_names = [f.name for f in schema.fields]
        assert "symbol" in field_names
        assert "exchange_timestamp" in field_names
        assert "price" in field_names
        assert "volume" in field_names

    def test_quote_schema_valid(self):
        """Quote schema should parse without errors."""
        schema_str = load_avro_schema("quote")
        schema_dict = json.loads(schema_str)

        schema = avro.schema.parse(schema_str)

        assert schema.name == "Quote"
        assert schema.namespace == "com.k2.market_data"

        field_names = [f.name for f in schema.fields]
        assert "symbol" in field_names
        assert "bid_price" in field_names
        assert "ask_price" in field_names

    def test_reference_data_schema_valid(self):
        """Reference data schema should parse without errors."""
        schema_str = load_avro_schema("reference_data")

        schema = avro.schema.parse(schema_str)

        assert schema.name == "ReferenceData"

        field_names = [f.name for f in schema.fields]
        assert "company_id" in field_names
        assert "symbol" in field_names
        assert "isin" in field_names

    def test_all_schemas_have_required_fields(self):
        """All market data schemas need key ordering fields."""
        for schema_name in ["trade", "quote"]:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)
            field_names = [f["name"] for f in schema_dict["fields"]]

            # Required for ordering and partitioning
            assert "symbol" in field_names, f"{schema_name} schema missing 'symbol' field"
            assert (
                "exchange_timestamp" in field_names
            ), f"{schema_name} schema missing 'exchange_timestamp' field"
            assert (
                "ingestion_timestamp" in field_names
            ), f"{schema_name} schema missing 'ingestion_timestamp' field"
            assert (
                "sequence_number" in field_names
            ), f"{schema_name} schema missing 'sequence_number' field"

    def test_timestamp_fields_use_logical_type(self):
        """Timestamp fields should use timestamp-millis logical type."""
        for schema_name in ["trade", "quote"]:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            # Find timestamp fields
            for field in schema_dict["fields"]:
                if "timestamp" in field["name"]:
                    field_type = field["type"]

                    # Handle union types (e.g., ["null", "long"])
                    if isinstance(field_type, list):
                        # Skip - sequence_number can be null
                        continue

                    if isinstance(field_type, dict):
                        assert (
                            field_type.get("logicalType") == "timestamp-millis"
                        ), f"{schema_name}.{field['name']} should use timestamp-millis"

    def test_decimal_fields_use_logical_type(self):
        """Price fields should use decimal logical type (not float)."""
        for schema_name in ["trade", "quote"]:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            # Find price fields
            for field in schema_dict["fields"]:
                if "price" in field["name"]:
                    field_type = field["type"]

                    assert isinstance(
                        field_type, dict,
                    ), f"{schema_name}.{field['name']} should be dict type"
                    assert (
                        field_type.get("logicalType") == "decimal"
                    ), f"{schema_name}.{field['name']} should use decimal type"
                    assert (
                        field_type.get("precision") == 18
                    ), f"{schema_name}.{field['name']} should have precision=18"
                    assert (
                        field_type.get("scale") == 6
                    ), f"{schema_name}.{field['name']} should have scale=6"

    def test_list_available_schemas(self):
        """Should list all .avsc files."""
        schemas = list_available_schemas()

        assert "trade" in schemas
        assert "quote" in schemas
        assert "reference_data" in schemas
        assert len(schemas) >= 3

    def test_load_nonexistent_schema_raises_error(self):
        """Loading nonexistent schema should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_avro_schema("nonexistent_schema")

        assert "not found" in str(exc_info.value).lower()

    def test_schema_files_have_doc_strings(self):
        """All schemas and fields should have doc strings."""
        for schema_name in ["trade", "quote", "reference_data"]:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            # Schema-level doc
            assert "doc" in schema_dict, f"{schema_name} schema missing doc string"

            # Field-level docs
            for field in schema_dict["fields"]:
                assert "doc" in field, f"{schema_name}.{field['name']} missing doc string"

    def test_optional_fields_have_defaults(self):
        """Optional (nullable) fields should have default values."""
        for schema_name in ["trade", "quote", "reference_data"]:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            for field in schema_dict["fields"]:
                field_type = field["type"]

                # Check if union with null
                if isinstance(field_type, list) and "null" in field_type:
                    # Optional field should have default
                    assert (
                        "default" in field
                    ), f"{schema_name}.{field['name']} is optional but has no default"


@pytest.mark.unit
class TestSchemasV2:
    """Validate v2 Avro schemas with industry-standard fields."""

    def test_trade_v2_schema_valid(self):
        """Trade v2 schema should parse without errors."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        # Parse with Avro library
        schema = avro.schema.parse(schema_str)

        assert schema.name == "TradeV2"
        assert schema.namespace == "com.k2.marketdata"

        # Check v2 fields exist
        field_names = [f.name for f in schema.fields]
        assert "message_id" in field_names
        assert "trade_id" in field_names
        assert "symbol" in field_names
        assert "exchange" in field_names
        assert "asset_class" in field_names
        assert "timestamp" in field_names  # v2: not exchange_timestamp
        assert "price" in field_names
        assert "quantity" in field_names  # v2: not volume
        assert "currency" in field_names
        assert "side" in field_names
        assert "vendor_data" in field_names

    def test_quote_v2_schema_valid(self):
        """Quote v2 schema should parse without errors."""
        schema_str = load_avro_schema("quote", version="v2")
        schema_dict = json.loads(schema_str)

        schema = avro.schema.parse(schema_str)

        assert schema.name == "QuoteV2"
        assert schema.namespace == "com.k2.marketdata"

        field_names = [f.name for f in schema.fields]
        assert "message_id" in field_names
        assert "quote_id" in field_names
        assert "symbol" in field_names
        assert "asset_class" in field_names
        assert "timestamp" in field_names
        assert "bid_price" in field_names
        assert "bid_quantity" in field_names  # v2: not bid_volume
        assert "ask_quantity" in field_names  # v2: not ask_volume
        assert "currency" in field_names
        assert "vendor_data" in field_names

    def test_v2_timestamp_uses_microseconds(self):
        """V2 schemas should use timestamp-micros (not millis)."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        # Find timestamp field
        timestamp_field = next(f for f in schema_dict["fields"] if f["name"] == "timestamp")
        field_type = timestamp_field["type"]

        assert isinstance(field_type, dict)
        assert field_type.get("logicalType") == "timestamp-micros"

    def test_v2_decimal_precision_18_8(self):
        """V2 price/quantity fields should use Decimal(18,8) not (18,6)."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        # Check price and quantity fields
        for field_name in ["price", "quantity"]:
            field = next(f for f in schema_dict["fields"] if f["name"] == field_name)
            field_type = field["type"]

            assert isinstance(field_type, dict), f"{field_name} should be dict type"
            assert field_type.get("logicalType") == "decimal"
            assert field_type.get("precision") == 18
            assert field_type.get("scale") == 8, f"v2 {field_name} should have scale=8"

    def test_v2_enum_fields(self):
        """V2 schemas should have enum fields for asset_class and side."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        # Check asset_class enum
        asset_class_field = next(f for f in schema_dict["fields"] if f["name"] == "asset_class")
        assert asset_class_field["type"]["type"] == "enum"
        assert asset_class_field["type"]["name"] == "AssetClass"
        assert set(asset_class_field["type"]["symbols"]) == {"equities", "crypto", "futures", "options"}

        # Check side enum
        side_field = next(f for f in schema_dict["fields"] if f["name"] == "side")
        assert side_field["type"]["type"] == "enum"
        assert side_field["type"]["name"] == "TradeSide"
        assert set(side_field["type"]["symbols"]) == {"BUY", "SELL", "SELL_SHORT", "UNKNOWN"}

    def test_v2_vendor_data_is_map(self):
        """V2 vendor_data should be optional map<string, string>."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        vendor_data_field = next(f for f in schema_dict["fields"] if f["name"] == "vendor_data")
        field_type = vendor_data_field["type"]

        # Should be union with null (optional)
        assert isinstance(field_type, list)
        assert "null" in field_type

        # Extract map type
        map_type = next(t for t in field_type if isinstance(t, dict))
        assert map_type["type"] == "map"
        assert map_type["values"] == "string"

    def test_load_schema_with_invalid_version(self):
        """Loading schema with invalid version should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            load_avro_schema("trade", version="v99")

        assert "version" in str(exc_info.value).lower()

    def test_v2_schemas_have_doc_strings(self):
        """V2 schemas and fields should have doc strings."""
        for schema_name in ["trade", "quote"]:
            schema_str = load_avro_schema(schema_name, version="v2")
            schema_dict = json.loads(schema_str)

            # Schema-level doc
            assert "doc" in schema_dict, f"{schema_name}_v2 schema missing doc string"

            # Field-level docs (check key fields)
            key_fields = ["message_id", "symbol", "timestamp", "vendor_data"]
            for field_name in key_fields:
                field = next((f for f in schema_dict["fields"] if f["name"] == field_name), None)
                if field:
                    assert "doc" in field, f"{schema_name}_v2.{field_name} missing doc string"
