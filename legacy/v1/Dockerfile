# ==============================================================================
# K2 Market Data Platform - Multi-Stage Docker Build
# ==============================================================================
# Optimized for: Binance streaming service, consumers, API servers
# Build time: ~3-5 minutes (with layer caching)
# Final image size: ~500-600 MB (Python 3.13 + dependencies)
#
# Build: docker build -t k2-platform:latest .
# Run:   docker compose up -d
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Base Image with UV Package Manager
# ------------------------------------------------------------------------------
FROM python:3.14-slim AS base

# Set environment variables for Python behavior
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies required for Python packages
# - gcc, g++: Required for compiling C extensions (confluent-kafka, psycopg2)
# - librdkafka-dev: Kafka C library (required by confluent-kafka)
# - libpq-dev: PostgreSQL library (required by psycopg2)
# - curl: For health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    librdkafka-dev \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager (modern, fast alternative to pip)
# UV is 10-100x faster than pip for dependency resolution
RUN pip install uv

# Set working directory
WORKDIR /app

# ------------------------------------------------------------------------------
# Stage 2: Dependencies Installation
# ------------------------------------------------------------------------------
FROM base AS dependencies

# Declare build arguments that can be passed from docker-compose
ARG INSTALL_API=false

# Copy only dependency files first (leverages Docker layer caching)
# This layer is cached unless pyproject.toml changes
COPY pyproject.toml .
COPY .python-version .

# Copy source code (required for editable install)
COPY src/ /app/src/

# Install project dependencies using UV
# --system: Install into system Python (not venv, since container is isolated)
# --no-cache-dir: Don't cache pip packages (reduces image size)
# -e .: Install in editable mode (allows code changes without rebuild for development)
RUN if [ "${INSTALL_API:-false}" = "true" ]; then \
      echo "Installing API dependencies with [api] extras"; \
      uv pip install --system --no-cache-dir -e ".[api]"; \
    else \
      echo "Installing base dependencies only"; \
      uv pip install --system --no-cache-dir -e .; \
    fi

# ------------------------------------------------------------------------------
# Stage 3: Final Runtime Image
# ------------------------------------------------------------------------------
FROM base AS runtime

# Copy installed dependencies from dependencies stage
COPY --from=dependencies /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# Copy application source code
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY config/ /app/config/
COPY pyproject.toml .
COPY .python-version .

# Set PYTHONPATH so Python can find the k2 module
ENV PYTHONPATH=/app/src

# Create non-root user for security (follows Docker best practices)
# Running as root in containers is a security risk
RUN useradd --create-home --shell /bin/bash k2user && \
    chown -R k2user:k2user /app

# Switch to non-root user
USER k2user

# Expose ports (documentation only, actual ports configured in docker-compose.v1.yml)
# 9091: Prometheus metrics endpoint (binance-stream)
# 8000: FastAPI REST API (if running API server)
EXPOSE 9091 8000

# Health check (override in docker-compose.v1.yml for specific services)
# This is a default health check that can be customized per service
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9091/metrics || exit 1

# Default command (override in docker-compose.v1.yml for specific services)
# This serves as documentation and a fallback if no command is specified
CMD ["python", "scripts/binance_stream.py"]

# ==============================================================================
# Build Arguments (Optional)
# ==============================================================================
# Use build args to customize the image at build time:
# docker build --build-arg PYTHON_VERSION=3.13 -t k2-platform:latest .

ARG PYTHON_VERSION=3.13
ARG APP_VERSION=0.1.0
ARG BUILD_DATE
ARG VCS_REF

# Metadata labels (follows OCI image spec)
LABEL org.opencontainers.image.title="K2 Market Data Platform" \
      org.opencontainers.image.description="Real-time market data streaming platform with Kafka and Iceberg" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.vendor="K2 Platform" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/rjdscott/k2-market-data-platform" \
      maintainer="K2 Platform Team"

# ==============================================================================
# Usage Examples
# ==============================================================================
#
# Build image:
#   docker build -t k2-platform:latest .
#
# Run binance-stream service:
#   docker run --rm -e K2_KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
#              k2-platform:latest python scripts/binance_stream.py
#
# Run consumer service:
#   docker run --rm -e K2_KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
#              k2-platform:latest python scripts/consume_to_iceberg.py
#
# Run API server:
#   docker run --rm -p 8000:8000 \
#              k2-platform:latest uvicorn k2.api.main:app --host 0.0.0.0
#
# Interactive shell (debugging):
#   docker run --rm -it k2-platform:latest /bin/bash
#
# ==============================================================================
