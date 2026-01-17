# Step 02: Complete Data Pipeline Validation

**Duration**: 6 hours
**Status**: ⬜ Not Started (Documentation Complete)
**Priority**: High

---

## Objectives

Implement comprehensive tests that validate the complete data flow from Binance WebSocket through the FastAPI service, ensuring data consistency and schema compliance across all pipeline stages.

---

## Detailed Tasks

### Task 2.1: Core Pipeline Test Structure (90 minutes)

**File**: `tests/e2e/test_complete_pipeline.py`

**Test Class Structure**:
```python
import asyncio
import pytest
from typing import Dict, List, Any
from datetime import datetime, timedelta

from ..utils.docker_manager import E2EDockerManager
from ..utils.data_validator import DataValidator

class TestCompleteDataPipeline:
    """Comprehensive end-to-end data pipeline validation."""
    
    @pytest.mark.e2e
    @pytest.mark.slow
    async def test_binance_to_api_pipeline(self, minimal_stack, api_client):
        """Test complete Binance → Kafka → Consumer → Iceberg → API flow."""
        
    @pytest.mark.e2e
    async def test_pipeline_data_consistency(self, minimal_stack, api_client):
        """Validate data consistency across all pipeline stages."""
        
    @pytest.mark.e2e
    async def test_pipeline_schema_compliance(self, minimal_stack, api_client):
        """Validate V2 schema compliance across all stages."""
        
    @pytest.mark.e2e
    async def test_pipeline_error_handling(self, minimal_stack):
        """Test error handling and recovery scenarios."""
        
    @pytest.mark.e2e
    async def test_pipeline_performance_under_load(self, minimal_stack, api_client):
        """Test pipeline performance with realistic data volumes."""
```

### Task 2.2: Binance WebSocket Integration (90 minutes)

**Core Pipeline Test Implementation**:
```python
async def test_binance_to_api_pipeline(self, minimal_stack: Dict[str, str], api_client: httpx.AsyncClient) -> None:
    """Complete pipeline validation with real-time data."""
    
    # 1. Start minimal Docker stack (already done by fixture)
    # 2. Initialize infrastructure (schemas, topics, tables)
    await self._initialize_test_infrastructure(minimal_stack)
    
    # 3. Start Binance stream for controlled duration
    binance_container = minimal_stack.get("binance-stream")
    await self._start_binance_stream(binance_container, duration_seconds=60)
    
    # 4. Start consumer service
    consumer_container = minimal_stack.get("consumer-crypto")
    await self._start_consumer_service(consumer_container)
    
    # 5. Wait for data accumulation
    await asyncio.sleep(30)  # Allow data processing
    
    # 6. Stop data collection
    await self._stop_binance_stream(binance_container)
    await self._stop_consumer_service(consumer_container)
    
    # 7. Query API for validation
    api_response = await api_client.get("/v1/trades?table_type=TRADES&limit=100")
    assert api_response.status_code == 200
    
    # 8. Validate data consistency and accuracy
    await self._validate_pipeline_consistency(minimal_stack, api_response.json())
    
    # 9. Verify performance metrics
    await self._validate_performance_metrics(minimal_stack)

async def _initialize_test_infrastructure(self, stack: Dict[str, str]) -> None:
    """Initialize schemas, topics, and tables for testing."""
    # Register V2 schemas in Schema Registry
    # Create Kafka topics for crypto trades
    # Initialize Iceberg tables for trades
    
async def _start_binance_stream(self, container: str, duration_seconds: int) -> None:
    """Start Binance stream for specified duration."""
    # Execute in container with environment variables
    # Monitor stream health and data reception
    
async def _start_consumer_service(self, container: str) -> None:
    """Start consumer service for processing."""
    # Start consumer with proper configuration
    # Monitor for successful processing
```

### Task 2.3: Data Consistency Validation (90 minutes)

**Consistency Validation Framework**:
```python
class DataValidator:
    """Validate data consistency across pipeline stages."""
    
    async def validate_data_consistency(self, stack: Dict[str, str]) -> Dict[str, int]:
        """Validate data counts across all pipeline stages."""
        
        # Count messages in Kafka topic
        kafka_count = await self._count_kafka_messages(stack)
        
        # Count records in Iceberg table
        iceberg_count = await self._count_iceberg_records(stack)
        
        # Count records returned by API
        api_count = await self._count_api_records(stack)
        
        # Verify all counts match (exactly-once processing)
        consistency_results = {
            "kafka_count": kafka_count,
            "iceberg_count": iceberg_count,
            "api_count": api_count,
            "consistent": kafka_count == iceberg_count == api_count
        }
        
        return consistency_results
        
    async def validate_schema_compliance(self, stack: Dict[str, str]) -> Dict[str, bool]:
        """Validate V2 schema compliance at each stage."""
        
        # Validate Kafka message schemas
        kafka_compliance = await self._validate_kafka_schemas(stack)
        
        # Validate Iceberg table schemas
        iceberg_compliance = await self._validate_iceberg_schemas(stack)
        
        # Validate API response schemas
        api_compliance = await self._validate_api_schemas(stack)
        
        return {
            "kafka_compliance": kafka_compliance,
            "iceberg_compliance": iceberg_compliance,
            "api_compliance": api_compliance
        }
        
    async def validate_timestamp_consistency(self, stack: Dict[str, str]) -> Dict[str, Any]:
        """Validate timestamp handling and ordering."""
        
        # Check timestamp format consistency
        # Verify chronological ordering where appropriate
        # Validate timezone handling
        return {
            "format_consistent": True,
            "ordering_correct": True,
            "timezone_handling": True
        }
```

**Consistency Checks**:
- **Count Validation**: Kafka count = Iceberg count = API count
- **Schema Validation**: V2 schema compliance at all stages
- **Timestamp Validation**: Proper timestamp handling and ordering
- **Data Quality**: No duplicates, no missing records, proper data types

### Task 2.4: Error Handling and Recovery (90 minutes)

**Error Scenarios**:
```python
@pytest.mark.e2e
async def test_pipeline_error_recovery(self, minimal_stack: Dict[str, str]) -> None:
    """Test pipeline resilience to failures."""
    
    # Scenario 1: Kafka broker restart during data flow
    await self._test_kafka_restart_recovery(minimal_stack)
    
    # Scenario 2: Schema Registry unavailability
    await self._test_schema_registry_failure(minimal_stack)
    
    # Scenario 3: MinIO temporary failure
    await self._test_minio_failure_recovery(minimal_stack)
    
    # Scenario 4: Consumer service crash and recovery
    await self._test_consumer_crash_recovery(minimal_stack)

async def _test_kafka_restart_recovery(self, stack: Dict[str, str]) -> None:
    """Test pipeline recovery from Kafka broker restart."""
    # Start data flow
    # Restart Kafka broker
    # Verify connection recovery
    # Check for data loss
    
async def _test_schema_registry_failure(self, stack: Dict[str, str]) -> None:
    """Test handling of Schema Registry unavailability."""
    # Stop Schema Registry
    # Continue data ingestion (should buffer/fail gracefully)
    # Restart Schema Registry
    # Verify recovery and data consistency
```

---

## Success Criteria

### Must-Have Requirements
- ✅ Complete pipeline test implemented and passing
- ✅ Data consistency validation across all stages
- ✅ Error handling scenarios tested
- ✅ Schema compliance verification
- ✅ Performance within acceptable limits

### Nice-to-Have Requirements
- ✅ Advanced error scenarios (network partitions, partial failures)
- ✅ Performance benchmarking and baseline establishment
- ✅ Resource usage monitoring during pipeline operation

---

## Testing the Implementation

### Validation Steps
1. **Manual Pipeline Test**: Run pipeline manually to understand behavior
2. **Automated Test Execution**: Run `pytest tests/e2e/test_complete_pipeline.py -v`
3. **Data Consistency Verification**: Verify count matching across stages
4. **Error Scenario Testing**: Test each failure scenario independently

### Expected Behaviors
- Data flows correctly from Binance through all pipeline stages
- No data loss or duplication occurs
- Schema validation passes at each stage
- Error conditions are handled gracefully

---

## Common Issues and Solutions

### Timing Issues
**Problem**: Tests fail due to race conditions or insufficient wait times
**Solution**: Implement proper synchronization and retry mechanisms

### Data Consistency Failures
**Problem**: Counts don't match between pipeline stages
**Solution**: Investigate exactly-once processing and consumer offset management

### Schema Validation Failures
**Problem**: Schema compliance fails at certain stages
**Solution**: Verify schema registration and proper Avro serialization

---

## Next Steps

After completing Step 02:
1. **Validate Test Results**: Ensure all pipeline tests pass consistently
2. **Performance Baseline**: Establish baseline metrics for Step 04
3. **Proceed to Step 03**: Begin Docker stack testing implementation

---

**Implementation Order**: Task 2.1 → Task 2.2 → Task 2.3 → Task 2.4
**Estimated Completion**: 6 hours
**Dependencies**: Step 01 (E2E Infrastructure Setup)

---

**Status**: ⬜ Documentation complete, ready for implementation
**Start Date**: After Step 01 completion
**Priority**: High