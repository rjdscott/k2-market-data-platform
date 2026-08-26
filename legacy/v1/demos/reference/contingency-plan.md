# Demo Contingency Plan

**Purpose**: Failure scenarios and recovery strategies
**Last Updated**: 2026-01-14
**Recovery Time Objective**: <30 seconds for most scenarios

---

## Failure Scenarios & Responses

### Scenario 1: Services Won't Start

**Symptoms**:
- Docker compose fails
- Services in restart loop
- "Container ... is unhealthy" messages

**Diagnosis**:
```bash
docker compose ps  # Check service status
docker compose logs <service>  # Check error logs
```

**Response** (Recovery Time: 30 seconds):

**Immediate Action**:
1. Switch to recorded demo video (if available)
2. Open: `docs/phases/phase-4-demo-readiness/reference/demo-recording.mp4`
3. Narrate: "Let me show you the recorded execution while we troubleshoot..."

**Alternative** (if no video):
1. Switch to pre-executed notebook
2. Open: `notebooks/binance-demo-with-outputs.ipynb`
3. Walk through outputs without re-running

**Backup Materials**:
- ✅ Recorded demo video (to be created)
- ✅ Pre-executed notebook (to be created before demo)
- ✅ Screenshots (`docs/phases/phase-4-demo-readiness/reference/screenshots/`)

**Post-Demo Investigation**:
```bash
# Check logs for errors
docker compose logs --tail=100

# Restart services
docker compose down -v
docker compose up -d
```

---

### Scenario 2: Binance Stream Not Ingesting

**Symptoms**:
- No recent trades in logs
- Queries return empty results
- Stream container shows "connected" but no data

**Diagnosis**:
```bash
# Check stream logs
docker logs k2-binance-stream --tail 50

# Check for recent trades
docker logs k2-binance-stream | grep "Trade received" | tail -5

# Check Kafka for messages
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance \
  --max-messages 5
```

**Response** (Recovery Time: 30 seconds):

**Immediate Action**:
1. Switch to pre-executed notebook
2. Open: `notebooks/binance-demo-with-outputs.ipynb`
3. Narrate: "Here are the results from a previous execution with live data..."
4. Walk through outputs without re-running cells

**Alternative**:
1. Use static screenshots
2. Show: `docs/phases/phase-4-demo-readiness/reference/screenshots/`
3. Narrate each screenshot

**Backup Materials**:
- ✅ Pre-executed notebook with outputs
- ✅ Screenshots of Binance stream logs

**Post-Demo Fix**:
```bash
# Restart stream
docker restart k2-binance-stream

# Wait 30 seconds
sleep 30

# Verify ingestion
docker logs k2-binance-stream --follow
```

---

### Scenario 3: Jupyter Kernel Crashes

**Symptoms**:
- Notebook freezes
- Kernel dies mid-execution
- Cannot execute cells
- "Kernel Restarting" message

**Diagnosis**:
- Check notebook server logs
- Look for memory errors or Python exceptions

**Response** (Recovery Time: 30 seconds):

**Immediate Action**:
1. **Do NOT attempt to restart kernel** (wastes time)
2. Switch immediately to pre-executed notebook
3. Open: `notebooks/binance-demo-with-outputs.ipynb`
4. Walk through outputs (no execution needed)
5. Show terminal windows as proof of life

**Terminal Windows to Have Ready**:
```bash
# Terminal 1: Docker services
docker compose ps

# Terminal 2: Binance stream logs
docker logs k2-binance-stream --follow

# Terminal 3: System resources
htop  # or top on macOS
```

**Backup Materials**:
- ✅ Pre-executed notebook with all outputs
- ✅ Terminal windows showing services running
- ✅ Screenshots of key outputs

**Why This Works**:
- Pre-executed notebook has all outputs captured
- No need to re-run cells
- Terminal windows prove infrastructure is live
- Recovery is instant (30 seconds)

---

### Scenario 4: Network Issues

**Symptoms**:
- Cannot reach localhost services
- API timeouts (curl fails)
- Browser cannot connect to Grafana/Prometheus
- ERR_CONNECTION_REFUSED

**Diagnosis**:
```bash
# Check if services listening on ports
lsof -i :8000  # API
lsof -i :3000  # Grafana
lsof -i :9090  # Prometheus

# Test connectivity
curl http://localhost:8000/health
curl http://localhost:3000/api/health
curl http://localhost:9090/-/healthy
```

**Response** (Recovery Time: 30 seconds):

**Immediate Action**:
1. Switch to static screenshots
2. Open: `docs/phases/phase-4-demo-readiness/reference/screenshots/`
3. Walk through screenshots in sequence (01 → 10)
4. Narrate each screenshot as if it were live

**Alternative**:
1. Switch to recorded demo video (if available)
2. Play video with audio narration

**Backup Materials**:
- ✅ 10 static screenshots (high resolution)
- ✅ Recorded demo video
- ✅ Pre-executed notebook

**Post-Demo Fix**:
- Restart Docker services
- Check firewall/VPN settings
- Verify port forwarding

---

### Scenario 5: Query Returns Empty

**Symptoms**:
- Iceberg queries return 0 rows
- API returns empty data array
- Insufficient data accumulated
- "No data available" in visualizations

**Diagnosis**:
```bash
# Check data in Iceberg
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq '.data | length'

# Should return >0

# Check Binance stream uptime
docker ps | grep k2-binance-stream
```

**Response**:

**Option A** (if time available - 5 minutes):
```bash
# Run demo mode to ensure data
python scripts/demo_mode.py --reset --load
# Wait 5-10 minutes for data accumulation
```

**Option B** (if time-constrained - 30 seconds):
1. Switch to pre-executed notebook
2. Open: `notebooks/binance-demo-with-outputs.ipynb`
3. Explain: "In production, we'd have millions of messages..."
4. Show outputs from previous run with full data

**Backup Materials**:
- ✅ Demo mode script (`scripts/demo_mode.py`)
- ✅ Pre-executed notebook with data
- ✅ Screenshots showing data-rich queries

**Prevention**:
- Run full validation 30 minutes before demo
- Ensure stream has been running for 15+ minutes
- Verify >1000 messages in Iceberg before starting

---

### Scenario 6: Grafana Dashboard Empty

**Symptoms**:
- Grafana loads but no data visible
- Panels show "No data"
- Metrics queries return empty

**Diagnosis**:
```bash
# Check Prometheus metrics
curl http://localhost:9090/api/v1/query?query=k2_kafka_messages_produced_total

# Check Prometheus targets
open http://localhost:9090/targets
```

**Response** (Recovery Time: 30 seconds):

**Immediate Action**:
1. Show screenshot of working Grafana dashboard
2. File: `screenshots/06-grafana-degradation.png`
3. Explain: "Here's what the dashboard looks like with data..."

**Alternative**:
- Show Prometheus UI directly
- Query specific metrics in Prometheus
- Show raw metric values

**Post-Demo Fix**:
- Verify Prometheus scraping
- Check metric endpoints
- Restart Prometheus if needed

---

## Pre-Demo Checklist (30 min before)

Run this checklist **30 minutes before demo** to catch issues early:

### Infrastructure Checks
- [ ] **Services running**: `docker compose ps` - all show "Up"
- [ ] **Binance stream active**: `docker logs k2-binance-stream --tail 20` - recent trades
- [ ] **Kafka has messages**: Kafka UI shows >100 messages
- [ ] **Iceberg has data**: `curl http://localhost:8000/v1/symbols` returns >0
- [ ] **Prometheus healthy**: `curl http://localhost:9090/-/healthy`
- [ ] **Grafana healthy**: `curl http://localhost:3000/api/health`

### Data Validation
- [ ] **Data accumulation**: >1000 messages in Iceberg
- [ ] **Time range**: Data spans 15+ minutes
- [ ] **Symbols**: BTCUSDT and ETHUSDT both present
- [ ] **Recent data**: Latest timestamp <5 minutes old

### Validation Script
```bash
# Run automated validation
uv run python scripts/demo_mode.py --validate

# Should pass all checks
# If any fail → investigate immediately
```

### Demo Materials Ready
- [ ] **Quick reference**: `demo-quick-reference.md` open in browser
- [ ] **Architecture decisions**: `architecture-decisions-summary.md` open
- [ ] **Backup materials**:
  - [ ] Recorded demo video (if created) accessible
  - [ ] Pre-executed notebook open in browser tab
  - [ ] Screenshots folder open in Finder
  - [ ] Demo mode script tested

### Browser Tabs Open
- [ ] Grafana: http://localhost:3000
- [ ] Prometheus: http://localhost:9090
- [ ] API Docs: http://localhost:8000/docs
- [ ] Kafka UI: http://localhost:8080
- [ ] Quick reference (local file)

### Terminal Windows Ready
- [ ] Terminal 1: `docker compose logs --follow`
- [ ] Terminal 2: `docker logs k2-binance-stream --follow`
- [ ] Terminal 3: `htop` or `top` (resource monitoring)
- [ ] Terminal 4: Spare for ad-hoc commands

### Environment Optimization
- [ ] Laptop charged (100%)
- [ ] Power adapter connected
- [ ] Do Not Disturb enabled (macOS: Cmd+Shift+D)
- [ ] Notifications disabled
- [ ] Close unnecessary applications
- [ ] WiFi/Ethernet stable (run speed test)
- [ ] Screen resolution set correctly
- [ ] Night Shift/Dark Mode off (screenshots)

---

## Recovery Time Objectives

| Failure Scenario | Recovery Action | Time to Resume | Complexity |
|------------------|-----------------|----------------|------------|
| Services down | Switch to recorded video | 30 seconds | Low |
| Stream not ingesting | Switch to pre-executed notebook | 30 seconds | Low |
| Kernel crash | Switch to pre-executed notebook | 30 seconds | Low |
| Network issues | Use static screenshots | 30 seconds | Low |
| Empty queries | Demo mode --load OR pre-exec notebook | 5 min / 30 sec | Medium |
| Grafana empty | Show screenshots OR Prometheus | 30 seconds | Low |
| Services restart | Wait for healthy | 2-3 minutes | Medium |
| Full system failure | Recorded video + screenshots | 60 seconds | High |

---

## Emergency Procedures

### If Everything Fails (Nuclear Option)

**Response** (Recovery Time: 60 seconds):

1. **Stop attempting live demo** (acknowledge failure gracefully)
2. **Switch to recorded video** (if available)
3. **Use static screenshots** for key points
4. **Narrate architecture** from memory using quick reference
5. **Show code** in IDE instead of running execution

**Talking Points**:
> "I've encountered a technical issue with the live demo environment.
> Rather than spend time troubleshooting, let me show you the recorded
> execution and walk through the architecture using these materials.
>
> This is exactly why we prepare backup plans for production deployments."

### Contact Information

- **Docker Issues**: Check logs first, restart as last resort
- **Jupyter Issues**: Use pre-executed notebook immediately
- **Data Issues**: Run `demo_mode.py --validate` to diagnose

---

## Backup Material Inventory

### Available Now ✅
- `scripts/demo_mode.py` - Reset and validation script
- `docs/phases/phase-4-demo-readiness/reference/contingency-plan.md` - This document
- `docs/phases/phase-4-demo-readiness/reference/screenshots/` - Directory structure

### To Create Before Demo Day ⚠️
- `demo-recording.mp4` - Recorded demo video (10-12 minutes)
- `notebooks/binance-demo-with-outputs.ipynb` - Pre-executed notebook
- `screenshots/*.png` - 10 static screenshots

### Recommended: Create 2 Hours Before Demo
Run these commands **2 hours before presentation**:

```bash
# 1. Execute notebook with outputs (if services running)
jupyter nbconvert --to notebook --execute \
  notebooks/binance-demo.ipynb \
  --output binance-demo-with-outputs.ipynb \
  --ExecutePreprocessor.timeout=600

# 2. Capture screenshots (manual - see screenshots/README.md)

# 3. Record demo video (manual - 10-12 minutes)
#    - Use QuickTime (macOS) or OBS Studio
#    - Full screen capture with audio narration
#    - Save as demo-recording.mp4

# 4. Validate everything
python scripts/demo_mode.py --validate
```

---

## Lessons Learned Template

After demo (whether success or failure), document lessons:

```markdown
## Demo Post-Mortem - [Date]

### What Went Well
- ...

### What Went Wrong
- ...

### What We Used
- [ ] Live demo (no issues)
- [ ] Recorded video
- [ ] Pre-executed notebook
- [ ] Screenshots
- [ ] Demo mode script

### Improvements for Next Time
- ...

### Updated Contingency Plan
- ...
```

---

**Last Updated**: 2026-01-14
**Status**: Plan documented, manual artifacts to be created before demo day
**Review**: Update this plan after each rehearsal or actual demo
