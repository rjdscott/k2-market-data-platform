package com.k2.feedhandler

import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class InstrumentsLoaderTest {

    @TempDir
    lateinit var tempDir: Path

    private fun writeYaml(content: String): String {
        val file = File(tempDir.toFile(), "instruments.yaml")
        file.writeText(content)
        return file.absolutePath
    }

    /**
     * The real config/instruments.yaml.
     *
     * Walks up from user.dir rather than assuming exactly two levels: Gradle sets
     * user.dir to the subproject dir, but the test-runner container and IDE runners
     * do not always agree on that, and a hardcoded `parentFile.parentFile` silently
     * resolved to a nonexistent path — which the old `?: return` then turned into
     * three tests that passed without asserting anything.
     *
     * Fails loudly instead. If this cannot find the registry, the three tests below
     * are not verifying the shipped config and must not report green.
     */
    private fun canonicalYaml(): File {
        val start = File(System.getProperty("user.dir"))
        val found = generateSequence(start) { it.parentFile }
            .map { File(it, "config/instruments.yaml") }
            .firstOrNull { it.isFile }

        assertNotNull(
            found,
            "config/instruments.yaml not found walking up from user.dir=$start — " +
                "these tests must assert against the shipped registry, not skip silently"
        )
        return found
    }

    @Test
    fun `loads binance native symbols from valid yaml`() {
        val path = writeYaml(
            """
            version: 2
            instruments:
              binance:
                - { native: BTCUSDT, canonical: BTC/USDT }
                - { native: ETHUSDT, canonical: ETH/USDT }
                - { native: SOLUSDT, canonical: SOL/USDT }
              kraken:
                - { native: XBT/USD, canonical: BTC/USD }
                - { native: ETH/USD, canonical: ETH/USD }
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("binance")

        assertEquals(listOf("BTCUSDT", "ETHUSDT", "SOLUSDT"), symbols)
    }

    @Test
    fun `returns native symbols not canonical ones`() {
        // The whole point of the v2 shape: the subscribe frame must carry XBT/USD,
        // not the BTC/USD the rest of the platform keys on. Swapping the two here
        // would produce a handler that connects and silently receives nothing.
        val path = writeYaml(
            """
            version: 2
            instruments:
              kraken:
                - { native: XBT/USD, canonical: BTC/USD }
                - { native: XDG/USD, canonical: DOGE/USD }
                - { native: SOL/USD, canonical: SOL/USD }
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("kraken")

        assertEquals(listOf("XBT/USD", "XDG/USD", "SOL/USD"), symbols)
    }

    @Test
    fun `loads coinbase native symbols from valid yaml`() {
        val path = writeYaml(
            """
            version: 2
            instruments:
              coinbase:
                - { native: BTC-USD, canonical: BTC/USD }
                - { native: ETH-USD, canonical: ETH/USD }
                - { native: SOL-USD, canonical: SOL/USD }
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("coinbase")

        assertEquals(listOf("BTC-USD", "ETH-USD", "SOL-USD"), symbols)
    }

    @Test
    fun `accepts the optional book_depth override`() {
        // No instrument sets book_depth today; kaml runs strict, so an unknown key
        // fails the entire file rather than one entry. This test is what stops the
        // documented escape hatch from being unusable when someone first reaches for it.
        val path = writeYaml(
            """
            version: 2
            instruments:
              coinbase:
                - { native: BTC-USD, canonical: BTC/USD, book_depth: 50 }
                - { native: ETH-USD, canonical: ETH/USD }
            """.trimIndent()
        )

        assertEquals(listOf("BTC-USD", "ETH-USD"), InstrumentsLoader(path).loadForExchange("coinbase"))
    }

    @Test
    fun `exchange lookup is case-insensitive`() {
        val path = writeYaml(
            """
            version: 2
            instruments:
              binance:
                - { native: BTCUSDT, canonical: BTC/USDT }
            """.trimIndent()
        )

        assertEquals(listOf("BTCUSDT"), InstrumentsLoader(path).loadForExchange("Binance"))
        assertEquals(listOf("BTCUSDT"), InstrumentsLoader(path).loadForExchange("BINANCE"))
    }

    @Test
    fun `rejects version 1`() {
        // A v1 file lists bare strings and keeps the canonical mapping in code.
        // Reading it as v2 must fall back to K2_SYMBOLS, never subscribe to nothing.
        val path = writeYaml(
            """
            version: 1
            instruments:
              binance:
                - { native: BTCUSDT, canonical: BTC/USDT }
            """.trimIndent()
        )

        assertTrue(InstrumentsLoader(path).loadForExchange("binance").isEmpty())
    }

    @Test
    fun `rejects the v1 flat string shape`() {
        val path = writeYaml(
            """
            instruments:
              binance:
                symbols:
                  - BTCUSDT
                  - ETHUSDT
            """.trimIndent()
        )

        assertTrue(InstrumentsLoader(path).loadForExchange("binance").isEmpty())
    }

    @Test
    fun `returns empty list when exchange not found`() {
        val path = writeYaml(
            """
            version: 2
            instruments:
              binance:
                - { native: BTCUSDT, canonical: BTC/USDT }
            """.trimIndent()
        )

        assertTrue(InstrumentsLoader(path).loadForExchange("coinbase").isEmpty())
    }

    @Test
    fun `returns empty list when file does not exist`() {
        val symbols = InstrumentsLoader("/nonexistent/path/instruments.yaml").loadForExchange("binance")

        assertTrue(symbols.isEmpty())
    }

    @Test
    fun `returns empty list on malformed yaml`() {
        val path = writeYaml("this: is: not: valid: yaml: [[[")

        assertTrue(InstrumentsLoader(path).loadForExchange("binance").isEmpty())
    }

    @Test
    fun `loads all 12 binance pairs from canonical instruments yaml`() {
        val file = canonicalYaml()

        val symbols = InstrumentsLoader(file.absolutePath).loadForExchange("binance")
        assertEquals(12, symbols.size, "Expected 12 Binance pairs")
        assertTrue(symbols.contains("BTCUSDT"))
        assertTrue(symbols.contains("SOLUSDT"))
        assertTrue(symbols.contains("XRPUSDT"))
    }

    @Test
    fun `loads all 11 kraken pairs from canonical instruments yaml`() {
        val file = canonicalYaml()

        val symbols = InstrumentsLoader(file.absolutePath).loadForExchange("kraken")
        assertEquals(11, symbols.size, "Expected 11 Kraken pairs")
        assertTrue(symbols.contains("XBT/USD"), "Kraken subscribes with XBT, not BTC")
        assertTrue(symbols.contains("XDG/USD"), "Kraken subscribes with XDG, not DOGE")
    }

    @Test
    fun `loads all 11 coinbase pairs from canonical instruments yaml`() {
        val file = canonicalYaml()

        val symbols = InstrumentsLoader(file.absolutePath).loadForExchange("coinbase")
        assertEquals(11, symbols.size, "Expected 11 Coinbase pairs")
        assertTrue(symbols.contains("BTC-USD"))
        assertTrue(symbols.contains("ETH-USD"))
        assertTrue(symbols.contains("DOGE-USD"))
    }
}
