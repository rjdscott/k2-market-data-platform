SET spark.sql.ansi.enabled=true;
SET spark.sql.ansi.doubleQuotedIdentifiers=true;
CREATE TABLE "lake"."kraken_instruments"."bronze_pairs" (
    "pair_code" STRING NOT NULL,
    "ws_name" STRING NOT NULL,
    "base" STRING NOT NULL,
    "quote" STRING NOT NULL,
    "tick_size" STRING NOT NULL,
    "pair_decimals" INT NOT NULL,
    "lot_decimals" INT NOT NULL,
    "order_min" STRING NOT NULL,
    "status" STRING NOT NULL,
    "fetched_at" TIMESTAMP_LTZ NOT NULL,
    "source_file_sha256" STRING NOT NULL
)
USING iceberg
TBLPROPERTIES ('format-version' = '2', 'write.format.default' = 'parquet', 'write.parquet.compression-codec' = 'zstd', 'write.distribution-mode' = 'hash', 'write.delete.mode' = 'copy-on-write', 'write.update.mode' = 'copy-on-write', 'write.merge.mode' = 'copy-on-write');
