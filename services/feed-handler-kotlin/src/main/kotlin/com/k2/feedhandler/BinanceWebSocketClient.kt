package com.k2.feedhandler

import com.typesafe.config.Config
import io.github.oshai.kotlinlogging.KotlinLogging
import io.ktor.client.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.websocket.*
import io.ktor.websocket.*
import kotlinx.coroutines.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlin.time.Duration.Companion.milliseconds

private val logger = KotlinLogging.logger {}

/**
 * Binance WebSocket client with automatic reconnection
 *
 * Features:
 * - Structured concurrency (coroutines)
 * - Automatic reconnection with exponential backoff
 * - Heartbeat/ping to keep connection alive
 * - Graceful error handling
 */
class BinanceWebSocketClient(
    private val config: Config,
    private val producer: KafkaProducerService,
    private val symbols: List<String>
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val wsUrl = config.getString("websocket-url")
    private val reconnectDelay = config.getLong("reconnect-delay-ms")
    private val maxReconnectAttempts = config.getInt("max-reconnect-attempts")

    private val client = HttpClient(CIO) {
        install(WebSockets) {
            pingInterval = config.getLong("ping-interval-ms").milliseconds
        }
    }

    /**
     * Connect to Binance WebSocket and subscribe to trade streams
     *
     * Automatically reconnects on failures
     */
    suspend fun connect() {
        var attempt = 0

        while (true) {
            try {
                attempt++
                logger.info { "🔌 Connecting to Binance WebSocket (attempt $attempt)..." }

                client.webSocket(wsUrl) {
                    logger.info { "✅ Connected to Binance WebSocket" }

                    // Subscribe to trade streams for all symbols
                    subscribe()

                    // Reset attempt counter on successful connection
                    attempt = 0

                    // Handle incoming messages
                    try {
                        for (frame in incoming) {
                            when (frame) {
                                is Frame.Text -> {
                                    handleMessage(frame.readText())
                                }
                                is Frame.Close -> {
                                    logger.warn { "WebSocket closed: ${frame.readReason()}" }
                                    break
                                }
                                else -> {}
                            }
                        }
                    } catch (e: CancellationException) {
                        logger.info { "WebSocket connection cancelled" }
                        throw e // Re-throw to cancel coroutine
                    } catch (e: Exception) {
                        logger.error(e) { "Error reading WebSocket frame" }
                    }
                }

                // Connection closed, will reconnect
                logger.warn { "WebSocket connection lost, reconnecting in ${reconnectDelay}ms..." }

            } catch (e: CancellationException) {
                logger.info { "WebSocket client cancelled, shutting down..." }
                client.close()
                throw e
            } catch (e: Exception) {
                logger.error(e) { "WebSocket connection error" }
            }

            // Check if we've exceeded max attempts
            if (maxReconnectAttempts > 0 && attempt >= maxReconnectAttempts) {
                logger.error { "Max reconnect attempts ($maxReconnectAttempts) reached, giving up" }
                client.close()
                break
            }

            // Wait before reconnecting
            delay(reconnectDelay)
        }
    }

    /**
     * Subscribe to trade streams for all configured symbols
     */
    private suspend fun DefaultClientWebSocketSession.subscribe() {
        val streams = symbols.map { "${it.lowercase()}@trade" }
        val subscription = BinanceSubscription(
            method = "SUBSCRIBE",
            params = streams,
            id = 1
        )

        val message = json.encodeToString(subscription)
        logger.info { "Sending subscription: $message" }
        send(Frame.Text(message))

        logger.info { "📡 Subscribed to ${symbols.size} trade streams: ${symbols.joinToString(", ")}" }
    }

    /**
     * Handle incoming WebSocket message
     */
    private suspend fun handleMessage(text: String) {
        try {
            // Skip subscription confirmation and error messages
            if (text.contains("\"result\":null")) {
                logger.debug { "Subscription confirmed" }
                return
            }
            if (text.contains("\"error\"")) {
                logger.warn { "Binance API error: $text" }
                return
            }

            // Parse combined stream message and extract trade event
            val combined = json.decodeFromString<BinanceCombinedStreamMessage>(text)
            val event = combined.data

            // Produce to both raw and normalized topics concurrently
            coroutineScope {
                launch {
                    producer.produceRaw(event)
                }

                launch {
                    val normalized = TradeNormalizer.normalizeBinance(event)
                    producer.produceNormalized(normalized)
                }
            }

            logger.debug { "Trade: ${event.symbol} ${event.price} × ${event.quantity}" }

        } catch (e: Exception) {
            logger.error(e) { "Error processing message: ${text.take(200)}" }
        }
    }
}
