# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K2 Market Data Feed Handler - Multi-stage Docker Build
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Stage 1: Build
FROM gradle:8.12-jdk21 AS build

WORKDIR /app

# Copy Gradle files first (layer caching)
COPY legacy/v2-kotlin/build.gradle.kts legacy/v2-kotlin/settings.gradle.kts legacy/v2-kotlin/gradle.properties ./

# Copy source code
COPY legacy/v2-kotlin/src ./src

# Copy Avro schemas
COPY schemas /app/schemas

# Build fat JAR
RUN gradle fatJar --no-daemon

# Stage 2: Runtime
FROM eclipse-temurin:21-jre-alpine

LABEL maintainer="K2 Platform Engineering"
LABEL description="ARCHIVED v2 Kotlin feed handler - see legacy/v2-kotlin/README.md"

WORKDIR /app

# Install curl for healthchecks
RUN apk add --no-cache curl

# Copy fat JAR from build stage
COPY --from=build /app/build/libs/*-all.jar /app/feed-handler.jar

# Copy schemas from build stage
COPY --from=build /app/schemas /app/schemas

# Create logs directory
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Non-root user for security
RUN addgroup -g 1000 k2 && \
    adduser -D -u 1000 -G k2 k2 && \
    chown -R k2:k2 /app

USER k2

# Metrics + health endpoints (Micrometer / Ktor)
EXPOSE 8082

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS http://localhost:8082/health || exit 1

# Run the application
ENTRYPOINT ["java", "-jar", "/app/feed-handler.jar"]
