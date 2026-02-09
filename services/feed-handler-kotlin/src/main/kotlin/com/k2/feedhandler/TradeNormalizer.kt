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
