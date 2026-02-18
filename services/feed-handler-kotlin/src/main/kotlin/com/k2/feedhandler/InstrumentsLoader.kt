package com.k2.feedhandler

import com.charleskorn.kaml.Yaml
import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.serialization.Serializable
import java.io.File

private val logger = KotlinLogging.logger {}

@Serializable
data class InstrumentConfig(
    val instruments: Map<String, ExchangeInstruments>
)

@Serializable
data class ExchangeInstruments(
    val symbols: List<String>
)

/**
 * Loads instrument subscriptions from instruments.yaml.
 *
 * Designed for the fallback chain in Main.kt:
 *   instruments.yaml → K2_SYMBOLS env var → application.conf defaults
 */
class InstrumentsLoader(private val filePath: String) {

    /**
     * Returns symbols for [exchange] (case-insensitive lookup).
     * Returns empty list if the exchange is not found or file cannot be parsed —
     * the caller is responsible for falling back to K2_SYMBOLS.
     */
    fun loadForExchange(exchange: String): List<String> {
        val file = File(filePath)
        if (!file.exists()) {
            logger.warn { "instruments.yaml not found at $filePath — falling back to K2_SYMBOLS" }
            return emptyList()
        }

        return try {
            val config = Yaml.default.decodeFromString(InstrumentConfig.serializer(), file.readText())
            val exchangeKey = exchange.lowercase()
            val symbols = config.instruments[exchangeKey]?.symbols ?: emptyList()

            if (symbols.isEmpty()) {
                logger.warn { "No symbols found for exchange '$exchange' in $filePath — falling back to K2_SYMBOLS" }
            } else {
                logger.info { "Loaded ${symbols.size} symbols for '$exchange' from instruments.yaml: ${symbols.joinToString(", ")}" }
            }
            symbols
        } catch (e: Exception) {
            logger.error(e) { "Failed to parse instruments.yaml at $filePath — falling back to K2_SYMBOLS" }
            emptyList()
        }
    }
}
