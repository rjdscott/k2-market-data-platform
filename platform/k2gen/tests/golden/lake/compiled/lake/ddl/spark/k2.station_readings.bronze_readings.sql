SET spark.sql.ansi.enabled=true;
SET spark.sql.ansi.doubleQuotedIdentifiers=true;
CREATE TABLE "lake"."station_readings"."bronze_readings" (
    "station_id" STRING NOT NULL,
    "read_at" TIMESTAMP_LTZ NOT NULL,
    "value_text" STRING NOT NULL,
    "region" STRING,
    "operator_note" STRING,
    "source_file_sha256" STRING NOT NULL
)
USING iceberg
TBLPROPERTIES ('format-version' = '2', 'write.format.default' = 'parquet', 'write.parquet.compression-codec' = 'zstd', 'write.distribution-mode' = 'hash', 'write.delete.mode' = 'copy-on-write', 'write.update.mode' = 'copy-on-write', 'write.merge.mode' = 'copy-on-write');
