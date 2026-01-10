"""
Structured logging with correlation IDs.

All logs are JSON-formatted for easy parsing in Grafana Loki / ELK.
Uses structlog for structured logging with context propagation.

Usage:
    from k2.common.logging import get_logger

    logger = get_logger(__name__, component="ingestion")

    # Simple logging
    logger.info("Processing batch", symbol="BHP", record_count=1000)

    # With correlation ID context
    from k2.common.logging import set_correlation_id, clear_correlation_id

    set_correlation_id("request-123")
    logger.info("Started processing")  # Automatically includes correlation_id
    clear_correlation_id()

    # With timing
    with logger.timer("batch_processing"):
        process_batch()
"""

import contextvars
import logging
import sys
import time
from typing import Any, Dict, Optional
from contextlib import contextmanager

import structlog
from structlog.types import EventDict, Processor


# Context variables for correlation ID and request ID
# These are thread-safe and async-friendly
_correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


# ==============================================================================
# Context Management
# ==============================================================================


def set_correlation_id(correlation_id: str) -> None:
    """
    Set correlation ID for current context.

    The correlation ID will be automatically included in all log messages
    within the current context (thread or async task).

    Args:
        correlation_id: Unique identifier for tracing related operations
    """
    _correlation_id_var.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """Get current correlation ID from context."""
    return _correlation_id_var.get()


def clear_correlation_id() -> None:
    """Clear correlation ID from current context."""
    _correlation_id_var.set(None)


def set_request_id(request_id: str) -> None:
    """
    Set request ID for current context (API requests only).

    Args:
        request_id: Unique identifier for HTTP/API request
    """
    _request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """Get current request ID from context."""
    return _request_id_var.get()


def clear_request_id() -> None:
    """Clear request ID from current context."""
    _request_id_var.set(None)


# ==============================================================================
# Structlog Processors
# ==============================================================================


def add_correlation_ids(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Add correlation_id and request_id to log events from context.

    This processor automatically includes correlation IDs in all log messages
    without requiring them to be passed explicitly.
    """
    correlation_id = get_correlation_id()
    if correlation_id:
        event_dict["correlation_id"] = correlation_id

    request_id = get_request_id()
    if request_id:
        event_dict["request_id"] = request_id

    return event_dict


def add_timestamp(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Add ISO 8601 timestamp to log events.

    Uses UTC timezone for consistency across regions.
    """
    import datetime

    event_dict["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return event_dict


def add_log_level(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Add log level to event dict.

    Converts method_name (info, warning, error) to uppercase level.
    """
    if method_name == "msg":
        # Called from logger.msg(), use stored level
        level = event_dict.pop("level", "INFO")
    else:
        # Called from logger.info(), logger.error(), etc.
        level = method_name.upper()

    event_dict["level"] = level
    return event_dict


# ==============================================================================
# Logger Configuration
# ==============================================================================


def configure_logging(
    json_output: bool = False,
    log_level: str = "INFO",
) -> None:
    """
    Configure structlog for the application.

    Should be called once at application startup.

    Args:
        json_output: If True, output JSON logs. If False, use console-friendly format.
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Shared processors for all configurations
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_correlation_ids,
        add_timestamp,
        add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # Production: JSON output for log aggregation systems
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: Console-friendly colored output
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ==============================================================================
# Logger Factory
# ==============================================================================


class K2Logger:
    """
    K2 Platform logger with additional convenience methods.

    Wraps structlog BoundLogger with K2-specific functionality like
    timing context managers and standard field helpers.
    """

    def __init__(self, logger: structlog.BoundLogger, component: Optional[str] = None):
        """
        Initialize K2 logger.

        Args:
            logger: Structlog bound logger
            component: Component name (e.g., "ingestion", "storage", "query")
        """
        self._logger = logger
        self._component = component

        # Bind component if provided
        if component:
            self._logger = self._logger.bind(component=component)

    def bind(self, **kwargs: Any) -> "K2Logger":
        """
        Bind additional context to logger.

        Returns a new logger with the additional context.

        Example:
            request_logger = logger.bind(request_id="123", user_id="456")
            request_logger.info("Processing request")
        """
        return K2Logger(self._logger.bind(**kwargs), self._component)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message with context."""
        self._logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message with context."""
        self._logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message with context."""
        self._logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message with context."""
        self._logger.error(message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log critical message with context."""
        self._logger.critical(message, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception with traceback."""
        self._logger.exception(message, **kwargs)

    @contextmanager
    def timer(self, operation: str, **context: Any):
        """
        Context manager for timing operations.

        Logs operation start, duration, and any context on completion.

        Example:
            with logger.timer("batch_write", symbol="BHP", records=1000):
                write_batch()

        Output:
            {"level": "INFO", "message": "Operation completed",
             "operation": "batch_write", "duration_ms": 150.5,
             "symbol": "BHP", "records": 1000}
        """
        start_time = time.perf_counter()
        self.debug(f"Starting {operation}", operation=operation, **context)

        try:
            yield
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.error(
                f"Operation failed: {operation}",
                operation=operation,
                duration_ms=duration_ms,
                error=str(e),
                **context,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.info(
                f"Operation completed: {operation}",
                operation=operation,
                duration_ms=duration_ms,
                **context,
            )


def get_logger(
    name: str,
    component: Optional[str] = None,
    **initial_context: Any,
) -> K2Logger:
    """
    Get a structured logger for a module.

    Args:
        name: Module name (typically __name__)
        component: Component name (e.g., "ingestion", "storage", "query")
        **initial_context: Additional context to bind to logger

    Returns:
        Configured K2Logger instance

    Example:
        logger = get_logger(__name__, component="ingestion", exchange="asx")
        logger.info("Processing batch", symbol="BHP", records=1000)

        Output (JSON):
        {
            "timestamp": "2026-01-10T12:34:56.789Z",
            "level": "INFO",
            "logger": "k2.ingestion.consumer",
            "component": "ingestion",
            "exchange": "asx",
            "message": "Processing batch",
            "symbol": "BHP",
            "records": 1000
        }
    """
    # Get structlog logger
    base_logger = structlog.get_logger(name)

    # Bind initial context
    if initial_context:
        base_logger = base_logger.bind(**initial_context)

    return K2Logger(base_logger, component)


# ==============================================================================
# Initialize logging on module import
# ==============================================================================

# Default configuration (console output for development)
# Call configure_logging(json_output=True) in production
configure_logging(json_output=False, log_level="INFO")
