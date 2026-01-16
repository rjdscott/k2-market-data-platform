"""End-to-End Data Pipeline Integration Tests.

Tests complete data flow:
Producer → Kafka → Consumer → Iceberg → Query Engine → API

All tests validate V2 schema data integrity, performance baselines,
and integration between all components in the K2 Market Data Platform.

Test Categories:
- test_basic_pipeline.py: Basic V2 data flow validation
- test_hybrid_query_integration.py: Hybrid query integration testing
"""

# Import actual test modules that exist
