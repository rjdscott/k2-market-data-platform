"""
apply_ddl.py's file order: lake.sql, then the DDL `k2gen generate` writes.

lake.sql is the DDL of record for every table it creates (ADR-032), and the
generated files are new products' tables, in name order so two runs apply the
same sequence. apply_ddl.py imports spark_conf, which imports pyspark, so the
module comes in through the stub tests/test_lake_bronze.py uses.
"""

import sys
import types

import pytest


@pytest.fixture(scope="module")
def apply_ddl():
    for name in ("pyspark", "pyspark.sql"):
        sys.modules.setdefault(name, types.ModuleType(name))
    if not hasattr(sys.modules["pyspark.sql"], "SparkSession"):
        sys.modules["pyspark.sql"].SparkSession = object()
    import apply_ddl as module

    return module


def test_lake_sql_first_then_generated_in_name_order(apply_ddl, tmp_path, monkeypatch):
    generated = tmp_path / "generated"
    generated.mkdir()
    for name in ("gold.zeta.sql", "README.md", "bronze.alpha.sql"):
        (generated / name).write_text("")
    monkeypatch.setattr(apply_ddl, "GENERATED_DIR", generated)
    assert apply_ddl.ddl_files() == [apply_ddl.DDL_FILE, generated / "bronze.alpha.sql", generated / "gold.zeta.sql"]


def test_no_generated_directory_is_lake_sql_alone(apply_ddl, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_ddl, "GENERATED_DIR", tmp_path / "absent")
    assert apply_ddl.ddl_files() == [apply_ddl.DDL_FILE]
