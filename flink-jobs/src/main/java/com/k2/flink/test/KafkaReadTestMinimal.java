package com.k2.flink.test;

import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;

/**
 * Minimal DataStream API test to verify Kafka connectivity
 * Bypasses Table API entirely - just reads and prints to stdout
 */
public class KafkaReadTestMinimal {

    public static void main(String[] args) throws Exception {
        System.out.println("=".repeat(70));
        System.out.println("Minimal DataStream API Kafka Test");
        System.out.println("=".repeat(70));

        // Create streaming environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        // Disable checkpointing for this test (avoids S3 credentials issue)
        env.getCheckpointConfig().disableCheckpointing();

        System.out.println("✓ Environment created (checkpointing disabled for test)");

        // Create Kafka source with minimal config
        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers("kafka:29092")
            .setTopics("market.crypto.trades.binance.raw")
            .setGroupId("flink-datastream-test")
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .build();

        System.out.println("✓ Kafka source created");
        System.out.println("  Bootstrap servers: kafka:29092");
        System.out.println("  Topic: market.crypto.trades.binance.raw");
        System.out.println("  Starting from: EARLIEST offset");

        // Create stream and print to stdout
        DataStream<String> stream = env.fromSource(
            source,
            WatermarkStrategy.noWatermarks(),
            "Kafka Source"
        );

        stream.print();

        System.out.println("\n" + "=".repeat(70));
        System.out.println("✓ Job configured - starting execution");
        System.out.println("=".repeat(70));
        System.out.println("Watch TaskManager logs for printed messages:");
        System.out.println("  docker logs k2-flink-taskmanager-1 -f");
        System.out.println("=".repeat(70) + "\n");

        // Execute job
        env.execute("Minimal Kafka Test - DataStream API");
    }
}
