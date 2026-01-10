# Step 5: Storage Layer - Configuration Management

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 2-3 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Steps 1-4 (to understand configuration needs)
- **Blocks**: All subsequent steps (centralized config used throughout)

## Goal
Centralize configuration for different environments (local, test, prod). Provide type-safe configuration using Pydantic Settings with environment variable support.

---

## Implementation

### 5.1 Create Configuration Module

**File**: `src/k2/common/config.py`

See original plan lines 1056-1140 for complete implementation.

Key components:
- `KafkaConfig` - Bootstrap servers, schema registry URL
- `IcebergConfig` - Catalog URI, S3 endpoints, credentials
- `ObservabilityConfig` - Prometheus port, log level
- `K2Config` - Main config aggregating all sub-configs
- Environment variable override support via `env_prefix`

### 5.2 Update Existing Modules

Update catalog.py, writer.py, and other modules to use centralized config instead of hardcoded values.

### 5.3 Test Configuration

**File**: `tests/unit/test_config.py`

```python
def test_default_config():
    config = K2Config()
    assert config.environment == "local"
    assert config.kafka.bootstrap_servers == "localhost:9092"

def test_env_override(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    config = K2Config()
    assert config.kafka.bootstrap_servers == "kafka:29092"
```

---

## Validation Checklist

- [ ] Configuration module created (`src/k2/common/config.py`)
- [ ] All config classes use Pydantic BaseSettings
- [ ] Environment variables override defaults
- [ ] `.env.example` file created documenting all variables
- [ ] Unit tests pass: `pytest tests/unit/test_config.py -v`
- [ ] Existing modules updated to use config
- [ ] No hardcoded connection strings in codebase

---

## Rollback Procedure

1. **Revert modules to hardcoded config**:
   ```bash
   git checkout src/k2/storage/catalog.py
   git checkout src/k2/storage/writer.py
   ```

2. **Remove config files**:
   ```bash
   rm src/k2/common/config.py
   rm tests/unit/test_config.py
   rm .env.example
   ```

---

## Notes & Decisions

### Decisions Made
- **Pydantic Settings**: Provides validation and type safety
- **Hierarchical config**: Sub-configs (Kafka, Iceberg) for organization
- **Environment prefix**: Each config has unique prefix (KAFKA_, ICEBERG_)

### References
- Pydantic Settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
