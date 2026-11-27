SET spark.sql.ansi.enabled=true;
SET spark.sql.ansi.doubleQuotedIdentifiers=true;
CREATE TABLE "lake"."currency_master"."bronze_currencies" (
    "code" STRING NOT NULL,
    "numeric_code" STRING NOT NULL,
    "name" STRING NOT NULL,
    "minor_units" INT,
    "is_fund" BOOLEAN NOT NULL,
    "source_file_sha256" STRING NOT NULL
)
USING iceberg
TBLPROPERTIES ('format-version' = '2', 'write.format.default' = 'parquet', 'write.parquet.compression-codec' = 'zstd', 'write.distribution-mode' = 'hash', 'write.delete.mode' = 'copy-on-write', 'write.update.mode' = 'copy-on-write', 'write.merge.mode' = 'copy-on-write');
