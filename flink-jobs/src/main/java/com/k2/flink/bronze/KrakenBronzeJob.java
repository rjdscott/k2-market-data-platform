package com.k2.flink.bronze;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.TableResult;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

/**
 * Flink Bronze Kraken Streaming Job
 * Continuously streams from Kafka to Iceberg Bronze table
 */
public class KrakenBronzeJob {

    public static void main(String[] args) throws Exception {
        System.out.println("=".repeat(70));
        System.out.println("Flink Bronze Kraken Streaming Job");
        System.out.println("=".repeat(70));

        // Create streaming execution environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        // Create table environment
        EnvironmentSettings settings = EnvironmentSettings
            .newInstance()
            .inStreamingMode()
            .build();
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env, settings);

        // Configure job name (checkpointing temporarily disabled for testing)
        tableEnv.getConfig().getConfiguration().setString("pipeline.name", "bronze_kraken_ingestion");

        // TODO: Re-enable checkpointing once S3 credentials issue is resolved
        // tableEnv.getConfig().getConfiguration().setString("execution.checkpointing.interval", "10s");
        // tableEnv.getConfig().getConfiguration().setString("execution.checkpointing.mode", "EXACTLY_ONCE");

        System.out.println("✓ Table Environment created");

        // Create Kafka source table with Avro schema
        System.out.println("Creating Kafka source table with avro-confluent format...");
        tableEnv.executeSql(
            "CREATE TABLE kafka_source_kraken (" +
            "  `channel_id` BIGINT, " +
            "  `price` STRING, " +
            "  `volume` STRING, " +
            "  `timestamp` STRING, " +
            "  `side` STRING, " +
            "  `order_type` STRING, " +
            "  `misc` STRING, " +
            "  `pair` STRING, " +
            "  `ingestion_timestamp` BIGINT, " +
            "  `topic` STRING METADATA FROM 'topic' VIRTUAL, " +
            "  `partition` INT METADATA FROM 'partition' VIRTUAL, " +
            "  `offset` BIGINT METADATA FROM 'offset' VIRTUAL, " +
            "  `kafka_timestamp` TIMESTAMP(3) METADATA FROM 'timestamp' VIRTUAL" +
            ") WITH (" +
            "  'connector' = 'kafka', " +
            "  'topic' = 'market.crypto.trades.kraken.raw', " +
            "  'properties.bootstrap.servers' = 'kafka:29092', " +
            "  'properties.group.id' = 'flink-bronze-kraken-v4', " +
            "  'scan.startup.mode' = 'earliest-offset', " +
            "  'format' = 'avro-confluent', " +
            "  'avro-confluent.url' = 'http://schema-registry-1:8081'" +
            ")"
        );
        System.out.println("✓ Kafka source table created with Avro deserialization");

        // Create Iceberg catalog
        System.out.println("Creating Iceberg catalog...");
        tableEnv.executeSql(
            "CREATE CATALOG iceberg WITH (" +
            "  'type' = 'iceberg', " +
            "  'catalog-type' = 'rest', " +
            "  'uri' = 'http://iceberg-rest:8181', " +
            "  'warehouse' = 's3a://warehouse', " +
            "  'io-impl' = 'org.apache.iceberg.hadoop.HadoopFileIO'" +
            ")"
        );
        System.out.println("✓ Iceberg catalog created");

        // Switch to Iceberg catalog
        tableEnv.useCatalog("iceberg");

        // Create Iceberg sink table with deserialized Avro fields
        System.out.println("Creating Iceberg sink table...");
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS market_data.bronze_kraken_trades_flink (" +
            "  channel_id BIGINT NOT NULL, " +
            "  price STRING NOT NULL, " +
            "  volume STRING NOT NULL, " +
            "  `timestamp` STRING NOT NULL, " +
            "  side STRING NOT NULL, " +
            "  order_type STRING NOT NULL, " +
            "  misc STRING NOT NULL, " +
            "  pair STRING NOT NULL, " +
            "  ingestion_timestamp BIGINT NOT NULL, " +
            "  topic STRING NOT NULL, " +
            "  `partition` INT NOT NULL, " +
            "  `offset` BIGINT NOT NULL, " +
            "  kafka_timestamp TIMESTAMP(3) NOT NULL, " +
            "  flink_ingestion_timestamp TIMESTAMP(3) NOT NULL, " +
            "  ingestion_date DATE NOT NULL" +
            ") PARTITIONED BY (ingestion_date) " +
            "WITH (" +
            "  'format-version' = '2', " +
            "  'write.format.default' = 'parquet', " +
            "  'write.parquet.compression-codec' = 'snappy', " +
            "  'write.target-file-size-bytes' = '134217728', " +
            "  'write.metadata.previous-versions-max' = '5', " +
            "  'write.metadata.compression-codec' = 'gzip'" +
            ")"
        );
        System.out.println("✓ Iceberg sink table created with Avro schema");

        // Submit streaming INSERT job with Avro field mapping
        System.out.println("\nSubmitting continuous streaming INSERT job...");
        TableResult result = tableEnv.executeSql(
            "INSERT INTO market_data.bronze_kraken_trades_flink " +
            "SELECT " +
            "  `channel_id`, " +
            "  `price`, " +
            "  `volume`, " +
            "  `timestamp`, " +
            "  `side`, " +
            "  `order_type`, " +
            "  `misc`, " +
            "  `pair`, " +
            "  `ingestion_timestamp`, " +
            "  `topic`, " +
            "  `partition`, " +
            "  `offset`, " +
            "  `kafka_timestamp`, " +
            "  CURRENT_TIMESTAMP AS flink_ingestion_timestamp, " +
            "  CURRENT_DATE AS ingestion_date " +
            "FROM default_catalog.default_database.kafka_source_kraken"
        );

        System.out.println("\n" + "=".repeat(70));
        System.out.println("✓ Kraken Bronze Job Submitted Successfully");
        System.out.println("=".repeat(70));
        System.out.println("Job ID: " + result.getJobClient().get().getJobID());
        System.out.println("Status: RUNNING");
        System.out.println("Monitor at: http://localhost:8082");
        System.out.println("=".repeat(70));

        // Wait for job to complete (runs indefinitely for streaming)
        result.await();
    }
}
