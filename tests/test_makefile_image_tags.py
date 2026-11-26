"""A test target must not build a tag the running stack starts services from.

`make test-airflow` used to build `k2-airflow:3.3.2`, the tag the four Airflow
services run, so a test run from any branch replaced the live image (P2 audit).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_airflow_tests_build_a_tag_no_service_runs():
    compose_tags = set(re.findall(r"^\s*image:\s*(\S+)", (ROOT / "docker-compose.yml").read_text(), re.M))
    default = re.search(r"^AIRFLOW_IMAGE \?= (\S+)$", (ROOT / "Makefile").read_text(), re.M).group(1)
    assert "k2-airflow:3.3.2" in compose_tags  # the guard still looks at the right thing
    assert default not in compose_tags
