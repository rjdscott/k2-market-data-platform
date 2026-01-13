# Step 08: Cost Model

**Status**: ⬜ Not Started
**Assignee**: Implementation Team
**Issue**: #7 - Missing Cost Awareness

---

## Dependencies
- **Requires**: None (documentation step)
- **Blocks**: Step 09 (Final Validation)

---

## Goal

Add cost awareness documentation demonstrating FinOps understanding. Principal engineers balance technical excellence with business constraints—showing cost awareness elevates the portfolio.

---

## Overview

### Current Problem

No discussion of:
- Infrastructure costs at various scales
- Cost optimization opportunities
- FinOps decision triggers

### Solution

Create comprehensive cost model documentation covering:
- Baseline costs (dev environment)
- Production costs (100x scale)
- Scale costs (1000x scale)
- Optimization triggers and strategies

---

## Deliverables

### 1. Cost Model Document

Create `docs/operations/cost-model.md`:

```markdown
# K2 Platform Cost Model

**Last Updated**: 2026-01-11
**Reference Region**: AWS ap-southeast-2 (Sydney)
**Status**: Active

---

## Executive Summary

This document provides cost modeling for the K2 Market Data Platform at various
scale points. Understanding infrastructure costs enables informed architectural
decisions and budget planning.

**Key Finding**: At 100x scale (1M msg/sec), monthly infrastructure cost is
approximately **$15,100**. Storage dominates costs (60%), followed by compute (17%)
and Kafka (17%).

---

## Baseline Workload (1x Scale - Development)

### Specifications
- **Message Rate**: 10,000 msg/sec (peak during market open)
- **Message Size**: 500 bytes (Avro-serialized)
- **Daily Volume**: 10K msg/sec × 86,400 sec = 864M messages/day
- **Daily Storage**: 864M × 500 bytes = 432GB/day raw
- **Compressed**: ~130GB/day (Parquet + Zstd at 3.3x compression)

### Infrastructure (Docker Compose - Local)

| Component | Configuration | Monthly Cost |
|-----------|---------------|--------------|
| Kafka | 1 broker, 6 partitions | $0 |
| Schema Registry | 1 instance | $0 |
| MinIO | Local storage | $0 |
| PostgreSQL | Local | $0 |
| DuckDB | Embedded | $0 |
| **Total** | **Local Dev** | **$0** |

---

## Production Workload (100x Scale)

### Specifications
- **Message Rate**: 1,000,000 msg/sec
- **Daily Volume**: 86.4B messages/day
- **Daily Storage**: 13TB/day compressed
- **Monthly Storage Growth**: 390TB/month
- **Retention**: 2 years (9.4PB total)

### Infrastructure (AWS ap-southeast-2)

| Component | Resources | Specs | Monthly Cost |
|-----------|-----------|-------|--------------|
| **Kafka (MSK)** | 5× broker.m5.xlarge | 4 vCPU, 16GB, 500GB EBS | $2,520 |
| **S3 (Iceberg)** | 390TB/month | Standard storage | $8,970 |
| **PostgreSQL (RDS)** | db.r6g.large | 2 vCPU, 16GB | $410 |
| **Compute (EC2)** | 4× c6g.2xlarge | 8 vCPU, 16GB each | $1,180 |
| **Data Transfer** | Cross-AZ + egress | ~2TB/month | $1,840 |
| **Redis (ElastiCache)** | cache.r6g.large | 2 nodes | $580 |
| **Total** | | | **$15,500** |

### Cost Breakdown

```
Storage (S3):      58% ████████████████████████████
Kafka (MSK):       16% ████████
Data Transfer:     12% ██████
Compute (EC2):      8% ████
Redis:              4% ██
PostgreSQL:         2% █
```

---

## Scale Workload (1000x Scale)

### Specifications
- **Message Rate**: 10,000,000 msg/sec
- **Daily Volume**: 864B messages/day
- **Daily Storage**: 130TB/day compressed
- **Monthly Storage Growth**: 3.9PB/month

### Infrastructure

| Component | Resources | Monthly Cost |
|-----------|-----------|--------------|
| **Kafka (MSK)** | 20× broker.m5.2xlarge | $15,200 |
| **S3 (Iceberg)** | 3.9PB/month | $89,700 |
| **S3 Glacier** | Archive (90+ days) | -$35,000 |
| **PostgreSQL (RDS)** | db.r6g.xlarge HA | $1,640 |
| **Presto (EMR)** | 20× c5.4xlarge | $24,000 |
| **Data Transfer** | ~20TB/month | $18,400 |
| **Redis (ElastiCache)** | 4× cache.r6g.xlarge | $2,320 |
| **Total (before optimization)** | | **$151,260** |
| **Total (with Glacier)** | | **$116,260** |

---

## Cost Optimization Strategies

### 1. Storage Tiering (Saves 30-50%)

**Trigger**: Storage costs > $10K/month

**Implementation**:
```
Hot (0-7 days):    S3 Standard     $0.023/GB
Warm (7-90 days):  S3 IA           $0.0125/GB  (-46%)
Cold (90+ days):   S3 Glacier IR   $0.004/GB   (-83%)
```

**Savings at 100x**: ~$2,700/month (30%)
**Savings at 1000x**: ~$35,000/month (39%)

### 2. Reserved Instances (Saves 30-40%)

**Trigger**: Stable production workload (6+ months)

**Implementation**:
- Kafka brokers: 1-year reserved (-34%)
- RDS: 1-year reserved (-31%)
- EC2 compute: 1-year reserved (-30%)

**Savings at 100x**: ~$1,200/month
**Savings at 1000x**: ~$12,000/month

### 3. Spot Instances for Batch (Saves 60-70%)

**Trigger**: Batch processing workloads (compaction, replay)

**Implementation**:
- Use Spot for non-critical batch jobs
- Auto-scaling with Spot fleet
- Checkpointing for interruption handling

**Savings**: Variable, typically 60-70% on batch compute

### 4. Query Result Caching (Reduces Compute)

**Trigger**: Repetitive queries > 30% of traffic

**Implementation**:
- Redis cache for common aggregations
- 5-minute TTL for OHLCV bars
- 300-second TTL for reference data

**Impact**: 50%+ reduction in query compute

### 5. Partition Pruning Optimization

**Trigger**: Query latency increasing

**Implementation**:
- Ensure queries include partition keys
- Monitor files scanned per query
- Add indexes for common access patterns

**Impact**: 10-100x faster queries, lower compute

---

## Cost Monitoring

### Prometheus Metrics

```yaml
# Cost proxy metrics
k2_s3_storage_bytes_total:
  description: Total S3 storage in bytes
  formula: value * 0.023 / 1e9 = monthly_cost

k2_kafka_messages_produced_total:
  description: Kafka throughput
  formula: rate * 500 * 2.6e6 / 1e9 = monthly_storage_gb

k2_query_executions_total:
  description: Query volume
  formula: count * avg_compute_cost = query_cost
```

### Grafana Alerts

```yaml
alerts:
  - name: storage_cost_spike
    expr: increase(k2_s3_storage_bytes_total[1d]) / 1e12 * 0.023 > 500
    description: Storage cost increased by >$500/day
    severity: warning

  - name: kafka_throughput_spike
    expr: rate(k2_kafka_messages_produced_total[5m]) > 2000000
    description: Kafka throughput >2M msg/sec (cost impact)
    severity: info
```

---

## Cost Comparison

### vs. Alternatives

| Solution | 100x Scale | 1000x Scale | Notes |
|----------|------------|-------------|-------|
| **K2 (Iceberg)** | $15,500 | $116,000 | Open source, portable |
| **Snowflake** | $25,000 | $200,000 | Managed, higher compute |
| **Databricks** | $22,000 | $180,000 | Managed, good ML support |
| **kdb+** | $50,000+ | $500,000+ | License + hardware |

**K2 Advantage**: 35-40% lower cost than managed alternatives, no vendor lock-in.

---

## Budgeting Guidelines

### Planning Formula

```
Monthly Cost ≈ (Daily_TB × 30 × $0.023) +    # Storage
               (Brokers × $500) +             # Kafka
               (Compute_Nodes × $300) +       # Compute
               (Transfer_TB × $90) +          # Network
               (Fixed_Services × $500)        # RDS, Redis, etc.
```

### Quick Estimates

| Scale | Messages/sec | Monthly Cost |
|-------|--------------|--------------|
| 10K | $0 (local) | Dev |
| 100K | ~$5,000 | Small prod |
| 1M | ~$15,000 | Medium prod |
| 10M | ~$120,000 | Large prod |

---

## Decision Points

### When to Upgrade Infrastructure

| Trigger | Current | Action | Cost Impact |
|---------|---------|--------|-------------|
| Lag > 1M sustained | 1M msg/sec | Add Kafka brokers | +$500/broker |
| Query p99 > 5s | DuckDB | Consider Presto | +$5,000-20,000 |
| Storage > 1PB | S3 Standard | Enable tiering | -30% storage |
| Stable 6+ months | On-demand | Reserved instances | -30% compute |

---

## References

- [AWS Pricing Calculator](https://calculator.aws/)
- [S3 Storage Classes](https://aws.amazon.com/s3/storage-classes/)
- [MSK Pricing](https://aws.amazon.com/msk/pricing/)

---

**Last Updated**: 2026-01-11
**Maintained By**: Platform Team
```

### 2. Demo Cost Section

Add cost awareness to demo script (Step 07 already includes this).

---

## Validation

### Acceptance Criteria

1. [ ] `docs/operations/cost-model.md` created
2. [ ] Covers baseline, production, scale costs
3. [ ] Includes optimization strategies
4. [ ] Cost monitoring metrics defined
5. [ ] Decision triggers documented

### Verification Commands

```bash
# Check document exists
ls docs/operations/cost-model.md

# Verify sections
grep -E "(Baseline|Production|Scale|Optimization)" docs/operations/cost-model.md
```

---

## Demo Talking Points

> "Let me show you the cost model.
>
> At 100x scale—1 million messages per second—monthly cost is about $15,000.
> Storage dominates at 58%, which is why we use tiered storage.
>
> Compared to Snowflake at $25K or kdb+ at $50K+, K2 is 35-40% cheaper
> with no vendor lock-in.
>
> The key optimization triggers:
> - When storage exceeds $10K, enable Glacier tiering
> - When queries slow down, add caching
> - When workload stabilizes, buy reserved instances
>
> This is how Principal Engineers think about infrastructure."

---

**Last Updated**: 2026-01-11
**Status**: ⬜ Not Started
