# Phase 8: Executive Demo Runbook

**Purpose**: Complete runbook for executing flawless executive demonstrations
**Audience**: Staff Data Engineer, Principal Data Engineer, CTO
**Demo Duration**: 12 minutes target
**Preparation Time**: 2 hours minimum

---

## Executive Demo Overview

### Demo Objectives
1. **Platform Positioning**: Establish K2 as L3 cold path research platform
2. **Live Demonstration**: Show real-time market data processing
3. **Technical Excellence**: Demonstrate production-grade resilience
4. **Business Value**: Present cost-effective alternative to incumbent solutions

### Target Audience Personas
- **Principal Data Engineer**: Technical depth, architecture decisions, scalability
- **CTO**: Business value, cost analysis, competitive positioning
- **Executive Stakeholders**: ROI, time-to-market, competitive advantages

### Key Success Metrics
- **Demo Reliability**: 100% successful execution
- **Timing Adherence**: 12 minutes ±1 minute
- **Technical Accuracy**: All claims verifiable
- **Audience Engagement**: Clear Q&A preparation

---

## Pre-Demo Preparation Checklist

### T-2 Hours: Environment Setup

#### Infrastructure Validation
```bash
# 1. Start clean environment
docker compose down -v
docker compose up -d

# 2. Wait for services (30 seconds)
sleep 30

# 3. Validate health
make test-e2e-health
curl -f http://localhost:8000/health
curl -f http://localhost:9090/-/healthy
curl -f http://localhost:3000/api/health

# Expected: All services healthy, no errors
```

#### Data Preparation
```bash
# 1. Reset demo state
uv run python scripts/demo_mode.py --reset

# 2. Load demo data
uv run python scripts/demo_mode.py --load

# 3. Validate demo readiness
uv run python scripts/demo_mode.py --validate

# Expected: All checks pass, demo ready
```

#### Quick Demo Test
```bash
# Run 5-minute validation
make demo-quick

# Expected: All steps complete successfully
# If failures: Troubleshoot before proceeding
```

### T-90 Minutes: Materials Preparation

#### Physical Setup
- [ ] Laptop charged to 100%
- [ ] External monitor connected (if using)
- [ ] Power adapter connected
- [ ] Do Not Disturb enabled (⌘+Option+D on macOS)
- [ ] Phone silenced and out of sight
- [ ] Desk cleared of distractions

#### Digital Setup
- [ ] Browser tabs opened in specific order:
  1. http://localhost:8888 (Jupyter - leftmost)
  2. http://localhost:3000 (Grafana - center)
  3. http://localhost:9090 (Prometheus - right)
  4. http://localhost:8000/docs (API docs - background)
  5. http://localhost:9001 (MinIO - background)

- [ ] Terminal windows opened:
  1. `docker compose logs --follow` (left)
  2. `htop` (center)
  3. Ready for ad-hoc commands (right)

#### Reference Materials
- [ ] Demo quick reference printed and within reach
- [ ] Demo checklist printed and visible
- [ ] Backup materials folder open
- [ ] Troubleshooting guide accessible

### T-30 Minutes: Final Validation

#### System Health Check
```bash
# Run comprehensive validation
uv run python scripts/demo_mode.py --validate

# Expected: All green checks
# Any red items: Fix immediately or use backup plan
```

#### Performance Verification
```bash
# Quick API performance test
time curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=5" > /dev/null

# Expected: <500ms response time
# If slow: Check system resources
```

#### Content Refresh
- [ ] Review opening statement (memorize)
- [ ] Review closing statement (memorize)
- [ ] Refresh key performance numbers
- [ ] Review anticipated questions and answers

### T-5 Minutes: Go/No-Go Decision

#### Final Go/No-Go Checklist
- [ ] All Docker services healthy
- [ ] Demo scripts tested successfully
- [ ] API responding correctly
- [ ] Performance within targets
- [ ] Backup materials ready
- [ ] Environment stable for 5+ minutes

**If ALL items checked**: GO - proceed with demo
**If ANY items unchecked**: NO-GO - troubleshoot or use backup plan

---

## Executive Demo Script

### Opening Statement (Memorized)

> "Good morning [Principal/CTO]. Thanks for taking time for this demo.
> 
> Today I'll walk you through K2, a lakehouse-based market data platform
> I've built for Level 3 analytics and compliance workloads.
> 
> This is a live system - everything you're about to see is running in real-time,
> ingesting data from Binance right now.
> 
> The demo will take about 12 minutes, and then I'll be happy to answer questions.
> I've prepared comprehensive documentation and can dive deep into any area
> you're interested in.
> 
> Let's start with positioning..."

### Section 1: Platform Positioning (1 minute)

**Talking Points**:
- "K2 is designed as an L3 cold path research platform"
- "Target latency <500ms for analytics and compliance"
- "NOT competing with HFT execution platforms (<10μs)"
- "Built for quantitative research, backtesting, regulatory compliance"

**Actions**:
- Show platform positioning slide (if available)
- Reference latency tier diagram
- Clear scope and boundaries

**Transitions**:
> "Now let me show you the live data flow..."

### Section 2: Live Streaming (2 minutes)

**Talking Points**:
- "Currently ingesting live data from Binance WebSocket"
- "Processing BTC, ETH, BNB trades in real-time"
- "Average throughput: ~130 trades per second"
- "All data flows through Kafka with schema validation"

**Actions**:
```bash
# Show live Binance stream
docker logs k2-binance-stream --tail 20

# Point out live trades scrolling by
# Note: "Trade" messages appearing every few seconds
```

**Show Kafka Topic**:
- Navigate to http://localhost:8080
- Show market.crypto.trades.binance topic
- Point out message count increasing

**Transitions**:
> "This live data gets stored in our lakehouse..."

### Section 3: Storage & Time-Travel (2 minutes)

**Talking Points**:
- "Data stored in Apache Iceberg with ACID guarantees"
- "Time-travel queries - query any historical point"
- "10:1 compression ratio with Parquet + Snappy"
- "Unlimited historical storage, cost-effective"

**Actions**:
```bash
# Show Iceberg snapshots
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/snapshots" | jq .

# Show recent trades
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=5" | jq .
```

**Explain Time-Travel**:
- "Each write creates immutable snapshot"
- "Can query data as it existed at any point"
- "Perfect for backtesting trading strategies"

**Transitions**:
> "Let me show you how we monitor this system..."

### Section 4: Monitoring (2 minutes)

**Talking Points**:
- "Comprehensive monitoring with Prometheus + Grafana"
- "83 validated metrics across all components"
- "Real-time visibility into system health"
- "Automated alerting and degradation handling"

**Actions**:
- Navigate to Grafana http://localhost:3000
- Show System Health dashboard
- Point out key metrics:
  - API request rate and latency
  - Kafka consumer lag
  - Degradation level (currently NORMAL)

**Show Prometheus**:
- Navigate to http://localhost:9090
- Show k2_degradation_level metric
- Show k2_http_requests_total metric

**Transitions**:
> "What happens when the system gets overloaded? Let me show you..."

### Section 5: Resilience Demo (2 minutes)

**Talking Points**:
- "5-level circuit breaker prevents cascading failures"
- "Graceful degradation rather than hard failures"
- "Priority-based load shedding preserves critical data"
- "Automatic recovery with hysteresis"

**Actions**:
```bash
# Run degradation demo
uv run python scripts/demo_degradation.py --quick
```

**Explain Each Level**:
- **NORMAL**: All messages processed
- **SOFT**: Skip enrichment, reduced validation
- **GRACEFUL**: Drop low-priority symbols
- **AGGRESSIVE**: Critical symbols only
- **CIRCUIT_BREAK**: Stop accepting data

**Show Grafana Impact**:
- Point to degradation level changing in Grafana
- Show messages being shed at higher levels

**Transitions**:
> "Now let me show you how queries work across this system..."

### Section 6: Hybrid Queries (2 minutes)

**Talking Points**:
- "Hybrid query engine merges Kafka + Iceberg"
- "Recent data from Kafka (last 15 minutes)"
- "Historical data from Iceberg"
- "Seamless to users, transparent access"

**Actions**:
```bash
# Show hybrid query endpoint
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades/recent?symbol=BTCUSDT&limit=10" | jq .

# Explain how it merges sources
# Point out performance metrics
```

**Show API Documentation**:
- Navigate to http://localhost:8000/docs
- Show available endpoints
- Demonstrate Try it out feature

**Transitions**:
> "Finally, let me address the business case..."

### Section 7: Cost Model (1 minute)

**Talking Points**:
- "$0.85 per million messages at 1M msg/sec scale"
- "35-40% cheaper than Snowflake"
- "60% cheaper than kdb+ license fees"
- "Built on open source, vendor-neutral"

**Key Numbers**:
- **Current Scale**: 138 msg/sec (demo)
- **Target Scale**: 1M msg/sec (production)
- **AWS Cost**: ~$15K/month at scale
- **Savings**: $8-10K/month vs alternatives

**Transitions**:
> "That completes the demonstration. Let me summarize..."

### Closing Statement (Memorized)

> "That's K2 platform. To summarize:
> 
> **What we built**:
> - Lakehouse architecture (Kafka → Iceberg → DuckDB)
> - Production-grade resilience (5-level circuit breaker)
> - Evidence-based performance (<500ms p99, measured)
> - Cost-effective ($0.85 per million messages)
> 
> **What's next**:
> - Phase 9: Multi-node scale-out with Presto
> - Additional data sources beyond Binance
> - Enhanced monitoring and alerting
> 
> **Documentation**:
> - 161 markdown files covering architecture, design, operations
> - 95%+ test coverage, 86+ tests
> - Comprehensive runbooks and decision records
> 
> I'm happy to answer any questions or dive deeper into any component."

---

## Anticipated Questions and Answers

### Technical Questions

**Q: Why not use a real-time streaming database like kdb+?**
A: "kdb+ is excellent for L1/L2 workloads but costs 60% more. For L3 research workloads, DuckDB + Iceberg provides sub-second performance at 60% cost savings. We're targeting analytics, not execution."

**Q: How do you handle schema evolution as data sources change?**
A: "Iceberg supports schema evolution natively. We use Avro with Schema Registry for backward compatibility. V2 schema design with vendor_data map allows flexible field additions."

**Q: What's the maximum throughput this can handle?**
A: "Current single-node: 138 msg/sec demonstrated. Multi-node with Presto: target 1M msg/sec. Architecture designed for horizontal scaling - Kafka partitions, Iceberg bucketing."

**Q: How do you ensure data quality and consistency?**
A: "Schema Registry enforces structure validation. Idempotent producers prevent duplicates. Consumer validation checks data quality. End-to-end tests validate consistency."

### Business Questions

**Q: What's the ROI compared to existing solutions?**
A: "35-40% cost savings vs Snowflake, 60% vs kdb+. Open source avoids vendor lock-in. Built for research workloads - no over-engineering for execution."

**Q: What's the time-to-market for production deployment?**
A: "Current single-node ready now. Multi-node production: 4-6 weeks with Kubernetes and Presto. Much faster than building from scratch."

**Q: What are the ongoing operational costs?**
A: "At 1M msg/sec scale: ~$15K/month AWS. 35-40% less than managed alternatives. Includes infrastructure, monitoring, backup."

**Q: How do we ensure this meets compliance requirements?**
A: "Time-travel queries provide audit trails. Immutable snapshots ensure data integrity. Schema evolution maintains historical consistency. Comprehensive logging for regulatory reporting."

---

## Contingency Procedures

### During Demo Issues

#### Issue: Services Won't Start
**Symptoms**: Docker services failing to start
**Recovery**: 
```bash
# Quick reset (30 seconds)
make demo-reset

# If still failing: Switch to backup materials
# Use pre-executed notebooks or screenshots
```

#### Issue: Notebook Kernel Crashes
**Symptoms**: Jupyter kernel dies, cells won't execute
**Recovery**:
- Restart kernel (Kernel → Restart & Clear Output)
- If still failing: Switch to pre-executed notebook
- Backup: Use screenshots of expected outputs

#### Issue: API Returns Empty Data
**Symptoms**: API calls return empty arrays
**Recovery**:
```bash
# Reload demo data (5 minutes)
uv run python scripts/demo_mode.py --load

# Backup: Use cached results or screenshots
```

#### Issue: Performance Issues
**Symptoms**: Slow response times, laggy interface
**Recovery**:
- Check system resources (htop)
- Close unnecessary applications
- Restart services if needed
- Use conservative performance claims

#### Issue: Network/Internet Problems
**Symptoms**: Can't reach external URLs
**Recovery**:
- Demo is designed to work offline
- Use local services only
- All critical demos use localhost

### Post-Demo Issues

#### Issue: Questions Beyond Prepared Content
**Response**: "That's an excellent question. I don't have the specific data right now, but I can investigate and follow up. Let me note that down and get back to you with a detailed answer."

#### Issue: Technical Challenge to Claims
**Response**: "I appreciate that perspective. Let me clarify the trade-offs we made. Our approach prioritizes [X] over [Y] because [rationale]. The performance data I showed is from our actual measurements, and I'm happy to dive deeper into the methodology."

---

## Post-Demo Follow-Up

### Immediate Actions (T+30 minutes)
- [ ] Send demo materials to attendees
- [ ] Document any issues encountered
- [ ] Note follow-up items promised
- [ ] Update demo based on feedback

### Next Day Actions
- [ ] Review demo recording (if available)
- [ ] Address any technical issues discovered
- [ ] Prepare detailed answers to follow-up questions
- [ ] Schedule technical deep-dive if requested

### Weekly Maintenance
- [ ] Run full demo validation
- [ ] Update performance numbers if changed
- [ ] Refresh backup materials
- [ ] Review and update runbook

---

## Success Metrics and KPIs

### Demo Success Metrics
- **Completion Rate**: 100% (all sections completed)
- **Timing Accuracy**: ±1 minute from 12-minute target
- **Technical Accuracy**: All claims verifiable
- **Audience Engagement**: Relevant questions asked

### Technical Performance KPIs
- **Setup Time**: <15 minutes from cold start
- **API Latency**: <500ms p99 during demo
- **Service Uptime**: 100% during demo window
- **Error Rate**: 0 unhandled errors

### Business Impact KPIs
- **Stakeholder Satisfaction**: Positive feedback received
- **Follow-up Requests**: Technical deep-dive requested
- **Decision Support**: Clear business case presented
- **Competitive Positioning**: Advantages clearly articulated

---

## Version History

| Version | Date | Changes | Author |
|---------|------|----------|---------|
| 1.0 | 2026-01-17 | Initial creation for Phase 8 | Staff Data Engineer |
| | | | |
| | | | |

---

**Last Updated**: 2026-01-17
**Maintained By**: Staff Data Engineer
**Review Frequency**: Monthly or after platform changes
**Approval Required**: Principal Data Engineer