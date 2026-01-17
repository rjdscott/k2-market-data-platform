"""Soak tests-backup for long-running stability validation.

This package contains endurance tests-backup that run for extended periods (hours to days)
to validate system stability, memory growth, and connection reliability.

Test Types:
- 24h soak test: Full production validation (test_binance_24h_soak.py)
- 1h validation: Quick stability check (test_binance_1h_validation.py)

Usage:
    # Run 24h soak test
    uv run pytest tests-backup/soak/test_binance_24h_soak.py --timeout=90000 -v -s

    # Run 1h validation
    uv run pytest tests-backup/soak/test_binance_1h_validation.py --timeout=4000 -v -s
"""
