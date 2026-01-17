# Notebook Selection Guide

**Purpose**: Choose the right demo notebook for your audience

**Last Updated**: 2026-01-17

---

## Available Notebooks

### 1. Executive Demo (`executive-demo.ipynb`)

**Audience**: CTO, VP Engineering, Business Decision Makers

**Duration**: 12 minutes (interactive)

**Goal**: Demonstrate business value and key capabilities

**When to use**:
- Initial platform presentation to leadership
- Business case discussions
- Vendor evaluations
- Time-constrained executive briefings

**What it covers**:
- Platform value proposition (L3 cold path positioning)
- Key metrics (388ms p50, 10:1 compression, $0.85/M msgs)
- Live data ingestion demonstration
- Cost comparison vs alternatives (35-40% savings)
- Operational maturity (graceful degradation)

**Prerequisites**:
- Docker services running
- Pre-demo validation passed
- 30-minute pre-demo preparation

**Execution time**: ~12 minutes (includes 2-minute wait for data accumulation)

**Success criteria**:
- All cells execute without errors
- Live data visible in Grafana
- Query performance within targets (<500ms p99)

---

### 2. Technical Deep-Dive (`technical-deep-dive.ipynb`)

**Audience**: Engineers, Architects, Technical Decision Makers

**Duration**: 30-40 minutes (interactive)

**Goal**: Understand architecture, performance, and implementation details

**When to use**:
- Technical architecture reviews
- Implementation planning discussions
- Performance validation sessions
- Training new team members

**What it covers**:
- Detailed architecture walkthrough (Kafka → Iceberg → DuckDB)
- Component interactions and data flow
- Schema evolution demonstration (V1 → V2)
- Performance benchmarking and evidence
- Partitioning strategy and query optimization
- Circuit breaker and graceful degradation
- Monitoring and observability patterns

**Prerequisites**:
- Docker services running
- Pre-demo validation passed
- Understanding of distributed systems concepts
- 1-hour time block

**Execution time**: ~35 minutes (includes multiple wait periods for data accumulation)

**Success criteria**:
- All cells execute without errors
- Performance metrics match expectations
- Query patterns demonstrate partition pruning
- Degradation demo shows state transitions

---

### 3. Binance Crypto Demo (`exchange-demos/binance-crypto.ipynb`)

**Audience**: Technical specialists, Crypto market experts

**Duration**: 15-20 minutes (interactive)

**Goal**: Demonstrate live cryptocurrency streaming and analysis

**When to use**:
- Crypto-specific use cases
- Real-time streaming demonstrations
- WebSocket integration discussions
- Exchange-specific feature validation

**What it covers**:
- WebSocket connection to Binance
- Live trade streaming (BTCUSDT, ETHUSDT)
- Avro serialization with Schema Registry
- Real-time data validation
- Query patterns for crypto data

**Prerequisites**:
- Docker services running
- Binance WebSocket accessible (no API key needed)
- Pre-demo validation passed

**Execution time**: ~18 minutes (includes 5-minute streaming window)

**Success criteria**:
- WebSocket connection established
- Trades streaming at expected rate (10-50 msg/sec typical)
- Data queryable within 2 seconds of ingestion

---

### 4. ASX Equities Demo (`exchange-demos/asx-equities.ipynb`)

**Audience**: Technical specialists, Equities market experts

**Duration**: 15-20 minutes (interactive)

**Goal**: Demonstrate historical equities data analysis

**When to use**:
- Equities-specific use cases
- Bulk loading demonstrations
- Historical analysis patterns
- Exchange-specific feature validation

**What it covers**:
- Bulk loading historical equities data
- ASX market structure and conventions
- Time-series analysis patterns
- Query optimization for equities data

**Prerequisites**:
- Docker services running
- Historical ASX data available
- Pre-demo validation passed

**Execution time**: ~17 minutes (includes bulk data loading)

**Success criteria**:
- Data loaded successfully
- Queries return expected results
- Performance within targets

---

## Selection Decision Tree

### Start Here: What's your primary goal?

**Goal: Get funding/approval** → Executive Demo
- Focus on business value, cost savings, operational maturity
- Show, don't tell: live demonstration
- Keep it concise (12 minutes)

**Goal: Technical evaluation** → Technical Deep-Dive
- Detailed architecture walkthrough
- Performance evidence and benchmarking
- Implementation patterns and trade-offs

**Goal: Crypto use case** → Binance Crypto Demo
- Live streaming demonstration
- Crypto-specific features
- Real-time data validation

**Goal: Equities use case** → ASX Equities Demo
- Historical data analysis
- Equities-specific features
- Bulk loading patterns

---

## Audience Mapping

| Audience | Primary Notebook | Secondary Notebook |
|----------|------------------|-------------------|
| CTO, VP Engineering | Executive Demo | Technical Deep-Dive |
| Software Engineers | Technical Deep-Dive | Executive Demo |
| Data Engineers | Technical Deep-Dive | Exchange-specific demos |
| Architects | Technical Deep-Dive | Executive Demo |
| Crypto Specialists | Binance Crypto | Technical Deep-Dive |
| Equities Specialists | ASX Equities | Technical Deep-Dive |
| Business Analysts | Executive Demo | - |

---

## Execution Tips

### Before Opening Any Notebook

```bash
# 1. Validate environment (critical)
python demos/scripts/validation/pre_demo_check.py

# 2. Check services are healthy
docker-compose ps

# 3. Review key metrics
cat demos/reference/key-metrics.md
```

### During Execution

**DO**:
- Execute cells sequentially (top to bottom)
- Wait for completion before next cell
- Monitor Grafana during live ingestion
- Explain what's happening, not just results

**DON'T**:
- Skip cells (dependencies may break)
- Execute out of order
- Rush through wait periods (data needs time to accumulate)
- Ignore errors (address immediately)

### After Execution

```bash
# 1. Reset for next demo
python demos/scripts/utilities/reset_demo.py

# 2. Clean demo data
python demos/scripts/utilities/clean_demo_data.py

# 3. Validate clean state
python demos/scripts/validation/pre_demo_check.py
```

---

## Troubleshooting

### Notebook Won't Start

**Symptom**: Jupyter fails to launch

**Fix**:
```bash
# Ensure correct environment
uv sync --all-extras

# Restart Jupyter
jupyter notebook demos/notebooks/executive-demo.ipynb
```

---

### Cell Execution Fails

**Symptom**: Cell throws exception

**Common causes**:
- Services not running → `docker-compose up -d`
- Environment not validated → `python demos/scripts/validation/pre_demo_check.py`
- Previous cell skipped → Re-run from top
- Stale data → `python demos/scripts/utilities/reset_demo.py`

**Fix**: Address root cause, restart kernel, re-run from top

---

### No Data Visible

**Symptom**: Queries return empty results

**Check**:
```bash
# Check Kafka topics have messages
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Check Binance stream is running
docker logs $(docker ps -q -f name=binance)

# Check API endpoint
curl -H "X-API-Key: k2-dev-api-key-2026" http://localhost:8000/v1/symbols
```

**Fix**: Wait 2-3 minutes for data accumulation, or restart services

---

### Performance Slower Than Expected

**Symptom**: Query latency > 1000ms

**Common causes**:
- Cold cache (first query after restart)
- Missing partition filter (full table scan)
- High CPU load (other processes)

**Fix**:
- Pre-warm cache with common queries
- Always include date filter
- Close unnecessary applications

---

## Pre-Demo Checklist

**30 minutes before demo**:
- [ ] Run full validation: `python demos/scripts/validation/pre_demo_check.py --full`
- [ ] Test notebook execution: `jupyter nbconvert --execute demos/notebooks/executive-demo.ipynb`
- [ ] Review key metrics: `cat demos/reference/key-metrics.md`
- [ ] Check demo checklist: `cat demos/reference/demo-checklist.md`

**5 minutes before demo**:
- [ ] Laptop charged 100%
- [ ] Do Not Disturb enabled
- [ ] Browser tabs open (Grafana, Prometheus, API docs)
- [ ] Terminal window ready for logs
- [ ] Quick reference printed and nearby

---

## Next Steps

**After choosing a notebook**:
1. See [Quick Start Guide](../docs/quick-start.md) for environment setup
2. See [Demo Checklist](../reference/demo-checklist.md) for pre-demo validation
3. See [Useful Commands](../reference/useful-commands.md) for CLI operations

---

**Last Updated**: 2026-01-17
**Questions?**: See [Demo README](../README.md) or [Quick Reference](../reference/quick-reference.md)
