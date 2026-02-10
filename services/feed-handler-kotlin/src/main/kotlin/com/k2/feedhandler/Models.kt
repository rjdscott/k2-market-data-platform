package com.k2.feedhandler

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName

/**
 * Combined stream wrapper (wss://stream.binance.com:9443/stream)
 *
 * Note: Not used with /ws endpoint (subscription-based approach)
 * Only needed if using /stream endpoint with streams in URL
 */
@Serializable
data class BinanceCombinedStreamMessage(
    val stream: String,
    val data: BinanceTradeEvent
)

/**
 * Binance WebSocket trade event
 * https://binance-docs.github.io/apidocs/spot/en/#trade-streams
 */
@Serializable
data class BinanceTradeEvent(
    @SerialName("e") val eventType: String,          // "trade"
    @SerialName("E") val eventTime: Long,            // Event time (ms)
    @SerialName("s") val symbol: String,             // Symbol (BTCUSDT)
    @SerialName("t") val tradeId: Long,              // Trade ID
    @SerialName("p") val price: String,              // Price
    @SerialName("q") val quantity: String,           // Quantity
    @SerialName("b") val buyerOrderId: Long? = null,  // Buyer order ID (optional)
    @SerialName("a") val sellerOrderId: Long? = null, // Seller order ID (optional)
    @SerialName("T") val tradeTime: Long,            // Trade time (ms)
    @SerialName("m") val isBuyerMaker: Boolean,      // Is buyer maker
    @SerialName("M") val ignore: Boolean             // Ignore (always true)
)

/**
 * Normalized trade (canonical format)
 * Matches Avro schema: normalized-trade.avsc
 */
data class NormalizedTrade(
    val schemaVersion: String = "1.0.0",
    val exchange: String,
    val symbol: String,
    val canonicalSymbol: String,
    val tradeId: String,
    val price: String,
    val quantity: String,
    val quoteVolume: String,
    val side: TradeSide,
    val timestamp: Long,                             // Platform timestamp (ms)
    val exchangeTimestamp: Long,                     // Exchange timestamp (ms)
    val metadata: TradeMetadata? = null
)

/**
 * Trade side (taker perspective)
 */
enum class TradeSide {
    BUY, SELL
}

/**
 * Exchange-specific metadata
 */
data class TradeMetadata(
    val sequenceNumber: Long? = null,
    val isBuyerMaker: Boolean? = null,
    val buyerOrderId: Long? = null,
    val sellerOrderId: Long? = null
)

/**
 * WebSocket subscription message
 * Note: No default values - be explicit for protocol messages
 */
@Serializable
data class BinanceSubscription(
    val method: String,
    val params: List<String>,
    val id: Int
)
