SET spark.sql.ansi.enabled=true;
SET spark.sql.ansi.doubleQuotedIdentifiers=true;
CREATE TABLE "lake"."kraken_trades"."bronze_trade" (
    "kafka_partition" INT NOT NULL,
    "kafka_offset" BIGINT NOT NULL,
    "kafka_timestamp" TIMESTAMP_LTZ,
    "schema_id" INT,
    "exchange" STRING,
    "stream" STRING,
    "symbol" STRING,
    "recv_ts_ns" BIGINT,
    "conn_id" STRING,
    "conn_msg_seq" BIGINT,
    "payload" BINARY,
    "batch_id" STRING,
    "landed_at" TIMESTAMP_LTZ,
    "run_id" STRING,
    "release_id" STRING
)
USING iceberg
TBLPROPERTIES ('format-version' = '2', 'write.format.default' = 'parquet', 'write.parquet.compression-codec' = 'zstd', 'write.distribution-mode' = 'hash', 'write.delete.mode' = 'copy-on-write', 'write.update.mode' = 'copy-on-write', 'write.merge.mode' = 'copy-on-write');
