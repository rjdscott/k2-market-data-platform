package com.k2.feedhandler

import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class InstrumentsLoaderTest {

    @TempDir
    lateinit var tempDir: Path

    private fun writeYaml(content: String): String {
        val file = File(tempDir.toFile(), "instruments.yaml")
        file.writeText(content)
        return file.absolutePath
    }

    @Test
    fun `loads binance symbols from valid yaml`() {
        val path = writeYaml(
            """
            instruments:
              binance:
                symbols:
                  - BTCUSDT
                  - ETHUSDT
                  - SOLUSDT
              kraken:
                symbols:
                  - XBT/USD
                  - ETH/USD
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("binance")

        assertEquals(listOf("BTCUSDT", "ETHUSDT", "SOLUSDT"), symbols)
    }

    @Test
    fun `loads kraken symbols from valid yaml`() {
        val path = writeYaml(
            """
            instruments:
              binance:
                symbols:
                  - BTCUSDT
              kraken:
                symbols:
                  - XBT/USD
                  - ETH/USD
                  - SOL/USD
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("kraken")

        assertEquals(listOf("XBT/USD", "ETH/USD", "SOL/USD"), symbols)
    }

    @Test
    fun `exchange lookup is case-insensitive`() {
        val path = writeYaml(
            """
            instruments:
              binance:
                symbols:
                  - BTCUSDT
            """.trimIndent()
        )

        assertEquals(listOf("BTCUSDT"), InstrumentsLoader(path).loadForExchange("Binance"))
        assertEquals(listOf("BTCUSDT"), InstrumentsLoader(path).loadForExchange("BINANCE"))
    }

    @Test
    fun `returns empty list when exchange not found`() {
        val path = writeYaml(
            """
            instruments:
              binance:
                symbols:
                  - BTCUSDT
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("coinbase")

        assertTrue(symbols.isEmpty())
    }

    @Test
    fun `returns empty list when file does not exist`() {
        val symbols = InstrumentsLoader("/nonexistent/path/instruments.yaml").loadForExchange("binance")

        assertTrue(symbols.isEmpty())
    }

    @Test
    fun `returns empty list on malformed yaml`() {
        val path = writeYaml("this: is: not: valid: yaml: [[[")

        val symbols = InstrumentsLoader(path).loadForExchange("binance")

        assertTrue(symbols.isEmpty())
    }

    @Test
    fun `loads all 12 binance pairs from canonical instruments yaml`() {
        val canonicalYaml = File(
            System.getProperty("user.dir")
                .let { dir ->
                    // Walk up from service dir to find config/instruments.yaml
                    File(dir).parentFile?.parentFile?.resolve("config/instruments.yaml")
                        ?: File(dir, "config/instruments.yaml")
                }
        )

        // Skip if running outside of project (CI environments)
        if (!canonicalYaml.exists()) return

        val symbols = InstrumentsLoader(canonicalYaml.absolutePath).loadForExchange("binance")
        assertEquals(12, symbols.size, "Expected 12 Binance pairs")
        assertTrue(symbols.contains("BTCUSDT"))
        assertTrue(symbols.contains("SOLUSDT"))
        assertTrue(symbols.contains("XRPUSDT"))
    }

    @Test
    fun `loads coinbase symbols from valid yaml`() {
        val path = writeYaml(
            """
            instruments:
              coinbase:
                symbols:
                  - BTC-USD
                  - ETH-USD
                  - SOL-USD
            """.trimIndent()
        )

        val symbols = InstrumentsLoader(path).loadForExchange("coinbase")

        assertEquals(listOf("BTC-USD", "ETH-USD", "SOL-USD"), symbols)
    }

    @Test
    fun `loads all 11 coinbase pairs from canonical instruments yaml`() {
        val canonicalYaml = File(
            System.getProperty("user.dir")
                .let { dir ->
                    File(dir).parentFile?.parentFile?.resolve("config/instruments.yaml")
                        ?: File(dir, "config/instruments.yaml")
                }
        )

        // Skip if running outside of project (CI environments)
        if (!canonicalYaml.exists()) return

        val symbols = InstrumentsLoader(canonicalYaml.absolutePath).loadForExchange("coinbase")
        assertEquals(11, symbols.size, "Expected 11 Coinbase pairs")
        assertTrue(symbols.contains("BTC-USD"))
        assertTrue(symbols.contains("ETH-USD"))
        assertTrue(symbols.contains("DOGE-USD"))
    }
}
