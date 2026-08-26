# Quick Start Guide

**Goal**: Get from zero to working demo in under 5 minutes

**Last Updated**: 2026-01-17

---

## Prerequisites

Before starting, ensure you have:

- [x] Docker and Docker Compose installed
- [x] Python 3.13+ installed
- [x] `uv` package manager installed
- [x] Repository cloned locally

**Check prerequisites**:
```bash
docker --version          # Should be 20.10+
docker-compose --version  # Should be 1.29+
python --version          # Should be 3.13+
uv --version              # Should be 0.1+
```

---

## 5-Minute Setup

### Step 1: Install Dependencies (1 minute)

```bash
# Install all project dependencies
uv sync --all-extras
```

**What this does**: Installs all Python packages needed for the platform

### Step 2: Start Services (2 minutes)

```bash
# Start Kafka, Schema Registry, and DuckDB
docker-compose up -d

# Wait for services to be healthy
sleep 10
```

**What this does**: Starts all required services in Docker containers

**Services started**:
- Kafka (localhost:9092)
- Schema Registry (localhost:8081)
- DuckDB (embedded, no separate service)

### Step 3: Validate Environment (1 minute)

```bash
# Run pre-demo validation checks
python demos/scripts/validation/pre_demo_check.py
```

**Expected output**:
```
✓ Docker services running
✓ Kafka accessible
✓ Schema Registry accessible
✓ DuckDB initialized
✓ API responsive
```

**If validation fails**: See [Troubleshooting](#troubleshooting) section below

### Step 4: Run Your First Demo (1 minute)

```bash
# Quick CLI demo (2-3 minutes)
python demos/scripts/execution/demo.py --quick
```

**Or open Jupyter notebook**:
```bash
# Executive demo (12 minutes interactive)
jupyter notebook demos/notebooks/executive-demo.ipynb
```

---

## What Just Happened?

You now have:
1. All services running (Kafka, Schema Registry)
2. K2 platform ready to ingest and query data
3. Demo data loaded and ready to use

**Next steps**:
- Try the [Executive Demo](../notebooks/executive-demo.ipynb) for a business-focused view
- Try the [Technical Deep-Dive](../notebooks/technical-deep-dive.ipynb) for architecture details
- See [Demo README](../README.md) for more options

---

## Common Commands

### Check Service Status

```bash
# Check Docker containers
docker-compose ps

# Check Kafka topics
docker exec -it $(docker ps -q -f name=kafka) kafka-topics --list --bootstrap-server localhost:9092
```

### Stop Services

```bash
# Stop all services
docker-compose down

# Stop and remove data
docker-compose down -v
```

### Reset Demo

```bash
# Full reset (services + data)
python demos/scripts/utilities/reset_demo.py

# Clean data only
python demos/scripts/utilities/clean_demo_data.py
```

---

## Troubleshooting

### Issue: Docker services won't start

**Symptom**: `docker-compose up -d` fails

**Check**:
```bash
# Check Docker is running
docker ps

# Check ports are available
lsof -i :9092  # Kafka
lsof -i :8081  # Schema Registry
```

**Fix**:
```bash
# Kill processes using ports
sudo lsof -ti:9092 | xargs kill -9
sudo lsof -ti:8081 | xargs kill -9

# Restart Docker
sudo systemctl restart docker  # Linux
# Or restart Docker Desktop on Mac/Windows

# Try again
docker-compose down -v
docker-compose up -d
```

### Issue: Kafka not accessible

**Symptom**: `pre_demo_check.py` fails on Kafka check

**Check**:
```bash
# Check Kafka container logs
docker logs $(docker ps -q -f name=kafka)
```

**Fix**:
```bash
# Restart Kafka
docker-compose restart kafka

# Wait for it to be ready
sleep 10

# Validate again
python demos/scripts/validation/pre_demo_check.py
```

### Issue: Schema Registry not accessible

**Symptom**: `pre_demo_check.py` fails on Schema Registry check

**Check**:
```bash
# Check Schema Registry container logs
docker logs $(docker ps -q -f name=schema-registry)

# Try to access Schema Registry
curl http://localhost:8081/subjects
```

**Fix**:
```bash
# Restart Schema Registry
docker-compose restart schema-registry

# Wait for it to be ready
sleep 10

# Validate again
python demos/scripts/validation/pre_demo_check.py
```

### Issue: DuckDB not found

**Symptom**: `pre_demo_check.py` fails on DuckDB check

**Fix**:
```bash
# DuckDB is embedded, no separate service needed
# Just ensure Python environment is correct
uv sync --all-extras

# Try again
python demos/scripts/validation/pre_demo_check.py
```

### Issue: Python import errors

**Symptom**: `ModuleNotFoundError` when running scripts

**Fix**:
```bash
# Ensure environment is activated and synced
uv sync --all-extras

# Add project root to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Try again
python demos/scripts/execution/demo.py --quick
```

### Issue: Notebook won't execute

**Symptom**: Jupyter notebook cells fail to execute

**Check**:
```bash
# Check kernel is installed
jupyter kernelspec list

# Check environment
which python
```

**Fix**:
```bash
# Ensure correct environment
uv sync --all-extras

# Restart Jupyter
jupyter notebook demos/notebooks/executive-demo.ipynb
```

---

## Need More Help?

- **Contingency Plan**: See [Contingency Plan](../reference/contingency-plan.md) for failure recovery
- **Command Reference**: See [Useful Commands](../reference/useful-commands.md) for common operations
- **Technical Guide**: See [Technical Guide](./technical-guide.md) for detailed architecture

**Questions?**: Create an issue or consult the [Quick Reference](../reference/quick-reference.md)

---

## Next Steps

Now that you have the platform running:

1. **For Executives**: Open [Executive Demo](../notebooks/executive-demo.ipynb) to see business value
2. **For Engineers**: Open [Technical Deep-Dive](../notebooks/technical-deep-dive.ipynb) to understand architecture
3. **For Operators**: Review [Demo Checklist](../reference/demo-checklist.md) before running demos

---

**Last Updated**: 2026-01-17
**Questions?**: See [Demo README](../README.md)
