"""K2 Platform Tests.

Test organization:
- unit/: Fast unit tests, no external dependencies
- integration/: Tests requiring Docker services
- performance/: Load tests and benchmarks

Run tests with pytest:
    pytest tests/unit/ -v                # Unit tests only
    pytest tests/integration/ -v -m integration  # Integration tests
    pytest tests/ -v                     # All tests
"""
