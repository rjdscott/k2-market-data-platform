SET spark.sql.ansi.enabled=true;
SET spark.sql.ansi.doubleQuotedIdentifiers=true;
CREATE TABLE "lake"."station_readings"."landing_files" (
    "object_key" STRING,
    "sha256" STRING,
    "bytes" BIGINT,
    "url" STRING,
    "final_url" STRING,
    "http_status" INT,
    "fetched_at" TIMESTAMP,
    "landed_at" TIMESTAMP,
    "parsed" BOOLEAN,
    "batch_id" STRING,
    "run_id" STRING,
    "release_id" STRING
)
USING iceberg
PARTITIONED BY (days("landed_at"))
TBLPROPERTIES ('format-version' = '2', 'write.format.default' = 'parquet', 'write.parquet.compression-codec' = 'zstd', 'write.distribution-mode' = 'hash', 'write.delete.mode' = 'copy-on-write', 'write.update.mode' = 'copy-on-write', 'write.merge.mode' = 'copy-on-write');
