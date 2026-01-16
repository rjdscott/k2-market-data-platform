"""End-to-End Data Pipeline Integration Tests.

Tests complete data flow:
Producer → Kafka → Consumer → Iceberg → Query Engine → API

All tests validate V2 schema data integrity, performance baselines,
and integration between all components in the K2 Market Data Platform.

Test Categories:
- test_basic_pipeline.py: Basic V2 data flow validation
- test_high_volume_pipeline.py: Performance under realistic load
- test_hybrid_pipeline.py: Hybrid query integration testing
- test_error_scenarios.py: Error handling and recovery validation
"""

from .test_basic_pipeline import *
from .test_high_volume_pipeline import *
from .test_hybrid_pipeline import *
from .test_error_scenarios import *
