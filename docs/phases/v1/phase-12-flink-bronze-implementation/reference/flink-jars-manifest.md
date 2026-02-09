# Flink Connector JARs Manifest

**Flink Version**: 1.19.1
**Iceberg Version**: 1.5.0
**Last Updated**: 2026-01-20

---

## Required JARs (4 files)

All JARs should be downloaded to: `/flink-jars/` directory

### 1. Kafka SQL Connector
**File**: `flink-sql-connector-kafka-3.1.0-1.19.jar`
**Size**: ~12 MB
**Purpose**: Kafka source/sink connector for Flink SQL
**Maven Coordinates**: `org.apache.flink:flink-sql-connector-kafka:3.1.0-1.19`

**Download**:
```bash
cd flink-jars
curl -O https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.1.0-1.19/flink-sql-connector-kafka-3.1.0-1.19.jar
```

**Verification**:
```bash
sha256sum flink-sql-connector-kafka-3.1.0-1.19.jar
# Expected: Verify against Maven Central hash
```

---

### 2. Iceberg Flink Runtime
**File**: `iceberg-flink-runtime-1.19-1.5.0.jar`
**Size**: ~200 MB
**Purpose**: Apache Iceberg connector for Flink 1.19 (includes all dependencies)
**Maven Coordinates**: `org.apache.iceberg:iceberg-flink-runtime-1.19:1.5.0`

**Download**:
```bash
cd flink-jars
curl -O https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-flink-runtime-1.19/1.5.0/iceberg-flink-runtime-1.19-1.5.0.jar
```

**Verification**:
```bash
sha256sum iceberg-flink-runtime-1.19-1.5.0.jar
```

**Important Notes**:
- This is a "fat JAR" containing Iceberg + Hadoop + AWS SDK dependencies
- Matches Iceberg version used in Spark jobs (1.5.0)
- Includes Hive metastore support (though we use REST catalog)

---

### 3. S3 Filesystem Hadoop
**File**: `flink-s3-fs-hadoop-1.19.1.jar`
**Size**: ~50 MB
**Purpose**: S3-compatible filesystem for Flink checkpoints (MinIO)
**Maven Coordinates**: `org.apache.flink:flink-s3-fs-hadoop:1.19.1`

**Download**:
```bash
cd flink-jars
curl -O https://repo1.maven.org/maven2/org/apache/flink/flink-s3-fs-hadoop/1.19.1/flink-s3-fs-hadoop-1.19.1.jar
```

**Verification**:
```bash
sha256sum flink-s3-fs-hadoop-1.19.1.jar
```

**Configuration**:
```yaml
# In flink-conf.yaml
s3.endpoint: http://minio:9000
s3.access-key: minioadmin
s3.secret-key: minioadmin
s3.path-style-access: true
```

---

### 4. Prometheus Metrics Reporter
**File**: `flink-metrics-prometheus-1.19.1.jar`
**Size**: ~5 MB
**Purpose**: Export Flink metrics to Prometheus (for Grafana dashboards)
**Maven Coordinates**: `org.apache.flink:flink-metrics-prometheus:1.19.1`

**Download**:
```bash
cd flink-jars
curl -O https://repo1.maven.org/maven2/org/apache/flink/flink-metrics-prometheus/1.19.1/flink-metrics-prometheus-1.19.1.jar
```

**Verification**:
```bash
sha256sum flink-metrics-prometheus-1.19.1.jar
```

**Configuration**:
```yaml
# In flink-conf.yaml
metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory
metrics.reporter.prom.port: 9091
```

---

## Bulk Download Script

Create `/scripts/download_flink_jars.sh`:

```bash
#!/bin/bash
set -euo pipefail

JARS_DIR="flink-jars"
mkdir -p "$JARS_DIR"
cd "$JARS_DIR"

echo "Downloading Flink connector JARs..."

# Kafka SQL Connector
echo "[1/4] Downloading Kafka SQL Connector..."
curl -# -O https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.1.0-1.19/flink-sql-connector-kafka-3.1.0-1.19.jar

# Iceberg Flink Runtime (large file, ~200MB)
echo "[2/4] Downloading Iceberg Flink Runtime (this may take a while)..."
curl -# -O https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-flink-runtime-1.19/1.5.0/iceberg-flink-runtime-1.19-1.5.0.jar

# S3 Filesystem Hadoop
echo "[3/4] Downloading S3 Filesystem Hadoop..."
curl -# -O https://repo1.maven.org/maven2/org/apache/flink/flink-s3-fs-hadoop/1.19.1/flink-s3-fs-hadoop-1.19.1.jar

# Prometheus Metrics Reporter
echo "[4/4] Downloading Prometheus Metrics Reporter..."
curl -# -O https://repo1.maven.org/maven2/org/apache/flink/flink-metrics-prometheus/1.19.1/flink-metrics-prometheus-1.19.1.jar

echo ""
echo "Download complete! Verifying files..."
ls -lh

echo ""
echo "Total size:"
du -sh .

echo ""
echo "JAR files ready. Add to Flink cluster via docker-compose volume mounts."
```

**Usage**:
```bash
chmod +x scripts/download_flink_jars.sh
./scripts/download_flink_jars.sh
```

---

## JAR Placement in Docker Compose

JARs must be mounted into Flink containers at `/opt/flink/lib/`:

```yaml
# docker-compose.yml
services:
  flink-jobmanager:
    image: flink:1.19.1-scala_2.12-java11
    volumes:
      - ./flink-jars:/opt/flink/lib
      - ./config/flink/flink-conf.yaml:/opt/flink/conf/flink-conf.yaml
    # ...

  flink-taskmanager-1:
    image: flink:1.19.1-scala_2.12-java11
    volumes:
      - ./flink-jars:/opt/flink/lib
      - ./config/flink/flink-conf.yaml:/opt/flink/conf/flink-conf.yaml
    # ...
```

---

## Compatibility Matrix

| Component | Version | Notes |
|-----------|---------|-------|
| **Flink** | 1.19.1 | LTS release, stable |
| **Iceberg** | 1.5.0 | Matches Spark jobs |
| **Kafka Connector** | 3.1.0-1.19 | Flink 1.19 compatible |
| **Scala** | 2.12 | Matches Spark Scala version |
| **Java** | 11 | Matches Flink Docker image |
| **Hadoop** | 3.3.x | Included in iceberg-flink-runtime JAR |
| **AWS SDK** | 1.12.x | Included in iceberg-flink-runtime JAR |

---

## Troubleshooting

### ClassNotFoundException: org.apache.kafka.clients.consumer.ConsumerConfig
**Problem**: Kafka connector JAR not loaded
**Solution**: Verify `flink-sql-connector-kafka-3.1.0-1.19.jar` is in `/opt/flink/lib/`

### ClassNotFoundException: org.apache.iceberg.flink.TableLoader
**Problem**: Iceberg connector JAR not loaded
**Solution**: Verify `iceberg-flink-runtime-1.19-1.5.0.jar` is in `/opt/flink/lib/`

### Could not find a file system implementation for scheme 's3a'
**Problem**: S3 filesystem JAR not loaded
**Solution**: Verify `flink-s3-fs-hadoop-1.19.1.jar` is in `/opt/flink/lib/`

### Metrics not appearing in Prometheus
**Problem**: Prometheus metrics reporter not loaded or misconfigured
**Solution**:
1. Verify `flink-metrics-prometheus-1.19.1.jar` is in `/opt/flink/lib/`
2. Check `flink-conf.yaml` has `metrics.reporter.prom.*` settings
3. Verify port 9091 is exposed in docker-compose.yml

---

## Version Update Policy

When updating Flink or Iceberg versions:

1. **Check Compatibility**: Verify Flink + Iceberg versions are compatible
   - Reference: https://iceberg.apache.org/docs/latest/flink-connector/

2. **Update All Components**: Keep connector versions in sync
   - Flink runtime version → Kafka connector version
   - Iceberg version → Iceberg Flink runtime version

3. **Test in Non-Production**: Validate with sample tables before production deployment

4. **Document Changes**: Update this manifest with new versions and download links

---

## Additional Resources

- **Maven Central Repository**: https://repo1.maven.org/maven2/
- **Flink Connector Releases**: https://flink.apache.org/downloads.html
- **Iceberg Releases**: https://iceberg.apache.org/releases/
- **Flink Docker Hub**: https://hub.docker.com/_/flink
