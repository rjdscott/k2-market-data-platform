# Runbook: Blue-Green Deployment for Binance Streaming

**Severity**: High (Production Deployment)
**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Related Phase**: [Phase 5 - Binance Production Resilience](../../phases/phase-5-binance-production-resilience/)

---

## Overview

This runbook describes the zero-downtime blue-green deployment procedure for the Binance streaming service. This deployment strategy allows us to deploy new versions with zero message loss and no service interruption.

**Total Time**: ~33 minutes
**Downtime**: 0 seconds

---

## Pre-Deployment Checklist

Before starting the deployment, verify:

- [ ] All Phase 5 P0/P1 fixes have been implemented and tested
- [ ] 24h soak test has passed successfully (Step 07)
- [ ] Code changes have been reviewed and approved
- [ ] Docker image builds successfully
- [ ] Kafka and Schema Registry are healthy
- [ ] Prometheus/Grafana dashboards are accessible
- [ ] Rollback plan is understood
- [ ] Stakeholders have been notified

---

## Deployment Architecture

### Blue-Green Strategy

```
Kafka Topic (market.crypto.trades)
    ↑                    ↑
    |                    |
[Blue Container]    [Green Container]
  (Current v1.x)      (New v2.0)
    ↓                    ↓
  Binance WebSocket Stream
```

**Key Properties**:
- Both containers produce to the same Kafka topic
- Kafka deduplication via idempotent producer (transaction IDs)
- Green is validated before blue is stopped
- Blue can be quickly restarted if green fails

---

## Deployment Procedure

### Option 1: Automated Deployment (Recommended)

Use the automated deployment script:

```bash
# Navigate to project root
cd /path/to/k2-market-data-platform

# Run blue-green deployment script
./scripts/deploy_blue_green.sh v2.0-stable
```

The script will:
1. Build the new Docker image (v2.0-stable)
2. Deploy green container alongside blue
3. Monitor both for 10 minutes
4. Stop blue container (cutover)
5. Validate green for 15 minutes

**Monitor the script output carefully.** It will log each step and any errors.

### Option 2: Manual Deployment

If you need to run the deployment manually (e.g., for troubleshooting):

#### Step 1: Build New Image (5 minutes)

```bash
# Build new image with version tag
docker build -t k2-platform:v2.0-stable .

# Verify image was created
docker images | grep k2-platform
```

**Expected Output**:
```
k2-platform   v2.0-stable   abc123def456   Just now   1.2GB
```

#### Step 2: Deploy Green Container (2 minutes)

```bash
# Set environment variables for green
export K2_IMAGE_TAG=v2.0-stable
export K2_METRICS_PORT=9092

# Scale to 2 instances (blue + green)
docker compose up -d --scale binance-stream=2 --no-recreate

# Wait for green to start
sleep 10

# Identify green container
docker ps --filter "name=binance-stream" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

**Expected Output**:
```
NAMES                       STATUS              PORTS
k2-binance-stream           Up 5 hours          ...9091...
k2-market-data-...-stream-1 Up 20 seconds       ...9092...
```

#### Step 3: Monitor Dual-Operation (10 minutes)

During this phase, both blue and green are producing to Kafka.

**Check Blue Metrics** (port 9091):
```bash
# Message count
curl -s http://localhost:9091/metrics | grep binance_messages_received_total

# Memory usage
curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes
```

**Check Green Metrics** (port 9092):
```bash
# Message count (should be increasing)
curl -s http://localhost:9092/metrics | grep binance_messages_received_total

# Memory usage (should be ~200-250MB)
curl -s http://localhost:9092/metrics | grep process_memory_rss_bytes
```

**Watch both metrics** (refreshes every 30 seconds):
```bash
watch -n 30 '
  echo "=== Blue (9091) ==="
  curl -s http://localhost:9091/metrics | grep binance_messages_received_total
  echo ""
  echo "=== Green (9092) ==="
  curl -s http://localhost:9092/metrics | grep binance_messages_received_total
'
```

**Validation Criteria** (after 10 minutes):
- ✅ Green message count is increasing (>10 msg/sec)
- ✅ Green memory is stable (~200-250MB)
- ✅ No errors in green logs
- ✅ Green health check passing

**If any validation fails**, see "Rollback Procedure" section.

#### Step 4: Cutover to Green (1 minute)

Once monitoring confirms green is healthy:

```bash
# Stop blue container gracefully
docker compose stop k2-binance-stream

# Verify blue is stopped
docker ps --filter "name=binance-stream"
```

**Expected Output** (only green running):
```
NAMES                       STATUS          PORTS
k2-market-data-...-stream-1 Up 11 minutes   ...9092...
```

#### Step 5: Validate Green (15 minutes)

After cutover, validate that green is handling all traffic:

```bash
# Check green is healthy
./scripts/validate_deployment.sh k2-market-data-platform-binance-stream-1 9092

# Monitor for 15 minutes
watch -n 60 './scripts/validate_deployment.sh k2-market-data-platform-binance-stream-1 9092'
```

**Success Criteria**:
- ✅ Container health: healthy
- ✅ Message rate: >10 msg/sec sustained
- ✅ Memory usage: 200-250MB stable
- ✅ No errors in logs
- ✅ Kafka consumer lag: <1000 messages

---

## Validation Checklist

### Immediate Validation (First 15 minutes)

- [ ] Green container is running and healthy
- [ ] Metrics endpoint accessible (http://localhost:9092/metrics)
- [ ] Message rate >10 msg/sec sustained
- [ ] Memory usage 200-250MB (not growing rapidly)
- [ ] No errors in logs (`docker logs <green-container> --tail 100`)
- [ ] Connection rotations scheduled (check after 4 hours)
- [ ] Ping-pong heartbeat active (pong timestamp updating)

### Extended Validation (Next 24 hours)

See [Step 09 - Production Validation](../../phases/phase-5-binance-production-resilience/steps/step-09-validation.md) for the full 24-hour validation procedure.

Key metrics to monitor:
- Memory growth: <50MB over 24h
- Connection rotations: ~6 per day (every 4h)
- Message rate: >10 msg/sec average
- Leak detection score: <0.5

---

## Rollback Procedure

If green fails validation at any stage, rollback to blue immediately:

### Immediate Rollback (< 2 minutes)

```bash
# Restart blue container
docker compose up -d k2-binance-stream

# Wait for blue to be healthy
sleep 30

# Verify blue is healthy
./scripts/validate_deployment.sh k2-binance-stream 9091

# Stop green container
docker compose stop k2-market-data-platform-binance-stream-1

# Or find and stop by name
docker ps --filter "name=binance-stream" --format "{{.Names}}" | grep -v "k2-binance-stream$" | xargs docker stop
```

### Post-Rollback Actions

1. **Investigate failure**: Review green container logs
   ```bash
   docker logs k2-market-data-platform-binance-stream-1 --tail 200
   ```

2. **Check metrics**: Export metrics from failed deployment
   ```bash
   curl -s http://localhost:9092/metrics > failed-deployment-metrics.txt
   ```

3. **Create incident report**: Document what went wrong
4. **Notify stakeholders**: Inform team of rollback
5. **Fix issues**: Address root cause before retrying

---

## Troubleshooting

### Green Container Won't Start

**Symptoms**:
- Green container exits immediately
- Health check never passes
- No metrics endpoint

**Diagnosis**:
```bash
# Check container status
docker ps -a --filter "name=binance-stream"

# Check logs
docker logs <green-container-name>

# Check for port conflicts
netstat -an | grep 9092
```

**Resolution**:
1. Review logs for errors
2. Verify environment variables are correct
3. Check Kafka/Schema Registry connectivity
4. Ensure port 9092 is available
5. Rollback if issue cannot be resolved quickly

### Green Not Receiving Messages

**Symptoms**:
- Green container healthy but message count = 0
- Blue receiving messages normally

**Diagnosis**:
```bash
# Check green logs for connection errors
docker logs <green-container-name> | grep -i "error\|exception"

# Check Binance connectivity
docker exec <green-container-name> curl -s https://api.binance.com/api/v3/ping
```

**Resolution**:
1. Verify Binance WebSocket URL is correct
2. Check SSL certificate verification (should be enabled)
3. Verify symbols configuration
4. Check network connectivity from container
5. Rollback if Binance connectivity issues

### Memory Growing Rapidly

**Symptoms**:
- Green memory exceeds 300MB within first hour
- Memory leak score >0.8

**Diagnosis**:
```bash
# Check current memory
curl -s http://localhost:9092/metrics | grep process_memory_rss_bytes

# Check leak detection score
curl -s http://localhost:9092/metrics | grep memory_leak_detection_score

# Check serializer cache size
curl -s http://localhost:9092/metrics | grep serializer_cache_size
```

**Resolution**:
1. Verify bounded serializer cache is working (max 10 entries)
2. Check connection rotation is scheduled (every 4h)
3. Review memory samples in logs
4. **CRITICAL**: If memory exceeds 400MB, rollback immediately
5. Run 1h validation test before retrying deployment

### Kafka Producer Errors

**Symptoms**:
- Green receiving messages but not producing to Kafka
- Kafka produce errors increasing

**Diagnosis**:
```bash
# Check Kafka producer errors
curl -s http://localhost:9092/metrics | grep kafka_produce_errors_total

# Check Kafka connectivity
docker exec <green-container-name> telnet kafka 29092
```

**Resolution**:
1. Verify Kafka bootstrap servers configuration
2. Check Schema Registry connectivity
3. Verify topic exists and is accessible
4. Check producer idempotence configuration
5. Review Kafka broker logs for errors

---

## Post-Deployment Actions

After successful deployment:

1. **Update monitoring dashboards**
   - Change container name in Grafana queries (if needed)
   - Verify alerts are firing correctly

2. **Document deployment**
   - Record deployment time and version
   - Note any issues encountered
   - Update [Phase 5 PROGRESS.md](../../phases/phase-5-binance-production-resilience/PROGRESS.md)

3. **Notify stakeholders**
   - Deployment complete
   - New version deployed successfully
   - Monitoring ongoing

4. **Begin 24h validation** (Step 09)
   - See [Production Validation Guide](../../phases/phase-5-binance-production-resilience/steps/step-09-validation.md)

5. **Clean up old images** (after 7 days)
   ```bash
   # Remove old blue image (after green proven stable)
   docker rmi k2-platform:v1.x-old
   ```

---

## Related Documentation

- [Phase 5 Implementation Plan](../../phases/phase-5-binance-production-resilience/IMPLEMENTATION_PLAN.md)
- [Step 08 - Blue-Green Deployment](../../phases/phase-5-binance-production-resilience/steps/step-08-deployment.md)
- [Step 09 - Production Validation](../../phases/phase-5-binance-production-resilience/steps/step-09-validation.md)
- [Binance Streaming Runbook](./binance-streaming.md)

---

## Quick Reference Commands

```bash
# Deploy (automated)
./scripts/deploy_blue_green.sh v2.0-stable

# Validate deployment
./scripts/validate_deployment.sh <container-name> <metrics-port>

# Monitor metrics
watch -n 60 'curl -s http://localhost:9092/metrics | grep -E "binance_messages|process_memory"'

# Check logs
docker logs <container-name> --tail 100 -f

# Rollback
docker compose up -d k2-binance-stream && docker stop <green-container-name>
```

---

**Last Updated**: 2026-01-15
**Next Review**: After first production deployment
**Feedback**: Report issues to Engineering Team
