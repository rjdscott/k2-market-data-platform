"""
Unit test configuration for tests/unit/.

Adds the offload script directories to sys.path so tests can import
iceberg_maintenance_flow and iceberg_maintenance by module name.

Note: We cannot use the docker.offload.xxx package path because the 'docker'
namespace is already claimed by the docker-sdk PyPI package (used in the root
conftest.py).  sys.path injection is the standard pattern for this project's
offload scripts (see offload_generic.py).
"""

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# iceberg_maintenance_flow.py, iceberg_offload_flow.py, etc.
sys.path.insert(0, str(_PROJECT_ROOT / "docker" / "offload" / "flows"))

# iceberg_maintenance.py, offload_generic.py, watermark_pg.py, etc.
sys.path.insert(0, str(_PROJECT_ROOT / "docker" / "offload"))


@pytest.fixture(autouse=True)
def mock_prefect_run_logger():
    """
    Patch prefect.get_run_logger so task .fn() calls work outside a Prefect context.

    Prefect tasks call `from prefect import get_run_logger; logger = get_run_logger()`
    inside the function body.  Invoking .fn() in unit tests bypasses the Prefect
    executor so there is no active flow/task run context, causing MissingContextError.
    This fixture replaces get_run_logger with a standard Python logger for the
    duration of every unit test.
    """
    _logger = logging.getLogger("test.prefect")
    with patch("prefect.get_run_logger", return_value=_logger):
        yield
