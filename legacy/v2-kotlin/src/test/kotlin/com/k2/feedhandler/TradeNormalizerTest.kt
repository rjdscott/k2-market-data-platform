package com.k2.feedhandler

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class TradeNormalizerTest {

    // ─────────────────────────────────────────────────────────────────────
    // Coinbase: normalizeCoinbase()
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `normalizeCoinbase - happy path BUY trade`() {
        val result = TradeNormalizer.normalizeCoinbase(
            tradeId = "12345",
            productId = "BTC-USD",
            price = "21921.73",
            size = "0.00099853",
            side = "BUY",
            time = "2023-02-09T20:32:57.609931067Z",
            sequenceNum = 0L
        )

        assertEquals("coinbase", result.exchange)
        assertEquals("BTCUSD", result.symbol)
        assertEquals("BTC/USD", result.canonicalSymbol)
        assertEquals("12345", result.tradeId)
        assertEquals("21921.73", result.price)
        assertEquals("0.00099853", result.quantity)
        assertEquals(TradeSide.BUY, result.side)
    }

    @Test
    fun `normalizeCoinbase - SELL side maps correctly`() {
        val result = TradeNormalizer.normalizeCoinbase(
            tradeId = "99999",
            productId = "ETH-USD",
            price = "3000.00",
            size = "1.5",
            side = "SELL",
            time = "2023-02-09T20:32:57.609931067Z",
            sequenceNum = 42L
        )

        assertEquals(TradeSide.SELL, result.side)
    }

    @Test
    fun `normalizeCoinbase - lowercase side is normalised`() {
        val result = TradeNormalizer.normalizeCoinbase(
            tradeId = "1",
            productId = "SOL-USD",
            price = "100.00",
            size = "10.0",
            side = "buy",
            time = "2023-02-09T20:32:57.609931067Z",
            sequenceNum = 1L
        )

        assertEquals(TradeSide.BUY, result.side)
    }

    @Test
    fun `normalizeCoinbase - symbol normalisation from productId`() {
        val cases = mapOf(
            "BTC-USD" to Pair("BTCUSD", "BTC/USD"),
            "ETH-USD" to Pair("ETHUSD", "ETH/USD"),
            "SOL-USD" to Pair("SOLUSD", "SOL/USD"),
            "DOGE-USD" to Pair("DOGEUSD", "DOGE/USD"),
        )

        for ((productId, expected) in cases) {
            val result = TradeNormalizer.normalizeCoinbase(
                tradeId = "1",
                productId = productId,
                price = "1.0",
                size = "1.0",
                side = "BUY",
                time = "2023-02-09T20:32:57.609931067Z",
                sequenceNum = 0L
            )
            assertEquals(expected.first, result.symbol, "symbol mismatch for $productId")
            assertEquals(expected.second, result.canonicalSymbol, "canonicalSymbol mismatch for $productId")
        }
    }

    @Test
    fun `normalizeCoinbase - ISO8601 timestamp parsed to epoch millis`() {
        // "2023-02-09T20:32:57.609931067Z" → 1675974777609 ms
        val result = TradeNormalizer.normalizeCoinbase(
            tradeId = "1",
            productId = "BTC-USD",
            price = "21921.73",
            size = "0.001",
            side = "BUY",
            time = "2023-02-09T20:32:57.609931067Z",
            sequenceNum = 0L
        )

        assertEquals(1675974777609L, result.exchangeTimestamp)
    }

    @Test
    fun `normalizeCoinbase - quote volume computed correctly`() {
        // price=100.00, size=2.5 → quote_volume=250.00
        val result = TradeNormalizer.normalizeCoinbase(
            tradeId = "1",
            productId = "BTC-USD",
            price = "100.00",
            size = "2.5",
            side = "BUY",
            time = "2023-02-09T20:32:57.609931067Z",
            sequenceNum = 0L
        )

        assertEquals("250.000", result.quoteVolume)
    }

    @Test
    fun `normalizeCoinbase - metadata sequence number captured`() {
        val result = TradeNormalizer.normalizeCoinbase(
            tradeId = "1",
            productId = "BTC-USD",
            price = "1.0",
            size = "1.0",
            side = "BUY",
            time = "2023-02-09T20:32:57.609931067Z",
            sequenceNum = 777L
        )

        assertEquals(777L, result.metadata?.sequenceNumber)
        assertEquals(null, result.metadata?.isBuyerMaker)
    }
}
