-- ==============================================================================
-- Flink SQL Job: Kraken Bronze Ingestion
-- ==============================================================================
-- Purpose: Stream raw Kraken trades from Kafka to Iceberg Bronze table
-- Source: Kafka topic 'kraken.raw' (Avro with Schema Registry)
-- Sink: Iceberg table 'market_data.bronze_kraken_trades_flink'
-- Pattern: RAW format (store Schema Registry header + Avro bytes, no deserialization)
-- Created: 2026-01-20
-- Phase: 12 - Flink Bronze Implementation
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Configure Execution Environment
-- ------------------------------------------------------------------------------
SET 'execution.runtime-mode' = 'streaming';
SET 'execution.checkpointing.interval' = '10s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'table.exec.sink.upsert-materialize' = 'none';

-- Pipeline name (visible in Flink Web UI)
SET 'pipeline.name' = 'bronze_kraken_ingestion';

-- ------------------------------------------------------------------------------
-- 2. Create Kafka Source Table (Raw Bytes Format)
-- ------------------------------------------------------------------------------
-- Note: Using RAW format to capture entire Kafka message (including Schema Registry header)
-- This matches Spark Bronze semantics: store raw bytes for replayability
-- Create in default_catalog BEFORE switching to Iceberg catalog
CREATE TABLE kafka_source_kraken (
    `value` BYTES,                      -- Raw message bytes (5-byte SR header + Avro payload)
    `topic` STRING METADATA FROM 'topic' VIRTUAL,
    `partition` INT METADATA FROM 'partition' VIRTUAL,
    `offset` BIGINT METADATA FROM 'offset' VIRTUAL,
    `timestamp` TIMESTAMP(3) METADATA FROM 'timestamp' VIRTUAL
) WITH (
    'connector' = 'kafka',
    'topic' = 'market.crypto.trades.kraken.raw',
    'properties.bootstrap.servers' = 'kafka:29092',
    'properties.group.id' = 'flink-bronze-kraken',
    'properties.auto.offset.reset' = 'latest',
    'scan.startup.mode' = 'latest-offset',
    'format' = 'raw'                    -- CRITICAL: RAW format preserves Schema Registry header
);

-- ------------------------------------------------------------------------------
-- 3. Create Iceberg Catalog
-- ------------------------------------------------------------------------------
CREATE CATALOG iceberg WITH (
    'type' = 'iceberg',
    'catalog-type' = 'rest',
    'uri' = 'http://iceberg-rest:8181',
    'warehouse' = 's3a://warehouse'
);

USE CATALOG iceberg;

-- ------------------------------------------------------------------------------
-- 4. Create Iceberg Sink Table (Bronze Layer)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_data.bronze_kraken_trades_flink (
    raw_bytes BYTES NOT NULL COMMENT 'Raw Kafka message bytes (Schema Registry header + Avro payload)',
    topic STRING NOT NULL COMMENT 'Source Kafka topic',
    `partition` INT NOT NULL COMMENT 'Kafka partition',
    `offset` BIGINT NOT NULL COMMENT 'Kafka offset',
    kafka_timestamp TIMESTAMP(3) NOT NULL COMMENT 'Kafka message timestamp',
    ingestion_timestamp TIMESTAMP(3) NOT NULL COMMENT 'Flink processing timestamp',
    ingestion_date DATE NOT NULL COMMENT 'Partition key: Date of ingestion (UTC)'
) PARTITIONED BY (ingestion_date)
WITH (
    'format-version' = '2',             -- Use Iceberg V2 (row-level deletes, equality deletes)
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'snappy',
    'write.target-file-size-bytes' = '134217728',  -- 128MB target file size
    'write.metadata.previous-versions-max' = '5',  -- Keep 5 metadata versions (match Spark)
    'write.metadata.compression-codec' = 'gzip'
);

-- ------------------------------------------------------------------------------
-- 5. Insert Query (Continuous Streaming)
-- ------------------------------------------------------------------------------
-- BEST PRACTICE: Use EXECUTE STATEMENT SET to ensure job persists in session cluster
-- This pattern is recommended for production Flink SQL streaming jobs
-- Reference: https://nightlies.apache.org/flink/flink-docs-release-1.19/docs/dev/table/sql/insert/

EXECUTE STATEMENT SET BEGIN
  INSERT INTO market_data.bronze_kraken_trades_flink
  SELECT
      `value` AS raw_bytes,                           -- Raw Kafka message bytes
      `topic` AS topic,                                -- Topic metadata
      `partition` AS `partition`,                      -- Partition metadata (backticks for reserved keyword)
      `offset` AS `offset`,                            -- Offset metadata (backticks for reserved keyword)
      `timestamp` AS kafka_timestamp,                  -- Kafka timestamp (event time)
      CURRENT_TIMESTAMP AS ingestion_timestamp,        -- Flink processing timestamp
      CURRENT_DATE AS ingestion_date                   -- Partition key (UTC date)
  FROM default_catalog.default_database.kafka_source_kraken;
END;

-- ==============================================================================
-- Job Execution Notes
-- ==============================================================================
--
-- This SQL script is executed by the Flink SQL Client in "batch mode":
--   docker exec -it flink-jobmanager /opt/flink/bin/sql-client.sh embedded \
--       -f /opt/flink/sql/bronze_kraken_ingestion.sql
--
-- The INSERT INTO statement creates a long-running Flink job that:
-- 1. Consumes from 'kraken.raw' Kafka topic
-- 2. Preserves raw bytes (5-byte Schema Registry header + Avro payload)
-- 3. Adds metadata (topic, partition, offset, timestamps)
-- 4. Writes to Iceberg table with daily partitioning
-- 5. Checkpoints every 10 seconds to MinIO (s3a://flink/checkpoints)
--
-- Monitoring:
-- - Flink Web UI: http://localhost:8082 (Changed from 8081 to avoid Schema Registry conflict)
-- - Job name: "bronze_kraken_ingestion"
-- - Checkpoints: Should complete in < 10s (ideally 2-5s)
-- - Throughput: Target 500 msg/sec
--
-- Troubleshooting:
-- - Check Flink logs: docker logs k2-flink-jobmanager
-- - Verify Kafka connectivity: kafka:29092
-- - Verify Iceberg REST: http://iceberg-rest:8181
-- - Verify MinIO: http://minio:9000 (checkpoints bucket: flink/)
--
-- Best Practices Applied:
-- 1. EXECUTE STATEMENT SET pattern for job persistence in session cluster
-- 2. RAW format preserves Schema Registry headers for Bronze replayability
-- 3. Exact Kafka topic name: market.crypto.trades.kraken.raw
-- 4. Backticks for SQL reserved keywords (partition, offset)
-- 5. EXACTLY_ONCE checkpoint mode for data consistency
--
-- ==============================================================================
