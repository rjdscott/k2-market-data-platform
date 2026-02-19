package com.k2.feedhandler

import io.github.oshai.kotlinlogging.KotlinLogging
import io.ktor.server.application.*
import io.ktor.server.engine.*
import io.ktor.server.netty.*
import io.ktor.server.response.*
import io.ktor.server.routing.*
import io.micrometer.prometheusmetrics.PrometheusConfig
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry

private val logger = KotlinLogging.logger {}

/**
 * Global Prometheus registry shared across all components.
 * Lazily initialised once and reused for the process lifetime.
 */
val metricsRegistry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)

/**
 * Start a lightweight Ktor HTTP server that exposes /metrics for Prometheus scraping.
 *
 * @param port Port to listen on (default 8082)
 */
fun startMetricsServer(port: Int = 8082) {
    val server = embeddedServer(Netty, port = port, host = "0.0.0.0") {
        routing {
            get("/metrics") {
                call.respondText(metricsRegistry.scrape())
            }
            get("/health") {
                call.respondText("OK")
            }
        }
    }

    server.start(wait = false)
    logger.info { "📊 Metrics server started on port $port — http://0.0.0.0:$port/metrics" }
}
