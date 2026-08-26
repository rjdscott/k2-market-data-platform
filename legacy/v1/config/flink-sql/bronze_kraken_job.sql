-- ==============================================================================
-- Flink SQL Job: Kraken Bronze Streaming INSERT
-- ==============================================================================
-- Purpose: Continuous streaming job from Kafka to Iceberg
-- BEST PRACTICE: Submitted separately after table setup for proper job persistence
-- ==============================================================================

-- Submit streaming insert job
INSERT INTO iceberg.market_data.bronze_kraken_trades_flink
SELECT
    `value` AS raw_bytes,
    `topic` AS topic,
    `partition` AS `partition`,
    `offset` AS `offset`,
    `timestamp` AS kafka_timestamp,
    CURRENT_TIMESTAMP AS ingestion_timestamp,
    CURRENT_DATE AS ingestion_date
FROM default_catalog.default_database.kafka_source_kraken;
