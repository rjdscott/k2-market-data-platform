"""K2 Platform Tests.

Test organization:
- unit/: Fast unit tests-backup, no external dependencies
- integration/: Tests requiring Docker services
- performance/: Load tests-backup and benchmarks

Run tests-backup with pytest:
    pytest tests-backup/unit/ -v                # Unit tests-backup only
    pytest tests-backup/integration/ -v -m integration  # Integration tests-backup
    pytest tests-backup/ -v                     # All tests-backup
"""
