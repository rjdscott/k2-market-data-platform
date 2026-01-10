"""Unit tests for Avro schema validation.

Tests ensure schemas are well-formed and contain required fields for
ordering, partitioning, and data quality tracking.
"""
import pytest
from pathlib import Path
import json
import avro.schema
from k2.schemas import load_avro_schema, list_available_schemas


@pytest.mark.unit
class TestSchemas:
    """Validate Avro schemas are well-formed and contain required fields."""

    def test_trade_schema_valid(self):
        """Trade schema should parse without errors."""
        schema_str = load_avro_schema('trade')
        schema_dict = json.loads(schema_str)

        # Parse with Avro library
        schema = avro.schema.parse(schema_str)

        assert schema.name == 'Trade'
        assert schema.namespace == 'com.k2.market_data'

        # Check critical fields exist
        field_names = [f.name for f in schema.fields]
        assert 'symbol' in field_names
        assert 'exchange_timestamp' in field_names
        assert 'price' in field_names
        assert 'volume' in field_names

    def test_quote_schema_valid(self):
        """Quote schema should parse without errors."""
        schema_str = load_avro_schema('quote')
        schema_dict = json.loads(schema_str)

        schema = avro.schema.parse(schema_str)

        assert schema.name == 'Quote'
        assert schema.namespace == 'com.k2.market_data'

        field_names = [f.name for f in schema.fields]
        assert 'symbol' in field_names
        assert 'bid_price' in field_names
        assert 'ask_price' in field_names

    def test_reference_data_schema_valid(self):
        """Reference data schema should parse without errors."""
        schema_str = load_avro_schema('reference_data')

        schema = avro.schema.parse(schema_str)

        assert schema.name == 'ReferenceData'

        field_names = [f.name for f in schema.fields]
        assert 'company_id' in field_names
        assert 'symbol' in field_names
        assert 'isin' in field_names

    def test_all_schemas_have_required_fields(self):
        """All market data schemas need key ordering fields."""
        for schema_name in ['trade', 'quote']:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)
            field_names = [f['name'] for f in schema_dict['fields']]

            # Required for ordering and partitioning
            assert 'symbol' in field_names, \
                f"{schema_name} schema missing 'symbol' field"
            assert 'exchange_timestamp' in field_names, \
                f"{schema_name} schema missing 'exchange_timestamp' field"
            assert 'ingestion_timestamp' in field_names, \
                f"{schema_name} schema missing 'ingestion_timestamp' field"
            assert 'sequence_number' in field_names, \
                f"{schema_name} schema missing 'sequence_number' field"

    def test_timestamp_fields_use_logical_type(self):
        """Timestamp fields should use timestamp-millis logical type."""
        for schema_name in ['trade', 'quote']:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            # Find timestamp fields
            for field in schema_dict['fields']:
                if 'timestamp' in field['name']:
                    field_type = field['type']

                    # Handle union types (e.g., ["null", "long"])
                    if isinstance(field_type, list):
                        # Skip - sequence_number can be null
                        continue

                    if isinstance(field_type, dict):
                        assert field_type.get('logicalType') == 'timestamp-millis', \
                            f"{schema_name}.{field['name']} should use timestamp-millis"

    def test_decimal_fields_use_logical_type(self):
        """Price fields should use decimal logical type (not float)."""
        for schema_name in ['trade', 'quote']:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            # Find price fields
            for field in schema_dict['fields']:
                if 'price' in field['name']:
                    field_type = field['type']

                    assert isinstance(field_type, dict), \
                        f"{schema_name}.{field['name']} should be dict type"
                    assert field_type.get('logicalType') == 'decimal', \
                        f"{schema_name}.{field['name']} should use decimal type"
                    assert field_type.get('precision') == 18, \
                        f"{schema_name}.{field['name']} should have precision=18"
                    assert field_type.get('scale') == 6, \
                        f"{schema_name}.{field['name']} should have scale=6"

    def test_list_available_schemas(self):
        """Should list all .avsc files."""
        schemas = list_available_schemas()

        assert 'trade' in schemas
        assert 'quote' in schemas
        assert 'reference_data' in schemas
        assert len(schemas) >= 3

    def test_load_nonexistent_schema_raises_error(self):
        """Loading nonexistent schema should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_avro_schema('nonexistent_schema')

        assert 'not found' in str(exc_info.value).lower()

    def test_schema_files_have_doc_strings(self):
        """All schemas and fields should have doc strings."""
        for schema_name in ['trade', 'quote', 'reference_data']:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            # Schema-level doc
            assert 'doc' in schema_dict, \
                f"{schema_name} schema missing doc string"

            # Field-level docs
            for field in schema_dict['fields']:
                assert 'doc' in field, \
                    f"{schema_name}.{field['name']} missing doc string"

    def test_optional_fields_have_defaults(self):
        """Optional (nullable) fields should have default values."""
        for schema_name in ['trade', 'quote', 'reference_data']:
            schema_str = load_avro_schema(schema_name)
            schema_dict = json.loads(schema_str)

            for field in schema_dict['fields']:
                field_type = field['type']

                # Check if union with null
                if isinstance(field_type, list) and 'null' in field_type:
                    # Optional field should have default
                    assert 'default' in field, \
                        f"{schema_name}.{field['name']} is optional but has no default"
