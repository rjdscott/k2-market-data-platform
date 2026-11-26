"""
Unit test configuration for tests/.

Adds the lake script directory to sys.path so tests can import offsets.py,
wire.py and the other pure modules there by name: no pyspark, no network.

Note: we cannot use a `docker.lake.xxx` package path, because the `docker`
namespace is already claimed by the docker-sdk PyPI package. sys.path injection
is the standard pattern for this project's Spark-image scripts, which run with
their own directory on sys.path rather than as a package.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(_PROJECT_ROOT / "docker" / "lake"))


def import_lake_module(name: str):
    """A docker/lake module that imports pyspark at module level, for its pure parts.

    The root suite has no pyspark, so a bare stand-in takes its place unless the
    real one is installed; a test that reached Spark through it would fail on
    the stand-in, never touch a stack.
    """
    import importlib
    import types

    try:
        import pyspark.sql  # noqa: F401
    except ImportError:
        sql = sys.modules.setdefault("pyspark.sql", types.ModuleType("pyspark.sql"))
        sys.modules.setdefault("pyspark", types.ModuleType("pyspark")).sql = sql
        for attr in ("DataFrame", "Row", "Window", "functions", "SparkSession"):
            if not hasattr(sql, attr):
                setattr(sql, attr, object())
    return importlib.import_module(name)
