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

    // Symbol loading priority:
    // 1. K2_INSTRUMENTS_FILE → instruments.yaml for this exchange
    // 2. K2_SYMBOLS / application.conf → comma-separated or list fallback
    val symbols: List<String> = run {
        val instrumentsFile = System.getenv("K2_INSTRUMENTS_FILE")
        if (!instrumentsFile.isNullOrBlank()) {
            val loaded = InstrumentsLoader(instrumentsFile).loadForExchange(exchange)
            if (loaded.isNotEmpty()) return@run loaded
            logger.warn { "instruments.yaml returned no symbols for '$exchange', falling back to K2_SYMBOLS" }
        }
        // Fallback: HOCON list or comma-separated K2_SYMBOLS env var
        try {
            config.getStringList("symbols")
        } catch (e: Exception) {
            config.getString("symbols").split(",").map { it.trim() }
        }
    }

    if (symbols.isEmpty()) {
        logger.error { "No symbols configured for exchange '$exchange' — check instruments.yaml or K2_SYMBOLS" }
        exitProcess(1)
    }

    val schemaPath = config.getString("schema-path")
    val metricsPort = config.getInt("metrics.port")

    logger.info { "Exchange: $exchange" }
    logger.info { "Symbols (${symbols.size}): ${symbols.joinToString(", ")}" }
    logger.info { "Schema path: $schemaPath" }

    // Start Prometheus metrics HTTP server
    startMetricsServer(port = metricsPort)

    // Initialize Kafka producer (with exchange tag for metrics)
    val producer = KafkaProducerService(
        kafkaConfig = config.getConfig("kafka"),
        schemaPath = schemaPath,
        exchange = exchange
    )

    // Structured concurrency: all child coroutines cancel when parent cancels
    coroutineScope {
        // Launch WebSocket connection (auto-reconnect)
        val wsJob = launch(CoroutineName("$exchange-websocket")) {
            when (exchange.lowercase()) {
                "binance" -> {
                    val wsClient = BinanceWebSocketClient(
                        config = config.getConfig("binance"),
                        producer = producer,
                        symbols = symbols
                    )
                    wsClient.connect()
                }
                "kraken" -> {
                    val wsClient = KrakenWebSocketClient(
                        config = config.getConfig("kraken"),
                        producer = producer,
                        symbols = symbols
                    )
                    wsClient.connect()
                }
                "coinbase" -> {
                    val wsClient = CoinbaseWebSocketClient(
                        config = config.getConfig("coinbase"),
                        producer = producer,
                        symbols = symbols
                    )
                    wsClient.connect()
                }
                else -> {
                    logger.error { "Unknown exchange: $exchange" }
                    exitProcess(1)
                }
            }
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
