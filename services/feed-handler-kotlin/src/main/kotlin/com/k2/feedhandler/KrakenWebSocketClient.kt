package com.k2.feedhandler

import com.typesafe.config.Config
import io.github.oshai.kotlinlogging.KotlinLogging
import io.ktor.client.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.websocket.*
import io.ktor.websocket.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import kotlin.time.Duration.Companion.milliseconds

private val logger = KotlinLogging.logger {}

/**
 * Kraken WebSocket client with automatic reconnection
 *
 * Kraken WebSocket format: Array-based protocol
 * Trade message: [channelID, [[price, volume, timestamp, side, orderType, misc]], "trade", "XBT/USD"]
 *
 * Features:
 * - Structured concurrency (coroutines)
 * - Automatic reconnection with exponential backoff
 * - Heartbeat tracking via Kraken's heartbeat messages
 * - Graceful error handling
 */
class KrakenWebSocketClient(
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
     * Connect to Kraken WebSocket and subscribe to trade streams
     *
     * Automatically reconnects on failures
     */
    suspend fun connect() {
        var attempt = 0

        while (true) {
            try {
                attempt++
                logger.info { "🔌 Connecting to Kraken WebSocket (attempt $attempt)..." }

                client.webSocket(wsUrl) {
                    logger.info { "✅ Connected to Kraken WebSocket" }

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
     * Subscribe to trade channel for all configured symbols
     *
     * Kraken subscription format:
     * {
     *   "event": "subscribe",
     *   "pair": ["XBT/USD", "ETH/USD"],
     *   "subscription": {"name": "trade"}
     * }
     */
    private suspend fun DefaultClientWebSocketSession.subscribe() {
        val subscription = buildJsonObject {
            put("event", "subscribe")
            putJsonArray("pair") {
                symbols.forEach { add(it) }
            }
            putJsonObject("subscription") {
                put("name", "trade")
            }
        }

        val message = subscription.toString()
        logger.info { "Sending subscription: $message" }
        send(Frame.Text(message))

        logger.info { "📡 Subscribed to ${symbols.size} trade streams: ${symbols.joinToString(", ")}" }
    }

    /**
     * Handle incoming WebSocket message
     *
     * Kraken sends three types of messages:
     * 1. System events (objects): {"event":"systemStatus"}, {"event":"subscriptionStatus"}
     * 2. Heartbeats (objects): {"event":"heartbeat"}
     * 3. Trade data (arrays): [channelID, [[trade1], [trade2]], "trade", "XBT/USD"]
     */
    private suspend fun handleMessage(text: String) {
        try {
            val jsonElement = json.parseToJsonElement(text)

            // Handle system messages (JSON objects)
            if (jsonElement is JsonObject) {
                val event = jsonElement["event"]?.jsonPrimitive?.content
                when (event) {
                    "systemStatus" -> {
                        val status = jsonElement["status"]?.jsonPrimitive?.content
                        logger.info { "Kraken system status: $status" }
                    }
                    "subscriptionStatus" -> {
                        val status = jsonElement["status"]?.jsonPrimitive?.content
                        val pair = jsonElement["pair"]?.jsonPrimitive?.content
                        logger.info { "Subscription $status for $pair" }
                    }
                    "heartbeat" -> {
                        logger.debug { "Heartbeat received" }
                    }
                    "error" -> {
                        logger.error { "Kraken API error: $text" }
                    }
                    else -> {
                        logger.debug { "Unknown system message: $text" }
                    }
                }
                return
            }

            // Handle trade data (JSON array)
            if (jsonElement is JsonArray && jsonElement.size >= 4) {
                parseTrades(jsonElement)
            }

        } catch (e: Exception) {
            logger.error(e) { "Error processing message: ${text.take(200)}" }
        }
    }

    /**
     * Parse Kraken trade array and produce to Kafka (both raw and normalized)
     *
     * Format: [channelID, [[price, volume, timestamp, side, orderType, misc], ...], "trade", "XBT/USD"]
     *
     * Example:
     * [0, [["95381.70000", "0.00032986", "1737118158.321597", "s", "l", ""]], "trade", "XBT/USD"]
     */
    private suspend fun parseTrades(array: JsonArray) {
        val channelId = array[0].jsonPrimitive.long
        val tradesArray = array[1].jsonArray
        val channelName = array[2].jsonPrimitive.content
        val pair = array[3].jsonPrimitive.content

        // Verify it's a trade message
        if (channelName != "trade") {
            logger.debug { "Ignoring non-trade message: $channelName" }
            return
        }

        // Each message can contain multiple trades
        tradesArray.forEach { tradeElement ->
            val tradeArray = tradeElement.jsonArray

            // Parse trade data
            val price = tradeArray[0].jsonPrimitive.content
            val volume = tradeArray[1].jsonPrimitive.content
            val timestamp = tradeArray[2].jsonPrimitive.content  // "seconds.microseconds"
            val side = tradeArray[3].jsonPrimitive.content       // "b" or "s"
            val orderType = tradeArray[4].jsonPrimitive.content  // "l" or "m"
            val misc = tradeArray[5].jsonPrimitive.content

            // Build JSON matching kraken_raw_trade.avsc schema
            val tradeJson = buildJsonObject {
                put("channel_id", channelId)
                put("pair", pair)                               // Keep XBT/USD as-is (native format)
                put("price", price)
                put("volume", volume)
                put("timestamp", timestamp)                     // Keep Kraken's "seconds.microseconds" format
                put("side", side)                               // "b" or "s"
                put("order_type", orderType)                    // "l" or "m"
                put("misc", misc)
                put("ingestion_timestamp", System.currentTimeMillis() * 1000)  // Microseconds
            }

            // Produce to both raw and normalized topics concurrently (like Binance)
            coroutineScope {
                launch {
                    producer.produceRawJson("kraken", tradeJson.toString())
                }

                launch {
                    val normalized = TradeNormalizer.normalizeKraken(
                        channelId = channelId,
                        pair = pair,
                        price = price,
                        volume = volume,
                        timestamp = timestamp,
                        side = side,
                        orderType = orderType,
                        misc = misc
                    )
                    producer.produceNormalized(normalized)
                }
            }

            logger.debug { "Trade: $pair $price × $volume ($side)" }
        }
    }
}
