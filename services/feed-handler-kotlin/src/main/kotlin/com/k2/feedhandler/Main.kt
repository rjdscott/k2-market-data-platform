package com.k2.feedhandler

import com.typesafe.config.ConfigFactory
import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.coroutines.*
import kotlin.system.exitProcess

private val logger = KotlinLogging.logger {}

/**
 * K2 Market Data Feed Handler - Binance
 *
 * Connects to Binance WebSocket, produces to:
 * - Raw topic (exchange-specific JSON)
 * - Normalized topic (canonical Avro)
 *
 * Architecture:
 * - Coroutines for structured concurrency
 * - Automatic reconnection on failures
 * - Graceful shutdown on SIGTERM/SIGINT
 * - Idempotent Kafka producer
 */
suspend fun main() {
    logger.info { "🚀 K2 Feed Handler starting..." }

    // Load configuration
    val config = ConfigFactory.load().getConfig("k2.feed-handler")
    val exchange = config.getString("exchange")

    // Parse symbols - handle both list format and comma-separated string from env var
    val symbols = try {
        config.getStringList("symbols")
    } catch (e: Exception) {
        // If env var is comma-separated string, parse it
        config.getString("symbols").split(",").map { it.trim() }
    }

    val schemaPath = config.getString("schema-path")

    logger.info { "Exchange: $exchange" }
    logger.info { "Symbols: ${symbols.joinToString(", ")}" }
    logger.info { "Schema path: $schemaPath" }

    // Initialize Kafka producer
    val producer = KafkaProducerService(
        kafkaConfig = config.getConfig("kafka"),
        schemaPath = schemaPath
    )

    // Create WebSocket client
    val wsClient = BinanceWebSocketClient(
        config = config.getConfig("binance"),
        producer = producer,
        symbols = symbols
    )

    // Structured concurrency: all child coroutines cancel when parent cancels
    coroutineScope {
        // Launch WebSocket connection (auto-reconnect)
        val wsJob = launch(CoroutineName("binance-websocket")) {
            wsClient.connect()
        }

        // Launch metrics logger
        launch(CoroutineName("metrics-logger")) {
            while (isActive) {
                delay(config.getLong("metrics.log-interval-seconds") * 1000)
                producer.logMetrics()
            }
        }

        // Graceful shutdown on SIGTERM/SIGINT
        Runtime.getRuntime().addShutdownHook(Thread {
            runBlocking {
                logger.info { "🛑 Shutdown signal received, cleaning up..." }
                wsJob.cancelAndJoin()
                producer.close()
                logger.info { "✅ Shutdown complete" }
            }
        })

        logger.info { "✅ Feed handler running (Ctrl+C to stop)" }

        // Wait for WebSocket job to complete (shouldn't happen unless error)
        wsJob.join()
    }

    exitProcess(1) // Exit with error if we reach here
}
