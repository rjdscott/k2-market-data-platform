"""
The image's dbt against the checkout's locks: the dbt virtualenv and the dbt packages
are built into the image (docker/airflow/Dockerfile), while dbt/ is mounted from the
checkout, and the tag never changes. An image older than dbt/uv.lock or
dbt/package-lock.yml fails here.

    make test-airflow     # inside the image it has just built

and against the image the stack runs, to see whether it needs a rebuild:

    docker run --rm --network none -v "$PWD":/repo:ro k2-airflow:3.3.2 \\
      python -m unittest discover -s /repo/docker/airflow/tests -p 'test_dbt_image.py'
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

import tomllib
import yaml

REPO = Path(__file__).resolve().parents[3]
ENV = Path("/opt/k2/dbt-env")
VENV_PYTHON = "/opt/k2/dbt-venv/bin/python"
PACKAGES = Path("/opt/k2/dbt_packages")
INSTALLED = "import importlib.metadata as m, json; print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))"


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class DbtImage(unittest.TestCase):
    def test_every_python_package_in_the_dbt_virtualenv_is_the_version_uv_lock_pins(self):
        lock = {norm(p["name"]): p["version"] for p in tomllib.loads((REPO / "dbt/uv.lock").read_text())["package"] if "version" in p}
        out = subprocess.run([VENV_PYTHON, "-c", INSTALLED], capture_output=True, text=True, check=True).stdout
        installed = {norm(k): v for k, v in json.loads(out).items()}
        self.assertEqual((installed["dbt-core"], installed["dbt-trino"]), (lock["dbt-core"], lock["dbt-trino"]))
        self.assertEqual({k: lock.get(k) for k in installed}, installed)

    def test_the_dbt_packages_were_installed_from_this_package_lock(self):
        locked = yaml.safe_load((REPO / "dbt/package-lock.yml").read_text())
        self.assertEqual(yaml.safe_load((ENV / "package-lock.yml").read_text()), locked)
        self.assertEqual(sorted(p.name for p in PACKAGES.iterdir()), sorted(p["name"] for p in locked["packages"]))


if __name__ == "__main__":
    unittest.main()
