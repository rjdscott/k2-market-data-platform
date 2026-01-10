"""FastAPI dependency injection for K2 API.

This module provides dependency injection for:
- QueryEngine: DuckDB query engine for market data
- ReplayEngine: Time-travel and historical replay
- Authentication: API key validation

Using FastAPI's Depends() pattern ensures:
- Proper resource lifecycle management
- Thread-safe connection handling
- Easy testing with dependency overrides
"""

from typing import Generator, Optional
from functools import lru_cache

from k2.query.engine import QueryEngine
from k2.query.replay import ReplayEngine
from k2.common.logging import get_logger

logger = get_logger(__name__, component="api")

# Global engine instances (singleton pattern)
_query_engine: Optional[QueryEngine] = None
_replay_engine: Optional[ReplayEngine] = None


@lru_cache(maxsize=1)
def get_query_engine() -> QueryEngine:
    """
    Get or create the QueryEngine singleton.

    Uses lru_cache to ensure single instance across all requests.
    The engine is initialized on first access and reused.

    Returns:
        QueryEngine instance with DuckDB connection
    """
    global _query_engine
    if _query_engine is None:
        logger.info("Initializing QueryEngine")
        _query_engine = QueryEngine()
    return _query_engine


@lru_cache(maxsize=1)
def get_replay_engine() -> ReplayEngine:
    """
    Get or create the ReplayEngine singleton.

    Uses lru_cache to ensure single instance across all requests.

    Returns:
        ReplayEngine instance for time-travel queries
    """
    global _replay_engine
    if _replay_engine is None:
        logger.info("Initializing ReplayEngine")
        _replay_engine = ReplayEngine()
    return _replay_engine


def reset_engines() -> None:
    """
    Reset engine singletons (for testing).

    Clears the cached engines so they will be recreated on next access.
    """
    global _query_engine, _replay_engine

    if _query_engine is not None:
        try:
            _query_engine.close()
        except Exception:
            pass
        _query_engine = None

    if _replay_engine is not None:
        try:
            _replay_engine.close()
        except Exception:
            pass
        _replay_engine = None

    # Clear lru_cache
    get_query_engine.cache_clear()
    get_replay_engine.cache_clear()

    logger.info("Engines reset")


async def startup_engines() -> None:
    """
    Initialize engines on application startup.

    Called during FastAPI startup event to warm up connections.
    """
    logger.info("Starting up API engines")
    try:
        # Initialize query engine (this creates DuckDB connection)
        get_query_engine()
        logger.info("QueryEngine initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize QueryEngine", error=str(e))
        # Don't raise - allow API to start, queries will fail with clear error

    try:
        # Initialize replay engine
        get_replay_engine()
        logger.info("ReplayEngine initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize ReplayEngine", error=str(e))


async def shutdown_engines() -> None:
    """
    Clean up engines on application shutdown.

    Called during FastAPI shutdown event to close connections.
    """
    logger.info("Shutting down API engines")
    reset_engines()
    logger.info("API engines shut down")
