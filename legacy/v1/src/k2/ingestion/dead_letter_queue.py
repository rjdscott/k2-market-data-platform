"""Dead Letter Queue for failed messages.

Handles messages that cannot be processed due to permanent errors:
- Schema validation failures
- Invalid data formats
- Business logic violations

Design Decisions:
- JSONL format for easy reprocessing
- Separate files by timestamp for partitioning
- Include full error context for debugging
- Automatic rotation at configurable size

Usage:
    dlq = DeadLetterQueue(path=Path("/var/k2/dlq"))

    try:
        process_message(msg)
    except ValueError as err:
        # Permanent error - send to DLQ
        dlq.write(
            messages=[msg],
            error=str(err),
            error_type="validation"
        )
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from k2.common.logging import get_logger

logger = get_logger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime and Decimal objects.

    Converts:
    - datetime objects to ISO 8601 format strings
    - Decimal objects to strings (preserving precision)

    This is essential for DLQ messages that may contain these types
    (datetime from timestamps, Decimal from price/quantity fields).

    Example:
        >>> json.dumps({
        ...     "timestamp": datetime.utcnow(),
        ...     "price": Decimal("43250.50")
        ... }, cls=DateTimeEncoder)
        '{"timestamp": "2024-01-15T10:30:45.123456", "price": "43250.50"}'
    """

    def default(self, obj):
        """Override default JSON encoding to handle datetime and Decimal objects.

        Args:
            obj: Object to encode

        Returns:
            ISO format string for datetime, string for Decimal, default encoding otherwise
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)  # Preserve precision as string
        return super().default(obj)


class DeadLetterQueue:
    """Write failed messages to DLQ for later reprocessing.

    Messages are written in JSONL format with full error context:
    - Original message payload
    - Error message and type
    - Timestamp of failure
    - Consumer group and topic metadata

    Files are rotated when they exceed max_size_mb.
    """

    def __init__(
        self,
        path: Path | str,
        max_size_mb: int = 100,
        enable_rotation: bool = True,
    ):
        """Initialize DLQ writer.

        Args:
            path: Directory to write DLQ files
            max_size_mb: Maximum file size before rotation (default: 100MB)
            enable_rotation: Enable automatic file rotation (default: True)
        """
        self.path = Path(path)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.enable_rotation = enable_rotation

        # Create DLQ directory
        self.path.mkdir(parents=True, exist_ok=True)

        # Current file handle
        self._current_file: Path | None = None
        self._current_size: int = 0

        logger.info(
            "DLQ initialized",
            path=str(self.path),
            max_size_mb=max_size_mb,
            enable_rotation=enable_rotation,
        )

    def write(
        self,
        messages: list[dict[str, Any]],
        error: str,
        error_type: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Write failed messages to DLQ.

        Args:
            messages: List of message payloads
            error: Error message
            error_type: Classification of error (validation, schema, timeout, etc.)
            metadata: Additional metadata (consumer_group, topic, etc.)

        Returns:
            Path to DLQ file written

        Example:
            dlq.write(
                messages=[{"symbol": "BHP", "price": -1.0}],
                error="Price must be positive",
                error_type="validation",
                metadata={"consumer_group": "k2-trades", "topic": "market.trades"}
            )
        """
        timestamp = datetime.utcnow()

        # Determine output file
        dlq_file = self._get_or_create_file(timestamp)

        # Write each message with error context
        entries_written = 0
        with open(dlq_file, "a") as f:
            for message in messages:
                entry = {
                    "timestamp": timestamp.isoformat(),
                    "error": error,
                    "error_type": error_type,
                    "message": message,
                    "metadata": metadata or {},
                }

                # Use DateTimeEncoder to handle datetime objects in message payload
                line = json.dumps(entry, cls=DateTimeEncoder) + "\n"
                f.write(line)
                self._current_size += len(line.encode("utf-8"))
                entries_written += 1

        logger.warning(
            "Messages written to DLQ",
            file=str(dlq_file),
            count=entries_written,
            error_type=error_type,
        )

        return dlq_file

    def _get_or_create_file(self, timestamp: datetime) -> Path:
        """Get current DLQ file or create new one if rotation needed.

        Args:
            timestamp: Current timestamp for file naming

        Returns:
            Path to DLQ file to write to
        """
        # Check if rotation needed
        if (
            self.enable_rotation
            and self._current_file
            and self._current_size >= self.max_size_bytes
        ):
            logger.info(
                "DLQ file size limit reached, rotating",
                current_file=str(self._current_file),
                size_mb=self._current_size / (1024 * 1024),
            )
            self._current_file = None
            self._current_size = 0

        # Create new file if needed
        if self._current_file is None:
            filename = f"dlq_{timestamp.strftime('%Y%m%d_%H%M%S')}.jsonl"
            self._current_file = self.path / filename
            self._current_size = 0

            logger.info("Created new DLQ file", file=str(self._current_file))

        return self._current_file

    def get_stats(self) -> dict[str, Any]:
        """Get DLQ statistics.

        Returns:
            Dictionary with file count, total size, oldest/newest files
        """
        dlq_files = list(self.path.glob("dlq_*.jsonl"))

        if not dlq_files:
            return {
                "file_count": 0,
                "total_size_mb": 0,
                "oldest_file": None,
                "newest_file": None,
            }

        # Sort by modification time
        dlq_files.sort(key=lambda p: p.stat().st_mtime)

        total_size = sum(f.stat().st_size for f in dlq_files)

        return {
            "file_count": len(dlq_files),
            "total_size_mb": total_size / (1024 * 1024),
            "oldest_file": str(dlq_files[0]),
            "newest_file": str(dlq_files[-1]),
        }

    def list_files(self) -> list[Path]:
        """List all DLQ files in chronological order.

        Returns:
            List of DLQ file paths (oldest first)
        """
        dlq_files = list(self.path.glob("dlq_*.jsonl"))
        dlq_files.sort(key=lambda p: p.stat().st_mtime)
        return dlq_files

    def read_file(self, file_path: Path) -> list[dict[str, Any]]:
        """Read and parse a DLQ file.

        Args:
            file_path: Path to DLQ file

        Returns:
            List of DLQ entries

        Example:
            entries = dlq.read_file(Path("/var/k2/dlq/dlq_20240115_103045.jsonl"))
            for entry in entries:
                print(f"Error: {entry['error']}")
                print(f"Message: {entry['message']}")
        """
        entries = []
        with open(file_path) as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
        return entries


def create_dlq(
    path: Path | str | None = None,
    max_size_mb: int = 100,
) -> DeadLetterQueue:
    """Factory function to create DLQ with default configuration.

    Args:
        path: DLQ directory (default: /var/k2/dlq or configured path)
        max_size_mb: Maximum file size before rotation

    Returns:
        Configured DeadLetterQueue instance
    """
    from k2.common.config import config

    if path is None:
        path = config.dlq_path if hasattr(config, "dlq_path") else Path("/var/k2/dlq")

    return DeadLetterQueue(path=path, max_size_mb=max_size_mb)
