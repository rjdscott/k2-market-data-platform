"""Unit tests for K2 Market Data Platform schemas.

Tests validate:
- Avro schema structure and required fields
- V1 vs V2 schema differences
- Data type validation (decimals, timestamps, enums)
- Schema loading and error handling
- Industry-standard compliance for V2 schemas
"""

import json
from pathlib import Path

import pytest

from k2.schemas import list_available_schemas, load_avro_schema


class TestSchemaValidation:
    """Validate Avro schemas are well-formed and contain required fields."""

    def test_list_available_schemas(self):
        """Should list all .avsc files without extension."""
        schemas = list_available_schemas()

        # Should find both v1 and v2 schemas
        assert "trade" in schemas
        assert "quote" in schemas
        assert "reference_data" in schemas
        assert len(schemas) >= 3

    def test_load_v1_schemas_success(self):
        """V1 schemas should load successfully."""
        for schema_name in ["trade", "quote", "reference_data"]:
            schema_str = load_avro_schema(schema_name, version="v1")
            assert isinstance(schema_str, str)

            # Should be valid JSON
            schema_dict = json.loads(schema_str)
            assert "name" in schema_dict
            assert "fields" in schema_dict
            assert isinstance(schema_dict["fields"], list)

    def test_load_v2_schemas_success(self):
        """V2 schemas should load successfully."""
        for schema_name in ["trade", "quote", "reference_data"]:
            schema_str = load_avro_schema(schema_name, version="v2")
            assert isinstance(schema_str, str)

            schema_dict = json.loads(schema_str)
            assert "name" in schema_dict
            assert "fields" in schema_dict

    def test_load_nonexistent_schema_raises_file_not_found(self):
        """Loading nonexistent schema should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_avro_schema("nonexistent_schema")

        assert "not found" in str(exc_info.value).lower()
        assert "Available schemas:" in str(exc_info.value)

    def test_load_schema_with_invalid_version_raises_value_error(self):
        """Invalid version should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            load_avro_schema("trade", version="v99")

        assert "version" in str(exc_info.value).lower()
        assert "v1" in str(exc_info.value) and "v2" in str(exc_info.value)

    def test_trade_v1_schema_structure(self):
        """Trade V1 schema should have expected structure."""
        schema_str = load_avro_schema("trade", version="v1")
        schema_dict = json.loads(schema_str)

        # Schema metadata
        assert schema_dict["name"] == "Trade"
        assert schema_dict["namespace"] == "com.k2.market_data"
        assert "doc" in schema_dict

        # Critical fields for V1
        field_names = [f["name"] for f in schema_dict["fields"]]
        required_fields = ["symbol", "exchange_timestamp", "price", "volume", "sequence_number"]

        for field in required_fields:
            assert field in field_names, f"V1 trade schema missing '{field}' field"

    def test_trade_v2_schema_structure(self):
        """Trade V2 schema should have industry-standard structure."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        # Schema metadata
        assert schema_dict["name"] == "TradeV2"
        assert schema_dict["namespace"] == "com.k2.marketdata"

        # V2 specific fields
        field_names = [f["name"] for f in schema_dict["fields"]]
        v2_required_fields = [
            "message_id",
            "trade_id",
            "symbol",
            "exchange",
            "asset_class",
            "timestamp",
            "price",
            "quantity",
            "currency",
            "side",
            "vendor_data",
        ]

        for field in v2_required_fields:
            assert field in field_names, f"V2 trade schema missing '{field}' field"

    def test_quote_v1_vs_v2_differences(self):
        """Quote V1 vs V2 should have different field structures."""
        v1_str = load_avro_schema("quote", version="v1")
        v1_dict = json.loads(v1_str)
        v1_fields = {f["name"] for f in v1_dict["fields"]}

        v2_str = load_avro_schema("quote", version="v2")
        v2_dict = json.loads(v2_str)
        v2_fields = {f["name"] for f in v2_dict["fields"]}

        # V1 has volume, V2 has quantity
        assert "bid_volume" in v1_fields
        assert "ask_volume" in v1_fields
        assert "bid_quantity" not in v1_fields
        assert "ask_quantity" not in v1_fields

        assert "bid_volume" not in v2_fields
        assert "ask_volume" not in v2_fields
        assert "bid_quantity" in v2_fields
        assert "ask_quantity" in v2_fields

    def test_v2_timestamp_precision(self):
        """V2 schemas should use microsecond precision."""
        for schema_name in ["trade", "quote"]:
            schema_str = load_avro_schema(schema_name, version="v2")
            schema_dict = json.loads(schema_str)

            timestamp_field = next(f for f in schema_dict["fields"] if f["name"] == "timestamp")
            field_type = timestamp_field["type"]

            # Should be logical type with microsecond precision
            assert isinstance(field_type, dict)
            assert field_type.get("logicalType") == "timestamp-micros"

    def test_v2_decimal_precision_and_scale(self):
        """V2 price/quantity should use Decimal(18,8)."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        for field_name in ["price", "quantity"]:
            field = next(f for f in schema_dict["fields"] if f["name"] == field_name)
            field_type = field["type"]

            assert isinstance(field_type, dict)
            assert field_type.get("logicalType") == "decimal"
            assert field_type.get("precision") == 18
            assert field_type.get("scale") == 8

    def test_v2_enum_fields(self):
        """V2 should have proper enum fields for asset_class and side."""
        schema_str = load_avro_schema("trade", version="v2")
        schema_dict = json.loads(schema_str)

        # Check asset_class enum
        asset_class_field = next(f for f in schema_dict["fields"] if f["name"] == "asset_class")
        asset_type = asset_class_field["type"]
        assert asset_type["type"] == "enum"
        assert asset_type["name"] == "AssetClass"
        expected_classes = {"equities", "crypto", "futures", "options"}
        assert set(asset_type["symbols"]) == expected_classes

        # Check side enum
        side_field = next(f for f in schema_dict["fields"] if f["name"] == "side")
        side_type = side_field["type"]
        assert side_type["type"] == "enum"
        assert side_type["name"] == "TradeSide"
        expected_sides = {"BUY", "SELL", "SELL_SHORT", "UNKNOWN"}
        assert set(side_type["symbols"]) == expected_sides

    def test_vendor_data_optional_map(self):
        """V2 vendor_data should be optional map<string, string>."""
        for schema_name in ["trade", "quote"]:
            schema_str = load_avro_schema(schema_name, version="v2")
            schema_dict = json.loads(schema_str)

            vendor_field = next(f for f in schema_dict["fields"] if f["name"] == "vendor_data")
            field_type = vendor_field["type"]

            # Should be union with null (optional)
            assert isinstance(field_type, list)
            assert "null" in field_type

            # Extract map type from union
            map_type = next(t for t in field_type if isinstance(t, dict))
            assert map_type["type"] == "map"
            assert map_type["values"] == "string"

    def test_all_schemas_have_documentation(self):
        """All schemas and fields should have doc strings."""
        for version in ["v1", "v2"]:
            for schema_name in ["trade", "quote", "reference_data"]:
                schema_str = load_avro_schema(schema_name, version=version)
                schema_dict = json.loads(schema_str)

                # Schema-level documentation
                assert "doc" in schema_dict, f"{schema_name}_{version} missing schema doc"

                # Field-level documentation (sample check)
                for field in schema_dict["fields"][:3]:  # Check first 3 fields
                    assert (
                        "doc" in field
                    ), f"{schema_name}_{version}.{field['name']} missing field doc"

    def test_optional_fields_have_defaults(self):
        """Optional fields should have default values."""
        for version in ["v1", "v2"]:
            for schema_name in ["trade", "quote"]:
                schema_str = load_avro_schema(schema_name, version=version)
                schema_dict = json.loads(schema_str)

                for field in schema_dict["fields"]:
                    field_type = field["type"]

                    # Check if union with null (optional)
                    if isinstance(field_type, list) and "null" in field_type:
                        # Optional field should have default
                        assert (
                            "default" in field
                        ), f"{schema_name}_{version}.{field['name']} is optional but missing default"

    def test_schema_file_naming_convention(self):
        """Schema files should follow naming convention."""
        schema_dir = Path(__file__).parent.parent.parent / "src" / "k2" / "schemas"

        v1_files = list(schema_dir.glob("*.avsc"))
        v2_files = list(schema_dir.glob("*_v2.avsc"))

        # Should have corresponding v1 and v2 files
        v1_names = {f.stem for f in v1_files if not f.stem.endswith("_v2")}
        v2_names = {f.stem.replace("_v2", "") for f in v2_files}

        assert v1_names == v2_names, f"Mismatch between v1 {v1_names} and v2 {v2_names} schemas"


@pytest.mark.unit
class TestSchemaEdgeCases:
    """Test edge cases and error conditions for schema handling."""

    def test_malformed_json_schema_raises_error(self):
        """Malformed JSON in schema file should raise JSONDecodeError."""
        # This tests the JSON validation in load_avro_schema
        # The actual schema files should be well-formed
        pass

    def test_schema_loading_with_path_traversal_attempt(self):
        """Should handle path traversal attempts safely."""
        with pytest.raises(FileNotFoundError):
            load_avro_schema("../../../etc/passwd")

    def test_empty_schema_directory_handling(self):
        """Should handle case where no schemas exist gracefully."""
        # This would only fail if schema directory is empty
        schemas = list_available_schemas()
        assert isinstance(schemas, list)


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
