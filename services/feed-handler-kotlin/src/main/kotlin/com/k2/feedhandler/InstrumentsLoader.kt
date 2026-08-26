package com.k2.feedhandler

import com.charleskorn.kaml.Yaml
import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.io.File

private val logger = KotlinLogging.logger {}

/** The only registry shape this loader accepts. See config/instruments.yaml. */
private const val SUPPORTED_VERSION = 2

@Serializable
data class InstrumentConfig(
    val version: Int,
    val instruments: Map<String, List<Instrument>>
)

/**
 * One instrument as declared in config/instruments.yaml.
 *
 * [canonical] and [bookDepth] are not read by this loader — the v2 feed handlers
 * only ever needed the subscribe string. They are declared anyway because kaml
 * runs in strict mode and rejects properties it has no field for, so omitting
 * them would make the whole file unparseable rather than partially useful.
 */
@Serializable
data class Instrument(
    val native: String,
    val canonical: String,
    @SerialName("book_depth") val bookDepth: Int? = null
)

/**
 * Loads instrument subscriptions from instruments.yaml (version 2).
 *
 * Designed for the fallback chain in Main.kt:
 *   instruments.yaml → K2_SYMBOLS env var → application.conf defaults
 */
class InstrumentsLoader(private val filePath: String) {

    /**
     * Returns the exchange-native subscribe strings for [exchange]
     * (case-insensitive lookup) — `BTCUSDT`, `XBT/USD`, `BTC-USD`.
     *
     * Returns an empty list if the file is missing, is not version
     * [SUPPORTED_VERSION], cannot be parsed, or has no entry for the exchange.
     * The caller is responsible for falling back to K2_SYMBOLS.
     */
    fun loadForExchange(exchange: String): List<String> {
        val file = File(filePath)
        if (!file.exists()) {
            logger.warn { "instruments.yaml not found at $filePath — falling back to K2_SYMBOLS" }
            return emptyList()
        }

        return try {
            val config = Yaml.default.decodeFromString(InstrumentConfig.serializer(), file.readText())

            // Reject rather than best-effort: v1 put the canonical mapping in code and
            // listed bare strings, so a v1 file read as v2 would silently subscribe to
            // nothing. A loud fallback to K2_SYMBOLS beats a handler with no symbols.
            if (config.version != SUPPORTED_VERSION) {
                logger.error {
                    "instruments.yaml at $filePath is version ${config.version}, " +
                        "expected $SUPPORTED_VERSION — falling back to K2_SYMBOLS"
                }
                return emptyList()
            }

            val symbols = config.instruments[exchange.lowercase()].orEmpty().map { it.native }

            if (symbols.isEmpty()) {
                logger.warn { "No instruments found for exchange '$exchange' in $filePath — falling back to K2_SYMBOLS" }
            } else {
                logger.info { "Loaded ${symbols.size} symbols for '$exchange' from instruments.yaml: ${symbols.joinToString(", ")}" }
            }
            symbols
        } catch (e: Exception) {
            logger.error(e) { "Failed to parse instruments.yaml at $filePath — falling back to K2_SYMBOLS" }
            emptyList()
        }
    }

    // ponytail: no canonicalFor() here, and TradeNormalizer keeps its hardcoded
    // mapping. Wiring the registry into it is not a small diff — TradeNormalizer is
    // a stateless `object` whose three normalize* functions are called from
    // {Binance,Kraken,Coinbase}WebSocketClient, so it would need constructing with a
    // loader and every call site changed, on code that Phase C retires to
    // legacy/v2-kotlin/. The canonical mapping is data for the Rust capture tier,
    // which is the only consumer that will still exist. The two agree today (asserted
    // by tests/test_contracts.py against the same file TradeNormalizerTest asserts
    // against); if they ever diverge before cutover, that is the signal to do the
    // wiring rather than a reason to have done it now.
}
