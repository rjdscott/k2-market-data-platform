# Runbook: Kafka Checkpoint File Corruption Recovery

**Severity**: High
**Last Updated**: 2026-01-13
**Maintained By**: Platform Team

## Symptoms

- Kafka broker fails to start with exit code 1
- Docker logs show: `ERROR Error while reading checkpoint file /var/lib/kafka/data/replication-offset-checkpoint`
- Error message: `java.io.IOException: Malformed line in checkpoint file`
- Final message: `ERROR Shutdown broker because all log dirs in /var/lib/kafka/data have failed`
- Schema Registry cannot start (depends on healthy Kafka broker)

## Root Cause

Kafka checkpoint files can become corrupted due to:
1. Unclean shutdown (Docker killed while Kafka was writing checkpoints)
2. Disk full during checkpoint write
3. File system issues
4. Bug in Kafka checkpoint writer

Checkpoint files track:
- `replication-offset-checkpoint`: High water mark for topic partitions
- `recovery-point-offset-checkpoint`: Recovery point for crash recovery
- `log-start-offset-checkpoint`: Log start offset for topics

## Diagnosis

### Step 1: Check Docker Compose Status
```bash
docker compose ps
# Expected: k2-kafka shows "Exited (1)"
```

### Step 2: Check Kafka Logs
```bash
docker compose logs kafka --tail 50
# Look for: "ERROR Error while reading checkpoint file"
# Look for: "Malformed line in checkpoint file"
```

### Step 3: Confirm Checkpoint Corruption
```bash
docker compose logs kafka | grep "checkpoint"
# If you see IOException related to checkpoint files, corruption is confirmed
```

## Resolution

### Option 1: Full Data Reset (Development/Demo Environment)

**⚠️ WARNING**: This deletes ALL Kafka data (topics, messages, offsets).

**When to use**: Development, demo, or testing environments where data loss is acceptable.

```bash
# Step 1: Stop all services
docker compose down

# Step 2: Remove Kafka data volume
docker volume rm k2-market-data-platform_kafka-data

# Step 3: Start services
docker compose up -d

# Step 4: Verify Kafka is healthy
docker compose ps kafka
# Expected: Up (healthy)

# Step 5: Verify Schema Registry is healthy
docker compose ps schema-registry-1
# Expected: Up (healthy)
```

**Recovery Time**: 2-3 minutes (time for services to start and become healthy)

### Option 2: Checkpoint File Repair (Production Environment)

**When to use**: Production environment where data must be preserved.

```bash
# Step 1: Stop Kafka (but keep data)
docker compose stop kafka

# Step 2: Start a temporary container to access Kafka data volume
docker run --rm -it \
  -v k2-market-data-platform_kafka-data:/data \
  alpine:latest sh

# Step 3: Inside the container, delete corrupted checkpoint files
rm -f /data/replication-offset-checkpoint
rm -f /data/recovery-point-offset-checkpoint
rm -f /data/log-start-offset-checkpoint

# Step 4: Exit the container
exit

# Step 5: Start Kafka (it will rebuild checkpoint files)
docker compose start kafka

# Step 6: Monitor Kafka startup
docker compose logs kafka -f
# Wait for: "Kafka Server started"

# Step 7: Verify health
docker compose ps kafka
# Expected: Up (healthy)
```

**Recovery Time**: 3-5 minutes (Kafka rebuilds checkpoint files from log segments)

**Note**: Kafka will rebuild checkpoint files by scanning all log segments. This is I/O intensive and may take longer for large data volumes.

### Option 3: Restore from Backup (Production with Backups)

**When to use**: Production environment with Kafka data backups.

```bash
# Step 1: Stop Kafka
docker compose stop kafka

# Step 2: Restore Kafka data from backup
# (Backup/restore procedures depend on your backup solution)
# Example with volume backup:
docker run --rm \
  -v k2-market-data-platform_kafka-data:/data \
  -v /path/to/backups:/backups \
  alpine:latest sh -c "cd /data && tar xzf /backups/kafka-data-latest.tar.gz"

# Step 3: Start Kafka
docker compose start kafka

# Step 4: Verify health
docker compose ps kafka
```

## Prevention

### 1. Enable Graceful Shutdown
Always use `docker compose down` instead of `docker compose kill`:
```bash
# GOOD: Graceful shutdown
docker compose down

# BAD: Force kill (can corrupt checkpoints)
docker compose kill
```

### 2. Configure Kafka for Better Reliability
In `docker-compose.yml`, consider these settings:
```yaml
environment:
  # Flush log segments more frequently (trade performance for durability)
  KAFKA_LOG_FLUSH_INTERVAL_MESSAGES: 10000
  KAFKA_LOG_FLUSH_INTERVAL_MS: 1000

  # Reduce checkpoint interval (more frequent, smaller risk of corruption)
  KAFKA_LOG_FLUSH_SCHEDULER_INTERVAL_MS: 3000
```

### 3. Regular Backups
For production, implement regular Kafka data backups:
```bash
# Example: Daily backup of Kafka data volume
docker run --rm \
  -v k2-market-data-platform_kafka-data:/data:ro \
  -v /path/to/backups:/backups \
  alpine:latest sh -c "cd /data && tar czf /backups/kafka-data-$(date +%Y%m%d).tar.gz ."
```

### 4. Monitor Disk Space
Ensure sufficient disk space for Kafka data:
```bash
# Check disk usage
df -h

# Check Kafka volume size
docker system df -v | grep kafka
```

## Post-Incident Actions

After resolving the issue:

- [ ] Update this runbook if steps changed
- [ ] Review Docker host shutdown procedures
- [ ] Consider implementing automated health checks
- [ ] Add alerting for Kafka startup failures (Prometheus + Grafana)
- [ ] If recurring, investigate underlying file system or disk issues

## Related Monitoring

- **Dashboard**: Grafana Kafka Overview Dashboard
- **Alert**: `KafkaBrokerDown` - Fires when Kafka broker is not responding
- **Metrics**:
  - `kafka_server_kafkaserver_brokerstate` - Should be 3 (RUNNING)
  - `kafka_log_log_size` - Monitor for unexpected drops (indicates data loss)

## References

- [Kafka Operations Documentation](https://kafka.apache.org/documentation/#operations)
- [Kafka Storage Internals](https://kafka.apache.org/documentation/#design_filesystem)
- [Docker Compose Down vs Kill](https://docs.docker.com/compose/reference/)

## Troubleshooting Tips

### If Kafka Still Won't Start After Checkpoint Deletion

1. **Check disk space**: `df -h`
2. **Check file permissions**: Kafka data directory must be writable
3. **Check Docker logs for other errors**: `docker compose logs kafka`
4. **Verify Docker network**: `docker network ls | grep k2-network`
5. **Check port conflicts**: `lsof -i :9092`

### If Data Loss Occurred

1. **Restore from backup** (if available)
2. **Re-ingest historical data** from source systems
3. **Replay from upstream Kafka cluster** (if using mirror maker)

## Version History

- **2026-01-13**: Initial version - Documented checkpoint corruption recovery procedures
