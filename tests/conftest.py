"""
Unit test configuration for tests/.

Adds the v3 lake script directory to sys.path so tests can import offsets.py and
wire.py by module name. Both are pure — no pyspark, no network — so they import
here even though ingest.py and maintenance.py next to them do not.

Note: we cannot use a `docker.lake.xxx` package path, because the `docker`
namespace is already claimed by the docker-sdk PyPI package. sys.path injection
is the standard pattern for this project's Spark-image scripts, which run with
their own directory on sys.path rather than as a package.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(_PROJECT_ROOT / "docker" / "lake"))

