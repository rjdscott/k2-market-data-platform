# K2 Platform - Validation Guide for Steps 1-2

**Purpose**: This guide provides step-by-step instructions to validate the implementation of Steps 1-2 (Infrastructure & Schemas).

**Status**: Ready to validate once environment is properly configured

---

## Prerequisites

### System Requirements

✅ **Docker Desktop**
- Version: 20.10+ recommended
- Memory: 8GB minimum allocated to Docker
- Storage: 20GB free space for images

✅ **Python 3.11 or 3.12**
- Required for type hints (`list[str]` syntax)
- Check version: `python3.11 --version` or `python3.12 --version`

### Install Python 3.11+ (If Not Available)

#### macOS (Homebrew)
```bash
brew install python@3.11

# Verify installation
python3.11 --version
```

#### Ubuntu/Debian
```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev

# Verify installation
python3.11 --version
```

#### Windows
Download from https://www.python.org/downloads/ (Python 3.11.x)

---

## Step-by-Step Validation

### 1. Environment Setup

```bash
# Navigate to project directory
cd /path/to/k2-market-data-platform

# Create virtual environment with Python 3.11
python3.11 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# OR
.venv\Scripts\activate     # Windows

# Verify Python version in venv
python --version
# Expected: Python 3.11.x or 3.12.x

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install package in development mode with dev dependencies
pip install -e ".[dev]"
```

**Expected output**: No errors, all packages installed successfully

---

### 2. Start Docker Services

```bash
# Start all services (Kafka, MinIO, PostgreSQL, etc.)
docker compose up -d

# Wait for services to initialize
sleep 30

# Check service health
docker compose ps
```

**Expected output**: All services in "Up" state with "healthy" status

**If MinIO is unhealthy**, check logs:
```bash
docker logs k2-minio

# Common fix: Restart MinIO
docker compose restart k2-minio

# Wait and check again
sleep 10
docker compose ps
```

---

### 3. Run Unit Tests

Unit tests validate schema structure without requiring Docker services.

```bash
# Run schema unit tests
pytest tests/unit/test_schemas.py -v -m unit

# Expected output:
# tests/unit/test_schemas.py::TestSchemas::test_trade_schema_valid PASSED
# tests/unit/test_schemas.py::TestSchemas::test_quote_schema_valid PASSED
# tests/unit/test_schemas.py::TestSchemas::test_reference_data_schema_valid PASSED
# tests/unit/test_schemas.py::TestSchemas::test_all_schemas_have_required_fields PASSED
# tests/unit/test_schemas.py::TestSchemas::test_timestamp_fields_use_logical_type PASSED
# tests/unit/test_schemas.py::TestSchemas::test_decimal_fields_use_logical_type PASSED
# tests/unit/test_schemas.py::TestSchemas::test_list_available_schemas PASSED
# tests/unit/test_schemas.py::TestSchemas::test_load_nonexistent_schema_raises_error PASSED
# tests/unit/test_schemas.py::TestSchemas::test_schema_files_have_doc_strings PASSED
# tests/unit/test_schemas.py::TestSchemas::test_optional_fields_have_defaults PASSED
#
# ========== 10 passed in 0.XX s ==========
```

**Validation Criteria**: All 10 tests pass

---

### 4. Run Infrastructure Integration Tests

These tests validate Docker services are accessible.

```bash
# Run infrastructure tests
pytest tests/integration/test_infrastructure.py -v -m integration

# Expected output:
# tests/integration/test_infrastructure.py::TestInfrastructure::test_kafka_broker_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_schema_registry_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_minio_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_postgres_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_iceberg_rest_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_prometheus_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_grafana_available PASSED
# tests/integration/test_infrastructure.py::TestInfrastructure::test_kafka_ui_available PASSED
#
# ========== 8 passed in 2.XX s ==========
```

**Validation Criteria**: All 8 tests pass

**If tests fail**:
- Check `docker compose ps` - all services should be healthy
- Check service logs: `docker logs <service-name>`
- Restart unhealthy services: `docker compose restart <service-name>`

---

### 5. Initialize Infrastructure

Run the initialization script to create Kafka topics and Iceberg namespaces.

```bash
# Run infrastructure initialization
python scripts/init_infra.py
```

**Expected output**:
```
INFO     Creating Kafka topics bootstrap_servers=localhost:9092
INFO     Topic created successfully topic=market.trades.raw
INFO     Topic created successfully topic=market.quotes.raw
INFO     Topic created successfully topic=market.reference_data
INFO     Validating MinIO buckets endpoint=http://localhost:9000
INFO     Bucket exists bucket=warehouse
INFO     Bucket exists bucket=data
INFO     Bucket exists bucket=backups
INFO     Creating Iceberg namespaces catalog_uri=http://localhost:8181
INFO     Namespace created successfully namespace=market_data
INFO     Namespace created successfully namespace=reference_data
INFO     Infrastructure initialization complete
```

**Validation Criteria**:
- Script completes without errors
- Topics created message for all 3 topics
- Buckets exist for all 3 buckets
- Namespaces created for both namespaces

---

### 6. Verify Kafka Topics

```bash
# List Kafka topics
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list

# Expected output:
# market.quotes.raw
# market.reference_data
# market.trades.raw
```

**Validation Criteria**: All 3 topics listed

---

### 7. Run Schema Registry Integration Tests

These tests validate schema registration.

```bash
# Run schema registry tests
pytest tests/integration/test_schema_registry.py -v -m integration

# Expected output:
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_register_all_schemas PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_schemas_are_idempotent PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_schema_subjects_follow_convention PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_retrieve_registered_schema PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_schema_compatibility_mode PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_list_schema_versions PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_schema_registry_health PASSED
# tests/integration/test_schema_registry.py::TestSchemaRegistry::test_incompatible_schema_rejected PASSED
#
# ========== 8 passed in 3.XX s ==========
```

**Validation Criteria**: All 8 tests pass (or 7 pass, 1 skip if compatibility test skips)

---

### 8. Verify Schema Registration

```bash
# List registered schemas
curl http://localhost:8081/subjects

# Expected output:
# ["market.trades.raw-value","market.quotes.raw-value","market.reference_data-value"]
```

**Validation Criteria**: All 3 schema subjects listed

---

### 9. Visual Verification (Optional)

Open Kafka UI to visually inspect topics and schemas:

```bash
# Open Kafka UI
open http://localhost:8080  # macOS
# OR
xdg-open http://localhost:8080  # Linux
# OR visit http://localhost:8080 in browser
```

**What to check**:
- Topics tab: Should show 3 topics (trades, quotes, reference_data)
- Schema Registry tab: Should show 3 registered schemas
- Each topic shows partition count (6 for trades/quotes, 1 for reference_data)

---

## Validation Checklist

Complete this checklist to confirm Steps 1-2 are fully validated:

- [ ] Python 3.11+ installed and verified
- [ ] Virtual environment created with correct Python version
- [ ] All dependencies installed without errors (`pip install -e ".[dev]"`)
- [ ] Docker services started and all containers healthy
- [ ] Unit tests pass: 10/10 ✅
- [ ] Infrastructure integration tests pass: 8/8 ✅
- [ ] Infrastructure initialization script completes successfully
- [ ] Kafka topics verified: 3 topics created ✅
- [ ] Schema registry integration tests pass: 8/8 ✅
- [ ] Schemas registered: 3 schemas in Schema Registry ✅
- [ ] Kafka UI accessible and shows topics/schemas

---

## Troubleshooting

### Issue: Python version too old

**Error**: `ERROR: Package 'k2-platform' requires a different Python: 3.9.7 not in '>=3.11'`

**Solution**: Install Python 3.11+ (see Prerequisites section above)

---

### Issue: Docker service unhealthy

**Symptoms**: `docker compose ps` shows services in "unhealthy" state

**Solutions**:
```bash
# Check logs for specific service
docker logs <service-name>

# Restart specific service
docker compose restart <service-name>

# Restart all services
docker compose down
docker compose up -d

# Clean restart (removes volumes - WARNING: deletes data)
docker compose down -v
docker compose up -d
```

---

### Issue: Kafka topic already exists

**Error**: `Topic with this name already exists`

**Solution**: This is actually OK - the script handles this gracefully and logs "Topic already exists"

---

### Issue: Port already in use

**Error**: `Bind for 0.0.0.0:9092 failed: port is already allocated`

**Solution**: Another service is using the port. Either stop the conflicting service or change ports in `docker-compose.yml`

```bash
# Find what's using the port
lsof -i :9092  # macOS/Linux
netstat -ano | findstr :9092  # Windows
```

---

### Issue: Cannot connect to Docker daemon

**Error**: `Cannot connect to the Docker daemon`

**Solution**: Start Docker Desktop and wait for it to fully initialize

---

## Success Criteria

✅ **Steps 1-2 are validated when**:
1. All unit tests pass (10/10)
2. All infrastructure tests pass (8/8)
3. Infrastructure initialization completes without errors
4. All schema registry tests pass (8/8)
5. Kafka topics and schemas are visible in Kafka UI

Once all criteria are met, document results in `PROGRESS.md` and proceed to Step 3 (Storage Layer).

---

## Next Steps After Validation

1. Update `PROGRESS.md` with validation results and timestamp
2. Create git commit:
   ```bash
   git add -A
   git commit -m "Complete Steps 1-2: Infrastructure validation and schema management

   - Infrastructure integration tests (8/8 passing)
   - Avro schema definitions (trade, quote, reference_data)
   - Schema management module with registration
   - Schema validation tests (10/10 unit, 8/8 integration)
   - Infrastructure initialization script

   All tests passing. Ready for Step 3 (Storage Layer).

   Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
   ```

3. Begin Step 3: Storage Layer - Iceberg Table Initialization
