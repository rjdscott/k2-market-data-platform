package com.k2.feedhandler

import java.math.BigDecimal

/**
 * Normalizes exchange-specific trade data to canonical format
 */
object TradeNormalizer {

    /**
     * Normalize Binance trade to canonical format
     */
    fun normalizeBinance(event: BinanceTradeEvent): NormalizedTrade {
        val price = BigDecimal(event.price)
        val quantity = BigDecimal(event.quantity)
        val quoteVolume = price.multiply(quantity)

        return NormalizedTrade(
            exchange = "binance",
            symbol = event.symbol,
            canonicalSymbol = normalizeSymbol(event.symbol),
            tradeId = event.tradeId.toString(),
            price = event.price,
            quantity = event.quantity,
            quoteVolume = quoteVolume.toPlainString(),
            side = if (event.isBuyerMaker) TradeSide.SELL else TradeSide.BUY,  // Taker side
            timestamp = System.currentTimeMillis(),
            exchangeTimestamp = event.tradeTime,
            metadata = TradeMetadata(
                sequenceNumber = event.tradeId,
                isBuyerMaker = event.isBuyerMaker,
                buyerOrderId = event.buyerOrderId,
                sellerOrderId = event.sellerOrderId
            )
        )
    }

    /**
     * Normalize Kraken trade to canonical format
     */
    fun normalizeKraken(
        channelId: Long,
        pair: String,
        price: String,
        volume: String,
        timestamp: String,  // "seconds.microseconds"
        side: String,       // "b" or "s"
        orderType: String,  // "l" or "m"
        misc: String
    ): NormalizedTrade {
        val priceDecimal = BigDecimal(price)
        val quantityDecimal = BigDecimal(volume)
        val quoteVolume = priceDecimal.multiply(quantityDecimal)

        // Convert Kraken timestamp "seconds.microseconds" to milliseconds
        val timestampMs = (timestamp.toDouble() * 1000).toLong()

        // Generate deterministic trade ID (Kraken doesn't provide one)
        val tradeId = "KRAKEN-${timestampMs}-${pair.hashCode()}"

        // Normalize XBT → BTC in pair (XBT/USD → BTC/USD)
        val normalizedPair = normalizeKrakenPair(pair)

        // Symbol without slash (BTC/USD → BTCUSD)
        val symbol = normalizedPair.replace("/", "")

        return NormalizedTrade(
            exchange = "kraken",
            symbol = symbol,
            canonicalSymbol = normalizedPair,
            tradeId = tradeId,
            price = price,
            quantity = volume,
            quoteVolume = quoteVolume.toPlainString(),
            side = when (side) {
                "b" -> TradeSide.BUY
                "s" -> TradeSide.SELL
                else -> throw IllegalArgumentException("Unknown Kraken side: $side")
            },
            timestamp = System.currentTimeMillis(),
            exchangeTimestamp = timestampMs,
            metadata = TradeMetadata(
                sequenceNumber = channelId,
                isBuyerMaker = null,  // Kraken doesn't provide this
                buyerOrderId = null,
                sellerOrderId = null
            )
        )
    }

    /**
     * Normalize Kraken pair to canonical format
     *
     * Examples:
     * - XBT/USD → BTC/USD (normalize XBT to BTC)
     * - ETH/USD → ETH/USD (already canonical)
     * - XBT/EUR → BTC/EUR
     */
    private fun normalizeKrakenPair(krakenPair: String): String {
        return if (krakenPair.startsWith("XBT/")) {
            krakenPair.replace("XBT/", "BTC/")
        } else {
            krakenPair
        }
    }

    /**
     * Normalize Binance symbol to canonical format
     *
     * Examples:
     * - BTCUSDT → BTC/USDT
     * - ETHBTC → ETH/BTC
     * - BNBUSDT → BNB/USDT
     */
    private fun normalizeSymbol(binanceSymbol: String): String {
        // Common quote currencies (check longest first)
        val quoteCurrencies = listOf("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB")

        for (quote in quoteCurrencies) {
            if (binanceSymbol.endsWith(quote)) {
                val base = binanceSymbol.removeSuffix(quote)
                return "$base/$quote"
            }
        }

        // Fallback: if no known quote currency, just return as-is
        return binanceSymbol
    }
}
