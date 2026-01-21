# Step 5: Storage Layer - Configuration Management

**Status**: ✅ Complete
**Assignee**: Claude Sonnet 4.5
**Started**: 2026-01-10
**Completed**: 2026-01-10
**Estimated Time**: 2-3 hours
**Actual Time**: 2.5 hours

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
   rm tests-backup/unit/test_config.py
   rm .env.example
   ```

---

## Notes & Decisions

### Decisions Made (2026-01-10)
- **Pydantic Settings**: Provides validation and type safety
- **Hierarchical config**: Sub-configs (Kafka, Iceberg) for organization
- **Environment prefix**: Each config has unique prefix (K2_KAFKA_, K2_ICEBERG_, K2_DB_, K2_OBSERVABILITY_)
- **Singleton pattern**: Global `config` instance for easy import
- **Backward compatibility**: Default values match current hardcoded values

### Configuration Parameters Identified

**IcebergConfig (from catalog.py, writer.py)**:
- catalog_uri: str = "http://localhost:8181"
- s3_endpoint: str = "http://localhost:9000"
- s3_access_key: str = "admin"
- s3_secret_key: str = "password"
- warehouse: str = "s3://warehouse/"

**KafkaConfig (from schemas/__init__.py, future producer/consumer)**:
- bootstrap_servers: str = "localhost:9092"
- schema_registry_url: str = "http://localhost:8081"

**DatabaseConfig (for future sequence tracker)**:
- host: str = "localhost"
- port: int = 5432
- user: str = "iceberg"
- password: str = "iceberg"
- database: str = "iceberg_catalog"

**ObservabilityConfig (from logging.py, metrics.py)**:
- log_level: str = "INFO"
- prometheus_port: int = 9090
- enable_metrics: bool = True

**K2Config (main config)**:
- environment: str = "local"
- kafka: KafkaConfig
- iceberg: IcebergConfig
- database: DatabaseConfig
- observability: ObservabilityConfig

### Implementation Strategy
1. Create config module with all sub-configs
2. Use pydantic-settings for env var loading
3. Create singleton instance
4. Update existing modules (catalog, writer, schemas) to use config
5. Create .env.example with all parameters documented
6. Write comprehensive unit tests

### References
- Pydantic Settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- Existing imports in catalog.py:27, writer.py:20
