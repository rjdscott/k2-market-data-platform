"""K2 API - REST API for Market Data Platform.

This package provides a production-ready REST API for the K2 platform:
- FastAPI-based REST endpoints
- API key authentication
- Rate limiting (100 req/min)
- Request correlation IDs
- Response caching
- OpenAPI documentation

Usage:
    # Run development server
    uvicorn k2.api.main:app --reload --port 8000

    # Or use the Makefile
    make api

Endpoints:
    - GET /health - Health check (no auth)
    - GET /v1/trades - Query trades
    - GET /v1/quotes - Query quotes
    - GET /v1/summary/{symbol}/{date} - OHLCV summary
    - GET /v1/symbols - List symbols
    - GET /v1/stats - Database stats
    - GET /v1/snapshots - Iceberg snapshots
"""

from k2.api.main import app

__all__ = ["app"]
