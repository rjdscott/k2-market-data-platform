# Step 01: Platform Positioning

**Status**: ⬜ Not Started
**Assignee**: Implementation Team
**Issue**: #1 - Latency Budget Misaligns with HFT Positioning

---

## Dependencies
- **Requires**: Phase 1 complete
- **Blocks**: Step 07 (Demo Narrative)

---

## Goal

Explicitly position K2 as a **Research Data Platform** for quantitative research, strategy backtesting, compliance, and market analytics—NOT an HFT execution system. This clarity prevents mismatched expectations and demonstrates architectural self-awareness.

---

## Overview

The current documentation implies HFT capabilities with 500ms p99 latency. In reality:
- HFT systems operate at microseconds (1-100μs)
- 500ms is 5,000x slower than competitive market makers
- K2 is actually a **Research Data Platform** for quantitative analysis and backtesting

### Tiered Architecture Context

Production HFT systems use tiered architecture:

```
┌─────────────────────────────────────────────────────────────┐
│ L1: Hot Path (< 10μs)                                       │
│  • Kernel bypass (Solarflare, Mellanox)                     │
│  • Shared memory IPC (not Kafka)                            │
│  • FPGAs for order book reconstruction                      │
│  • Zero-copy, zero-serialization                            │
│  • Purpose: Execution, order routing                        │
└─────────────────────────────────────────────────────────────┘
                     ↓ (async, after trade executed)
┌─────────────────────────────────────────────────────────────┐
│ L2: Warm Path (< 10ms)                                      │
│  • In-memory streaming (Kafka, Redis Streams)               │
│  • Real-time risk calculation                               │
│  • Position management                                      │
│  • Purpose: Risk, PnL, position tracking                    │
└─────────────────────────────────────────────────────────────┘
                     ↓ (batch every 1-10 seconds)
┌─────────────────────────────────────────────────────────────┐
│ L3: Cold Path (< 500ms) ← K2 OPERATES HERE                  │
│  • Kafka → Iceberg pipeline                                 │
│  • ACID transactions, time-travel                           │
│  • DuckDB analytical queries                                │
│  • Purpose: Analytics, compliance, backtesting              │
└─────────────────────────────────────────────────────────────┘
```

**K2 is a best-in-class L3 Cold Path system.**

---

## Deliverables

### 1. README Positioning Section

Add to `README.md` after the introduction:

```markdown
## Platform Positioning

### What K2 IS

K2 is a **Research Data Platform** for:

- **Quantitative Research**: Backtest trading strategies on historical tick data with sub-second queries
- **Strategy Development**: Alpha research, signal generation, and factor analysis
- **Compliance & Audit**: Time-travel queries for regulatory requirements (ASIC, FINRA)
- **Market Analytics**: OHLCV aggregations, VWAP calculations, market microstructure analysis
- **Data Science**: ML feature engineering, pattern recognition, statistical analysis

**Latency Profile**: 500ms p99 end-to-end (Kafka → Iceberg → Query)

### What K2 is NOT

K2 is **not** designed for:

- **HFT Execution**: True HFT requires microsecond latency (kernel bypass, FPGAs)
- **Real-Time Risk**: Risk systems need <10ms latency (in-memory streaming)
- **Order Routing**: Order management requires direct exchange connectivity

### Tiered Architecture

K2 operates in the **Cold Path (L3)** of a tiered trading architecture:

| Tier | Latency | Use Cases | Technology |
|------|---------|-----------|------------|
| L1 Hot | < 10μs | Execution, order routing | FPGAs, kernel bypass |
| L2 Warm | < 10ms | Risk, position management | In-memory, Redis |
| **L3 Cold** | **< 500ms** | **Analytics, compliance, backtesting** | **Kafka, Iceberg, DuckDB** |

K2 is designed to be the best L3 system—reliable, scalable, and optimized for research workloads.
```

### 2. Platform Positioning Document

Create `docs/architecture/platform-positioning.md`:

```markdown
# Platform Positioning

**Last Updated**: 2026-01-11
**Status**: Active

## Executive Summary

K2 Market Data Platform is a **Research Data Platform** operating in the Cold Path (L3)
of a tiered trading infrastructure. It provides ACID-compliant storage, time-travel queries,
and sub-second analytics for quantitative research, strategy backtesting, and market analysis.

## Target Use Cases

### Primary: Quantitative Research & Strategy Development
- Historical tick data analysis for alpha research
- Strategy backtesting with realistic market data
- Market microstructure research and signal generation
- Factor analysis and portfolio construction research
- ML feature engineering on time-series data

### Primary: Compliance & Audit
- Point-in-time queries for regulatory audits
- Trade reconstruction for ASIC/FINRA requirements
- Data lineage and provenance tracking
- 7-year retention compliance

### Primary: Market Analytics
- OHLCV bar generation (1-min, 5-min, hourly, daily)
- VWAP and TWAP calculations
- Volume profile analysis
- Cross-symbol correlation studies

### Secondary: Operational Monitoring
- Data quality dashboards
- Ingestion health monitoring
- Query performance tracking
- Cost management

## Non-Target Use Cases

### NOT: High-Frequency Trading Execution
**Why**: 500ms latency is 5,000x slower than HFT requirements
**Alternative**: FPGA-based systems with kernel bypass

### NOT: Real-Time Risk Management
**Why**: Risk systems need <10ms for margin calculations
**Alternative**: In-memory streaming with Redis/Flink

### NOT: Order Management
**Why**: Order routing requires direct exchange connectivity
**Alternative**: FIX protocol gateways with co-location

## Latency Budget

| Component | p50 | p99 | Notes |
|-----------|-----|-----|-------|
| Kafka Producer | 5ms | 10ms | Includes serialization |
| Kafka Broker | 10ms | 20ms | Replication, acks=all |
| Kafka Consumer | 20ms | 50ms | Deserialization, batching |
| Iceberg Write | 100ms | 200ms | S3 upload, metadata commit |
| DuckDB Query | 100ms | 300ms | Depends on data volume |
| **End-to-End** | **235ms** | **580ms** | Ingestion to query |

## Competitive Positioning

| Platform | Tier | Latency | Strength |
|----------|------|---------|----------|
| K2 | L3 Cold | 500ms | Research, time-travel, ACID, backtesting |
| Kdb+ | L2 Warm | 1ms | In-memory queries, time-series |
| Custom C++ | L1 Hot | 10μs | Execution, order routing |

K2 competes with data lake solutions (Databricks, Snowflake) for research and analytics workloads,
not with execution systems. Think of K2 as purpose-built for quant researchers.

## Architecture Fit

```
┌─────────────────────────────────────────────────────────────┐
│                    Trading Firm Architecture                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐    │
│  │   Exchange  │────▶│ L1: FPGAs   │────▶│   Orders    │    │
│  │    Feeds    │     │   (10μs)    │     │             │    │
│  └─────────────┘     └──────┬──────┘     └─────────────┘    │
│                             │                               │
│                             ▼                               │
│                      ┌─────────────┐                        │
│                      │ L2: Risk    │                        │
│                      │   (10ms)    │                        │
│                      └──────┬──────┘                        │
│                             │                               │
│                             ▼                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                   K2 Platform (L3)                    │  │
│  │  ┌─────────┐    ┌─────────┐    ┌─────────┐            │  │
│  │  │  Kafka  │───▶│ Iceberg │───▶│ DuckDB  │            │  │
│  │  │ (20ms)  │    │ (200ms) │    │ (300ms) │            │  │
│  │  └─────────┘    └─────────┘    └─────────┘            │  │
│  │                                                       │  │
│  │  Use Cases: Analytics, Compliance, Backtesting        │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## When to Use K2

✅ **Use K2 when**:
- Quant researchers need historical tick data for backtesting
- Strategy development requires reproducible market data access
- Compliance requires point-in-time audit queries
- Data science team needs features for ML models
- Analytics team needs OHLCV, VWAP, volume profiles
- Schema evolution and data versioning is expected

❌ **Don't use K2 when**:
- You need sub-millisecond latency for execution
- Real-time risk calculation is required
- Direct exchange order routing is needed
- In-memory computation is essential

---

## Ideal Workflows

K2 excels at workflows where **reliability and query flexibility matter more than speed**.

### Quantitative Research Workflows

#### Strategy Backtesting
- Run trading strategies against historical tick data
- Reproduce exact market conditions at any point in time
- Compare strategy variations across different market regimes
- Walk-forward optimization with time-travel queries

#### Alpha Research
- Analyze historical price patterns and market microstructure
- Generate and validate trading signals on clean, versioned data
- Factor analysis and cross-asset correlation studies
- Event study analysis (earnings, dividends, corporate actions)

### Data Science Workflows

#### ML Feature Engineering
- Build features from historical OHLCV, volume profiles, order flow
- Time-series feature extraction for predictive models
- Point-in-time correct feature generation (avoiding look-ahead bias)
- Reproducible feature pipelines with schema versioning

#### Model Training & Validation
- Access clean, versioned datasets for reproducible experiments
- Walk-forward validation with time-travel queries
- A/B comparison of model performance across time periods
- Backtest ML strategies on out-of-sample historical data

### Compliance & Audit Workflows

#### Regulatory Reporting
- ASIC/FINRA trade reconstruction queries
- "Show me all BHP trades on 2025-03-15 as of market close"
- Audit trails with full data lineage and provenance
- 7-year retention with instant point-in-time access

#### Best Execution Analysis
- Compare execution prices against market benchmarks (VWAP, TWAP)
- Transaction cost analysis (TCA) for compliance reporting
- Slippage analysis and broker performance comparison

### Analytics Workflows

#### Market Analytics
- OHLCV bar generation (1-min, 5-min, hourly, daily)
- Volume profile and liquidity analysis
- Intraday pattern detection and seasonality studies
- Cross-market correlation and lead-lag analysis

#### Portfolio Analysis
- Historical exposure and risk factor calculations
- Performance attribution on historical positions
- Drawdown analysis and stress testing on historical scenarios
- Portfolio rebalancing simulation

### Workflow Summary

| Workflow Category | Example Tasks | K2 Strength |
|-------------------|---------------|-------------|
| **Quant Research** | Backtesting, signal generation | Time-travel, reproducibility |
| **Data Science** | Feature engineering, ML training | Schema evolution, versioning |
| **Compliance** | Trade reconstruction, TCA | Point-in-time queries, audit trails |
| **Analytics** | OHLCV, VWAP, correlations | Sub-second aggregations |

**Bottom line**: K2 is for research desks, not trading desks.
```

### 3. Demo Positioning Section

Update `scripts/demo.py` to include positioning context at the start.

### 4. Tiered Architecture Diagram

Create ASCII or Mermaid diagram showing L1/L2/L3 tiers with K2's position.

---

## Implementation Details

### File Changes

| File | Change |
|------|--------|
| `README.md` | Add "Platform Positioning" section after introduction |
| `docs/architecture/platform-positioning.md` | New file |
| `scripts/demo.py` | Add positioning context at demo start |

### Code Example: Demo Update

```python
# scripts/demo.py - Add to beginning

def show_platform_positioning(self):
    """Show platform positioning context."""
    console.print("\n[bold blue]Platform Positioning[/bold blue]\n")

    console.print("[yellow]What K2 IS:[/yellow]")
    console.print("  • Research Data Platform for quantitative analysis")
    console.print("  • Strategy backtesting and alpha research")
    console.print("  • Sub-second historical queries with time-travel")
    console.print("  • ACID-compliant storage with schema evolution")

    console.print("\n[yellow]What K2 is NOT:[/yellow]")
    console.print("  • HFT execution system (needs μs latency)")
    console.print("  • Real-time risk system (needs <10ms)")
    console.print("  • Order routing gateway")

    console.print("\n[yellow]Tiered Architecture:[/yellow]")
    console.print("  L1 Hot Path:  < 10μs   (FPGAs, execution)")
    console.print("  L2 Warm Path: < 10ms   (Risk, positions)")
    console.print("  [green]L3 Cold Path:  < 500ms  (K2 - Analytics, compliance)[/green]")
```

---

## Validation

### Acceptance Criteria

1. [ ] README has "Platform Positioning" section
2. [ ] `platform-positioning.md` exists in `docs/architecture/`
3. [ ] Document explains IS/ISN'T clearly
4. [ ] Tiered architecture diagram included
5. [ ] Ideal Workflows section with 4 workflow categories
6. [ ] Demo shows positioning at start
7. [ ] Latency budget table present

### Verification Commands

```bash
# Check README section exists
grep -A 5 "Platform Positioning" README.md

# Check architecture doc exists
ls docs/architecture/platform-positioning.md

# Check demo shows positioning
python scripts/demo.py --quick 2>&1 | head -20
# Should show positioning section
```

---

## Demo Talking Points

When presenting Step 01:

> "Before we dive in, let me be clear about what K2 is and isn't.
>
> K2 is a Research Data Platform—it's designed for quantitative research, strategy backtesting,
> and market analytics. Our latency budget is 500ms p99, which is perfect for historical
> queries and time-travel, but 5,000x slower than true HFT systems.
>
> In a real trading firm, K2 would sit in the Cold Path (L3) of a tiered architecture.
> Execution happens in L1 with FPGAs. Risk runs in L2 with in-memory systems.
> K2 handles the research and analytical workloads that need reliability over speed.
>
> This positioning is intentional—we optimize for query flexibility, ACID compliance,
> and schema evolution to support quant researchers and data scientists."

---

## References

- [Principal Data Engineer Demo Review](../../reviews/2026-01-11-principal-data-engineer-demo-review.md) - Issue #1
- [Platform Principles](../../architecture/platform-principles.md)
- [Latency Backpressure Design](../../design/operations/latency-backpressure.md)

---

**Last Updated**: 2026-01-12
**Status**: ⬜ Not Started
