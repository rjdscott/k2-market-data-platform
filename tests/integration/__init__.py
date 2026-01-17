"""Integration tests for K2 Market Data Platform.

This module provides comprehensive integration testing covering:
- End-to-end data pipeline validation (Kafka → Iceberg → API)
- External service integration (Kafka, Schema Registry, MinIO)
- Hybrid query functionality (Kafka + Iceberg)
- API integration with realistic data volumes
- Performance integration under realistic load

All tests use V2 schema only (industry-standard market data format).

Test Categories:
- test_end_to_end_pipeline.py: Complete data flow validation
- test_hybrid_query_integration.py: Hybrid query functionality
- test_kafka_integration.py: Kafka ecosystem integration
- test_iceberg_integration.py: Storage layer integration
- test_api_integration.py: API layer integration
- test_performance_integration.py: Performance under load
- test_fault_tolerance.py: Failure scenarios and recovery

Key Principles:
- Production-like data volumes and patterns
- Docker-based external services (Kafka, MinIO)
- Resource cleanup and isolation
- Comprehensive assertions and validation
"""
