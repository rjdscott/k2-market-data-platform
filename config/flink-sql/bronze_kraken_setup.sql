-- ==============================================================================
-- Flink SQL Setup: Kraken Bronze Table Definitions
-- ==============================================================================
-- Purpose: Create table definitions (Kafka source + Iceberg sink)
-- This file is executed once to set up the schema
-- The actual INSERT job is submitted separately for continuous running
-- ==============================================================================

-- Configure Execution Environment
SET 'execution.runtime-mode' = 'streaming';
SET 'execution.checkpointing.interval' = '10s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'table.exec.sink.upsert-materialize' = 'none';
SET 'pipeline.name' = 'bronze_kraken_ingestion';

-- Create Kafka Source Table (Raw Bytes Format)
-- BEST PRACTICE: Create in default catalog before switching to Iceberg
CREATE TABLE IF NOT EXISTS kafka_source_kraken (
    `value` BYTES,
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
    'format' = 'raw'
);

-- Create Iceberg Catalog
-- Note: IF NOT EXISTS is not supported for CREATE CATALOG in Flink SQL
CREATE CATALOG iceberg WITH (
    'type' = 'iceberg',
    'catalog-type' = 'rest',
    'uri' = 'http://iceberg-rest:8181',
    'warehouse' = 's3a://warehouse'
);

USE CATALOG iceberg;

-- Create Iceberg Sink Table (Bronze Layer)
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
    'format-version' = '2',
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'snappy',
    'write.target-file-size-bytes' = '134217728',
    'write.metadata.previous-versions-max' = '5',
    'write.metadata.compression-codec' = 'gzip'
);
