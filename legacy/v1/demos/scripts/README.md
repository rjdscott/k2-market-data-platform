# Demo Scripts Guide

**Purpose**: Understand and use demo execution and utility scripts

**Last Updated**: 2026-01-17

---

## Script Categories

Scripts are organized by function:
- **execution/**: Run interactive demos
- **utilities/**: Environment management and data operations
- **validation/**: Pre-demo checks and validation
- **resilience/**: Graceful degradation demonstrations

---

## Execution Scripts

### demo.py

**Purpose**: Interactive CLI demo (primary demo script)

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/execution/demo.py`

**Usage**:
```bash
# Full interactive demo (10-15 minutes)
python demos/scripts/execution/demo.py

# Quick demo (skips waits, 2-3 minutes)
python demos/scripts/execution/demo.py --quick

# Specific step only
python demos/scripts/execution/demo.py --step 3
```

**What it does**:
1. Validates environment (Kafka, Schema Registry, DuckDB)
2. Demonstrates live data ingestion
3. Shows query patterns and performance
4. Displays Grafana metrics
5. Demonstrates graceful degradation (optional)

**When to use**:
- Command-line demo environment
- Automated CI/CD validation
- Quick platform validation

**Options**:
- `--quick`: Skip wait periods (faster execution)
- `--step N`: Run specific step only
- `--no-cleanup`: Leave data for inspection

---

### demo_clean.py

**Purpose**: Clean variant of demo.py (minimal output)

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/execution/demo_clean.py`

**Usage**:
```bash
# Clean demo with minimal output
python demos/scripts/execution/demo_clean.py
```

**What it does**: Same as demo.py but with:
- Less verbose output
- No interactive prompts
- Progress indicators only

**When to use**:
- CI/CD pipelines
- Automated testing
- Screen recordings (cleaner output)

---

## Utility Scripts

### demo_mode.py

**Purpose**: Toggle demo mode settings

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/utilities/demo_mode.py`

**Usage**:
```bash
# Enable demo mode
python demos/scripts/utilities/demo_mode.py --enable

# Disable demo mode
python demos/scripts/utilities/demo_mode.py --disable

# Check current status
python demos/scripts/utilities/demo_mode.py --status
```

**What demo mode does**:
- Adjusts logging levels (less verbose)
- Sets shorter timeouts (faster execution)
- Configures demo-specific settings

**When to use**:
- Before running demos
- When switching between dev and demo environments

---

### reset_demo.py

**Purpose**: Full environment reset (services + data)

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/utilities/reset_demo.py`

**Usage**:
```bash
# Full reset (services + data)
python demos/scripts/utilities/reset_demo.py

# Reset data only (keep services running)
python demos/scripts/utilities/reset_demo.py --data-only

# Reset services only (keep data)
python demos/scripts/utilities/reset_demo.py --services-only
```

**What it does**:
1. Stops all Docker services
2. Removes volumes (data cleanup)
3. Restarts services
4. Validates environment

**When to use**:
- Between demos (clean slate)
- After failed demos (recovery)
- Troubleshooting environment issues

**Warning**: Destructive operation (deletes all demo data)

---

### clean_demo_data.py

**Purpose**: Clean demo data only (preserve services)

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/utilities/clean_demo_data.py`

**Usage**:
```bash
# Clean all demo data
python demos/scripts/utilities/clean_demo_data.py

# Clean specific data source
python demos/scripts/utilities/clean_demo_data.py --source binance

# Dry run (show what would be deleted)
python demos/scripts/utilities/clean_demo_data.py --dry-run
```

**What it does**:
- Deletes Kafka topics
- Clears Iceberg tables
- Resets Schema Registry schemas
- Preserves service configuration

**When to use**:
- Quick cleanup between demos
- Reset data without restarting services
- Faster than full reset (no service restart)

---

### init_e2e_demo.py

**Purpose**: Initialize end-to-end demo environment

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/utilities/init_e2e_demo.py`

**Usage**:
```bash
# Initialize with default data
python demos/scripts/utilities/init_e2e_demo.py

# Initialize with custom dataset
python demos/scripts/utilities/init_e2e_demo.py --dataset large

# Skip data loading (structure only)
python demos/scripts/utilities/init_e2e_demo.py --no-data
```

**What it does**:
1. Creates Kafka topics
2. Registers Avro schemas
3. Initializes Iceberg tables
4. Loads sample data (optional)
5. Validates environment

**When to use**:
- First-time setup
- After full reset
- Preparing demo environments

---

## Validation Scripts

### pre_demo_check.py

**Purpose**: Pre-demo validation (run 30 minutes before demo)

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/validation/pre_demo_check.py`

**Usage**:
```bash
# Quick validation
python demos/scripts/validation/pre_demo_check.py

# Full validation (comprehensive)
python demos/scripts/validation/pre_demo_check.py --full

# Specific check only
python demos/scripts/validation/pre_demo_check.py --check kafka
```

**What it validates**:
- Docker services running and healthy
- Kafka accessible (bootstrap servers)
- Schema Registry accessible (subjects)
- DuckDB initialized (tables exist)
- API responsive (health endpoint)
- Data freshness (recent messages)

**Expected output**:
```
✓ Docker services running (9/9 healthy)
✓ Kafka accessible (localhost:9092)
✓ Schema Registry accessible (localhost:8081)
✓ DuckDB initialized (3 tables found)
✓ API responsive (200 OK)
✓ Data fresh (last message 45s ago)
```

**When to use**:
- 30 minutes before every demo (mandatory)
- After environment changes
- Troubleshooting issues

---

## Resilience Scripts

### demo_degradation.py

**Purpose**: Demonstrate graceful degradation and circuit breaker

**Location**: `/home/rjdscott/Documents/projects/k2-market-data-platform/demos/scripts/resilience/demo_degradation.py`

**Usage**:
```bash
# Quick degradation demo (2-3 minutes)
python demos/scripts/resilience/demo_degradation.py --quick

# Full degradation cycle (5-7 minutes)
python demos/scripts/resilience/demo_degradation.py

# Specific degradation level
python demos/scripts/resilience/demo_degradation.py --level GRACEFUL
```

**What it demonstrates**:
1. Simulate consumer lag (inject delays)
2. Trigger degradation levels (NORMAL → GRACEFUL → AGGRESSIVE)
3. Show load shedding (drop low-priority symbols)
4. Automatic recovery with hysteresis
5. Grafana metrics visualization

**Degradation levels**:
- NORMAL: All features enabled
- SOFT: Skip enrichment
- GRACEFUL: Drop Tier 3 symbols
- AGGRESSIVE: Only Tier 1 symbols (BTC, ETH)
- CIRCUIT_BREAK: Stop processing

**When to use**:
- Demonstrating operational maturity
- Resilience testing
- Failure scenario planning

---

## Common Workflows

### First-Time Setup

```bash
# 1. Start services
docker-compose up -d

# 2. Initialize environment
python demos/scripts/utilities/init_e2e_demo.py

# 3. Validate
python demos/scripts/validation/pre_demo_check.py

# 4. Run quick demo
python demos/scripts/execution/demo.py --quick
```

---

### Pre-Demo Preparation

```bash
# 1. Full validation (30 min before)
python demos/scripts/validation/pre_demo_check.py --full

# 2. Enable demo mode
python demos/scripts/utilities/demo_mode.py --enable

# 3. Review key metrics
cat demos/reference/key-metrics.md

# 4. Test quick demo
python demos/scripts/execution/demo.py --quick
```

---

### Reset Between Demos

```bash
# Option 1: Quick reset (data only, faster)
python demos/scripts/utilities/clean_demo_data.py

# Option 2: Full reset (services + data, slower)
python demos/scripts/utilities/reset_demo.py

# Option 3: Re-initialize
python demos/scripts/utilities/init_e2e_demo.py
```

---

## Troubleshooting

### Script Fails to Import Modules

**Symptom**: `ModuleNotFoundError`

**Fix**:
```bash
# Ensure environment is synced
uv sync --all-extras

# Add project root to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Try again
python demos/scripts/execution/demo.py
```

---

### Validation Fails

**Symptom**: `pre_demo_check.py` reports failures

**Fix**:
```bash
# Check Docker services
docker-compose ps

# Restart failed services
docker-compose restart

# Full reset if needed
python demos/scripts/utilities/reset_demo.py
```

---

### Demo Hangs

**Symptom**: Script hangs indefinitely

**Common causes**:
- Waiting for Kafka messages that aren't coming
- Network timeout connecting to services

**Fix**:
```bash
# Cancel script (Ctrl+C)

# Check Kafka topics have messages
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Check Binance stream is running
docker logs $(docker ps -q -f name=binance)

# Restart services if needed
docker-compose restart
```

---

## Next Steps

**For demo operators**: See [Demo Checklist](../reference/demo-checklist.md) for pre-demo validation

**For troubleshooting**: See [Useful Commands](../reference/useful-commands.md) for common operations

**For detailed architecture**: See [Technical Guide](../docs/technical-guide.md)

---

**Last Updated**: 2026-01-17
**Questions?**: See [Demo README](../README.md) or [Quick Reference](../reference/quick-reference.md)
