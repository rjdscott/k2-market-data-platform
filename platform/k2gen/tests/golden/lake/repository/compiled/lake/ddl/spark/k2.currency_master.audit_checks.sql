SET spark.sql.ansi.enabled=true;
SET spark.sql.ansi.doubleQuotedIdentifiers=true;
CREATE TABLE "lake"."currency_master"."audit_checks" (
    "table_name" STRING,
    "batch_id" STRING,
    "snapshot_id" BIGINT,
    "check_name" STRING,
    "passed" BOOLEAN,
    "detail" STRING,
    "checked_at" TIMESTAMP_LTZ,
    "run_id" STRING,
    "release_id" STRING
)
USING iceberg
PARTITIONED BY (days("checked_at"))
TBLPROPERTIES ('format-version' = '2', 'write.format.default' = 'parquet', 'write.parquet.compression-codec' = 'zstd', 'write.distribution-mode' = 'hash', 'write.delete.mode' = 'copy-on-write', 'write.update.mode' = 'copy-on-write', 'write.merge.mode' = 'copy-on-write');
