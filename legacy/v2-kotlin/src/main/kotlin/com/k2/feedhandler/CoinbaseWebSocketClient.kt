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
 * Coinbase Advanced Trade WebSocket client with automatic reconnection.
 *
 * Protocol: Object-based (unlike Kraken's array-based format)
 * Trade message format:
 * {
 *   "channel": "market_trades",
 *   "sequence_num": 0,
 *   "events": [{
 *     "type": "update",
 *     "trades": [{
 *       "trade_id": "12345",
 *       "product_id": "BTC-USD",
 *       "price": "21921.73",
 *       "size": "0.00099853",
 *       "side": "BUY",
 *       "time": "2023-02-09T20:32:57.609931067Z"
 *     }]
 *   }]
 * }
 */
class CoinbaseWebSocketClient(
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
     * Connect to Coinbase WebSocket and subscribe to market_trades channel.
     * Automatically reconnects on failures with exponential-equivalent delay.
     */
    suspend fun connect() {
        var attempt = 0

        while (true) {
            try {
                attempt++
                logger.info { "🔌 Connecting to Coinbase WebSocket (attempt $attempt)..." }

                client.webSocket(wsUrl) {
                    logger.info { "✅ Connected to Coinbase WebSocket" }

                    subscribe()
                    attempt = 0

                    try {
                        for (frame in incoming) {
                            when (frame) {
                                is Frame.Text -> handleMessage(frame.readText())
                                is Frame.Close -> {
                                    logger.warn { "WebSocket closed: ${frame.readReason()}" }
                                    break
                                }
                                else -> {}
                            }
                        }
                    } catch (e: CancellationException) {
                        logger.info { "WebSocket connection cancelled" }
                        throw e
                    } catch (e: Exception) {
                        logger.error(e) { "Error reading WebSocket frame" }
                    }
                }

                logger.warn { "WebSocket connection lost, reconnecting in ${reconnectDelay}ms..." }
                producer.recordReconnect()

            } catch (e: CancellationException) {
                logger.info { "WebSocket client cancelled, shutting down..." }
                client.close()
                throw e
            } catch (e: Exception) {
                logger.error(e) { "WebSocket connection error" }
            }

            if (maxReconnectAttempts > 0 && attempt >= maxReconnectAttempts) {
                logger.error { "Max reconnect attempts ($maxReconnectAttempts) reached, giving up" }
                client.close()
                break
            }

            delay(reconnectDelay)
        }
    }

    /**
     * Subscribe to market_trades channel for all configured product IDs.
     *
     * Coinbase subscription format:
     * {
     *   "type": "subscribe",
     *   "channel": "market_trades",
     *   "product_ids": ["BTC-USD", "ETH-USD"]
     * }
     */
    private suspend fun DefaultClientWebSocketSession.subscribe() {
        val subscription = buildJsonObject {
            put("type", "subscribe")
            put("channel", "market_trades")
            putJsonArray("product_ids") {
                symbols.forEach { add(it) }
            }
        }

        val message = subscription.toString()
        logger.info { "Sending subscription: $message" }
        send(Frame.Text(message))

        logger.info { "📡 Subscribed to ${symbols.size} Coinbase trade streams: ${symbols.joinToString(", ")}" }
    }

    /**
     * Handle incoming WebSocket message.
     *
     * Coinbase sends object-based messages routed by the "channel" field:
     * - "market_trades" → parse events[] for trade data
     * - "subscriptions" → subscription confirmation (log and continue)
     * - (no channel) + type == "error" → log error
     */
    private suspend fun handleMessage(text: String) {
        try {
            val jsonElement = json.parseToJsonElement(text)
            if (jsonElement !is JsonObject) {
                logger.debug { "Ignoring non-object message" }
                return
            }

            val channel = jsonElement["channel"]?.jsonPrimitive?.contentOrNull
            val msgType = jsonElement["type"]?.jsonPrimitive?.contentOrNull

            when {
                channel == "market_trades" -> parseTradeEvents(jsonElement)
                channel == "subscriptions" -> logger.info { "Subscription confirmed: ${text.take(200)}" }
                msgType == "error" -> logger.error { "Coinbase API error: $text" }
                else -> logger.debug { "Unknown message: ${text.take(200)}" }
            }

        } catch (e: Exception) {
            logger.error(e) { "Error processing Coinbase message: ${text.take(200)}" }
        }
    }

    /**
     * Parse market_trades envelope and dispatch individual trades.
     *
     * Envelope structure:
     * {
     *   "channel": "market_trades",
     *   "sequence_num": 123,
     *   "events": [{ "type": "update", "trades": [...] }]
     * }
     */
    private suspend fun parseTradeEvents(envelope: JsonObject) {
        val sequenceNum = envelope["sequence_num"]?.jsonPrimitive?.longOrNull ?: 0L
        val events = envelope["events"]?.jsonArray ?: return

        for (event in events) {
            val eventObj = event as? JsonObject ?: continue
            val eventType = eventObj["type"]?.jsonPrimitive?.contentOrNull
            if (eventType != "update") continue

            val trades = eventObj["trades"]?.jsonArray ?: continue
            for (trade in trades) {
                val tradeObj = trade as? JsonObject ?: continue
                processTrade(tradeObj, sequenceNum)
            }
        }
    }

    /**
     * Process a single Coinbase trade: produce raw JSON + normalized Avro in parallel.
     *
     * Raw JSON fields preserved: trade_id, product_id, price, size, side, time, sequence_num
     */
    private suspend fun processTrade(trade: JsonObject, sequenceNum: Long) {
        val tradeId = trade["trade_id"]?.jsonPrimitive?.contentOrNull ?: return
        val productId = trade["product_id"]?.jsonPrimitive?.contentOrNull ?: return
        val price = trade["price"]?.jsonPrimitive?.contentOrNull ?: return
        val size = trade["size"]?.jsonPrimitive?.contentOrNull ?: return
        val side = trade["side"]?.jsonPrimitive?.contentOrNull ?: return
        val time = trade["time"]?.jsonPrimitive?.contentOrNull ?: return

        val rawJson = buildJsonObject {
            put("trade_id", tradeId)
            put("product_id", productId)
            put("price", price)
            put("size", size)
            put("side", side)
            put("time", time)
            put("sequence_num", sequenceNum)
        }.toString()

        coroutineScope {
            launch {
                producer.produceRawJson("coinbase", rawJson)
            }
            launch {
                val normalized = TradeNormalizer.normalizeCoinbase(
                    tradeId = tradeId,
                    productId = productId,
                    price = price,
                    size = size,
                    side = side,
                    time = time,
                    sequenceNum = sequenceNum
                )
                producer.produceNormalized(normalized)
            }
        }

        logger.debug { "Trade: $productId $price × $size ($side)" }
    }
}
