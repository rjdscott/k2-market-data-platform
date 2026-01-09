"""
Structured logging with correlation IDs.

All logs are JSON-formatted for easy parsing in Grafana Loki / ELK.
"""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Get a structured logger for a module.

    In production, configure JSON formatter with correlation IDs.
    For now, returns standard Python logger.

    Args:
        name: Module name (typically __name__)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger