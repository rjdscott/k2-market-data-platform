package com.k2.feedhandler

import com.typesafe.config.Config
import io.confluent.kafka.serializers.KafkaAvroSerializer
import io.github.oshai.kotlinlogging.KotlinLogging
import io.micrometer.core.instrument.Counter
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.apache.avro.Schema
import org.apache.avro.generic.GenericData
import org.apache.avro.generic.GenericRecord
import org.apache.kafka.clients.producer.KafkaProducer
import org.apache.kafka.clients.producer.ProducerConfig
import org.apache.kafka.clients.producer.ProducerRecord
import org.apache.kafka.common.serialization.StringSerializer
import java.io.File

private val logger = KotlinLogging.logger {}

/**
 * Kafka producer service with Avro serialization
 *
 * Features:
 * - Idempotent producer (exactly-once semantics)
 * - Avro schema registry integration
 * - Separate raw (JSON) and normalized (Avro) topics
 * - Prometheus metrics via Micrometer (exchange tag on all counters)
 */
class KafkaProducerService(
    private val kafkaConfig: Config,
    private val schemaPath: String,
    private val exchange: String = "unknown"
) {

    private val rawTopic = kafkaConfig.getString("topics.raw")
    private val normalizedTopic = kafkaConfig.getString("topics.normalized")

    // JSON serializer for raw messages
    private val json = Json { ignoreUnknownKeys = true }

    // Kafka producers (separate for JSON and Avro)
    private val rawProducer: KafkaProducer<String, String>
    private val normalizedProducer: KafkaProducer<String, GenericRecord>

    // Avro schemas
    private val normalizedSchema: Schema

    // Metrics — Micrometer counters with exchange label
    private val rawMessagesProduced: Counter = Counter.builder("feed_handler_trades_produced_total")
        .tag("exchange", exchange)
        .tag("type", "raw")
        .description("Total raw trade messages produced to Kafka")
        .register(metricsRegistry)

    private val normalizedMessagesProduced: Counter = Counter.builder("feed_handler_trades_produced_total")
        .tag("exchange", exchange)
        .tag("type", "normalized")
        .description("Total normalized trade messages produced to Kafka")
        .register(metricsRegistry)

    private val errors: Counter = Counter.builder("feed_handler_errors_total")
        .tag("exchange", exchange)
        .description("Total Kafka produce errors")
        .register(metricsRegistry)

    private val reconnects: Counter = Counter.builder("feed_handler_reconnects_total")
        .tag("exchange", exchange)
        .description("Total WebSocket reconnect attempts")
        .register(metricsRegistry)

    init {
        logger.info { "Initializing Kafka producer..." }

        val bootstrapServers = kafkaConfig.getString("bootstrap-servers")
        val schemaRegistryUrl = kafkaConfig.getString("schema-registry-url")

        // Raw producer (JSON)
        rawProducer = KafkaProducer(
            mapOf(
                ProducerConfig.BOOTSTRAP_SERVERS_CONFIG to bootstrapServers,
                ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG to StringSerializer::class.java.name,
                ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG to StringSerializer::class.java.name,
                ProducerConfig.ACKS_CONFIG to kafkaConfig.getString("producer.acks"),
                ProducerConfig.RETRIES_CONFIG to kafkaConfig.getInt("producer.retries"),
                ProducerConfig.LINGER_MS_CONFIG to kafkaConfig.getInt("producer.linger-ms"),
                ProducerConfig.BATCH_SIZE_CONFIG to kafkaConfig.getInt("producer.batch-size"),
                ProducerConfig.COMPRESSION_TYPE_CONFIG to kafkaConfig.getString("producer.compression-type"),
                ProducerConfig.MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION to kafkaConfig.getInt("producer.max-in-flight-requests-per-connection"),
                ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG to kafkaConfig.getBoolean("producer.enable-idempotence"),
                // Topics are managed by redpanda-init; suppress auto-create on metadata requests
                "allow.auto.create.topics" to false
            )
        )

        // Normalized producer (Avro)
        normalizedProducer = KafkaProducer(
            mapOf(
                ProducerConfig.BOOTSTRAP_SERVERS_CONFIG to bootstrapServers,
                ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG to StringSerializer::class.java.name,
                ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG to KafkaAvroSerializer::class.java.name,
                "schema.registry.url" to schemaRegistryUrl,
                ProducerConfig.ACKS_CONFIG to kafkaConfig.getString("producer.acks"),
                ProducerConfig.RETRIES_CONFIG to kafkaConfig.getInt("producer.retries"),
                ProducerConfig.LINGER_MS_CONFIG to kafkaConfig.getInt("producer.linger-ms"),
                ProducerConfig.BATCH_SIZE_CONFIG to kafkaConfig.getInt("producer.batch-size"),
                ProducerConfig.COMPRESSION_TYPE_CONFIG to kafkaConfig.getString("producer.compression-type"),
                ProducerConfig.MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION to kafkaConfig.getInt("producer.max-in-flight-requests-per-connection"),
                ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG to kafkaConfig.getBoolean("producer.enable-idempotence"),
                // Topics are managed by redpanda-init; suppress auto-create on metadata requests
                "allow.auto.create.topics" to false
            )
        )

        // Load Avro schema from configured path
        val schemaFile = File("$schemaPath/avro/normalized-trade.avsc")
        logger.info { "Loading Avro schema from: ${schemaFile.absolutePath}" }

        normalizedSchema = Schema.Parser().parse(schemaFile.readText())

        logger.info { "✅ Kafka producer initialized" }
        logger.info { "   Raw topic: $rawTopic" }
        logger.info { "   Normalized topic: $normalizedTopic" }
    }

    /**
     * Produce raw trade (JSON) to raw topic
     */
    suspend fun produceRaw(event: BinanceTradeEvent) {
        try {
            val json = json.encodeToString(event)
            val record = ProducerRecord(rawTopic, event.symbol, json)

            rawProducer.send(record) { metadata, exception ->
                if (exception != null) {
                    logger.error(exception) { "Failed to produce raw message: ${event.symbol}" }
                    errors.increment()
                } else {
                    rawMessagesProduced.increment()
                }
            }
        } catch (e: Exception) {
            logger.error(e) { "Error producing raw message" }
            errors.increment()
        }
    }

    /**
     * Produce raw JSON string to exchange-specific topic
     *
     * Used for exchanges like Kraken that need custom JSON serialization
     */
    suspend fun produceRawJson(exchange: String, json: String) {
        try {
            val topic = "market.crypto.trades.$exchange.raw"
            val record = ProducerRecord(topic, exchange, json)

            rawProducer.send(record) { metadata, exception ->
                if (exception != null) {
                    logger.error(exception) { "Failed to produce raw JSON to $topic" }
                    errors.increment()
                } else {
                    rawMessagesProduced.increment()
                }
            }
        } catch (e: Exception) {
            logger.error(e) { "Error producing raw JSON message" }
            errors.increment()
        }
    }

    /**
     * Produce normalized trade (Avro) to normalized topic
     */
    suspend fun produceNormalized(trade: NormalizedTrade) {
        try {
            val avroRecord = toAvroRecord(trade)
            val record = ProducerRecord(normalizedTopic, trade.symbol, avroRecord)

            normalizedProducer.send(record) { metadata, exception ->
                if (exception != null) {
                    logger.error(exception) { "Failed to produce normalized message: ${trade.symbol}" }
                    errors.increment()
                } else {
                    normalizedMessagesProduced.increment()
                }
            }
        } catch (e: Exception) {
            logger.error(e) { "Error producing normalized message" }
            errors.increment()
        }
    }

    /**
     * Convert NormalizedTrade to Avro GenericRecord
     */
    private fun toAvroRecord(trade: NormalizedTrade): GenericRecord {
        val record = GenericData.Record(normalizedSchema)
        record.put("schema_version", trade.schemaVersion)
        record.put("exchange", trade.exchange)
        record.put("symbol", trade.symbol)
        record.put("canonical_symbol", trade.canonicalSymbol)
        record.put("trade_id", trade.tradeId)
        record.put("price", trade.price)
        record.put("quantity", trade.quantity)
        record.put("quote_volume", trade.quoteVolume)

        // Create Avro enum symbol for side (not a plain string)
        val sideEnum = normalizedSchema.getField("side").schema()
        record.put("side", GenericData.EnumSymbol(sideEnum, trade.side.name))
        record.put("timestamp", trade.timestamp)
        record.put("exchange_timestamp", trade.exchangeTimestamp)

        // Metadata (optional)
        if (trade.metadata != null) {
            val metadataSchema = normalizedSchema.getField("metadata").schema().types[1] // Get non-null type
            val metadataRecord = GenericData.Record(metadataSchema)
            metadataRecord.put("sequence_number", trade.metadata.sequenceNumber)
            metadataRecord.put("is_buyer_maker", trade.metadata.isBuyerMaker)
            metadataRecord.put("buyer_order_id", trade.metadata.buyerOrderId)
            metadataRecord.put("seller_order_id", trade.metadata.sellerOrderId)
            record.put("metadata", metadataRecord)
        } else {
            record.put("metadata", null)
        }

        return record
    }

    /**
     * Increment reconnect counter (called by WebSocket clients on reconnect)
     */
    fun recordReconnect() {
        reconnects.increment()
    }

    /**
     * Log metrics summary (also visible via /metrics Prometheus endpoint)
     */
    fun logMetrics() {
        logger.info {
            "📊 Metrics [$exchange]: Raw=${rawMessagesProduced.count().toLong()}, " +
            "Normalized=${normalizedMessagesProduced.count().toLong()}, " +
            "Errors=${errors.count().toLong()}, " +
            "Reconnects=${reconnects.count().toLong()}"
        }
    }

    /**
     * Close producers
     */
    fun close() {
        logger.info { "Closing Kafka producers..." }
        rawProducer.flush()
        normalizedProducer.flush()
        rawProducer.close()
        normalizedProducer.close()
        logger.info { "✅ Kafka producers closed" }
    }
}
