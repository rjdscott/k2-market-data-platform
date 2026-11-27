"""docker/lake/spark_conf.py's session builder, run against a recording stand-in for
SparkSession.builder (no Spark): which key a session signs with and which catalogs it opens, and
the Spark image's redaction regex against every property it sets. Every variable with a
credential-shaped name (USER, PASSWORD, KEY, SECRET, TOKEN, CREDENTIAL) reads as a marked value, so
a credential added under a new name fails here before a job serves it in clear on the job UI, the
History Server or an event log. Spark blanks a value when the regex finds a match anywhere in the
property's name (Scala findFirstIn, which is re.search)."""

import re
import types
from pathlib import Path

import pytest
from conftest import import_lake_module

ROOT = Path(__file__).resolve().parent.parent
SPARK_3_5_9_DEFAULT = r"(?i)secret|password|token|access[.]?key"   # read from the image's JVM, 2026-11-26
CREDENTIAL = re.compile(r"USER|PASSWORD|KEY|SECRET|TOKEN|CREDENTIAL")


def image_regex() -> str:
    found = re.findall(r"'spark\.redaction\.regex\s+(\S+)'", (ROOT / "docker/spark/Dockerfile").read_text())
    assert len(found) == 1, "docker/spark/Dockerfile must write spark.redaction.regex into spark-defaults.conf once"
    return found[0]


class Env(dict):
    """The environment lake_session reads: a credential-shaped name not set reads as `cred:<name>`."""

    def __missing__(self, name):
        if CREDENTIAL.search(name):
            return f"cred:{name}"
        raise KeyError(name)


def session(monkeypatch, *args, env=None, **kw) -> dict[str, str]:
    """The conf lake_session builds: every .config(name, value), and the app name."""
    sc = import_lake_module("spark_conf")

    class Builder:
        def __init__(self):
            self.conf = {}

        def appName(self, name):
            self.conf["spark.app.name"] = name
            return self

        def config(self, name, value):
            assert name not in self.conf, f"{name} set twice"
            self.conf[name] = value
            return self

        def getOrCreate(self):
            return self.conf

    monkeypatch.setattr(sc, "SparkSession", types.SimpleNamespace(builder=Builder()))
    monkeypatch.setattr(sc, "os", types.SimpleNamespace(environ=Env(env or {})))
    return sc.lake_session(*args, **kw)


def session_properties(monkeypatch) -> dict[str, bool]:
    """Every property of a session with two catalogs -> whether its value is a credential."""
    conf = session(monkeypatch, "redaction", catalogs=["lake", "fx_build"])
    return {n: str(v).startswith("cred:") for n, v in conf.items()}


@pytest.fixture
def props(monkeypatch):
    return session_properties(monkeypatch)


def test_the_parse_sees_the_catalog_keys(props):
    assert len(props) > 10
    assert {n for n, secret in props.items() if secret} == {
        f"spark.sql.catalog.{c}.s3.{k}" for c in ("lake", "fx_build") for k in ("access-key-id", "secret-access-key")}


def test_every_property_that_carries_a_credential_is_redacted(props):
    rx = re.compile(image_regex())
    missed = [n for n, secret in props.items() if secret and not rx.search(n)]
    assert missed == []


def test_every_credential_shaped_name_is_redacted(props):
    """By name too, in every separator spelling, whatever feeds the value."""
    rx = re.compile(image_regex())
    shaped = re.compile(r"secret|password|passwd|token|credential|accesskey|apikey|privatekey")
    names = list(props) + [
        "fs.s3a.access.key", "fs.s3a.accessKey", "s3.access_key_id", "s3.secret.key", "s3.session-token",
        "client.credentials-provider", "spark.ssl.keyPassword"]
    missed = [n for n in names if shaped.search(re.sub(r"[-._]", "", n.lower())) and not rx.search(n)]
    assert missed == []


def test_the_regex_is_not_a_blanket(props):
    rx = re.compile(image_regex())
    kept = [n for n in props if not rx.search(n)]
    assert "spark.sql.catalog.lake.s3.endpoint" in kept and "spark.sql.catalog.lake.uri" in kept


def test_spark_default_misses_the_hyphenated_key_which_is_why_the_image_sets_one():
    assert not re.search(SPARK_3_5_9_DEFAULT, "spark.sql.catalog.lake.s3.access-key-id")


# --- which key, which catalogs (W1-3) --------------------------------------------------------------

KEYS = {"K2_SPARK_S3_ACCESS_KEY": "k2-spark-ak", "K2_SPARK_S3_SECRET_KEY": "spark-sk",
        "K2_MAINTENANCE_S3_ACCESS_KEY": "k2-maintenance-ak", "K2_MAINTENANCE_S3_SECRET_KEY": "maintenance-sk",
        "MINIO_ROOT_USER": "root-ak", "MINIO_ROOT_PASSWORD": "root-sk"}


def keys_of(conf: dict) -> dict[str, tuple[str, str]]:
    cats = {n.split(".")[3] for n in conf if n.startswith("spark.sql.catalog.") and n.count(".") > 3}
    return {c: (conf[f"spark.sql.catalog.{c}.s3.access-key-id"], conf[f"spark.sql.catalog.{c}.s3.secret-access-key"])
            for c in cats}


def test_the_default_session_is_the_lake_catalog_alone_on_sparks_key(monkeypatch):
    conf = session(monkeypatch, "job", env=KEYS)
    assert keys_of(conf) == {"lake": ("k2-spark-ak", "spark-sk")}
    assert conf["spark.sql.catalog.lake.warehouse"] == "k2" and conf["spark.sql.defaultCatalog"] == "lake"
    assert "root-ak" not in conf.values() and "root-sk" not in conf.values()


def test_a_named_key_signs_every_catalog_each_over_its_warehouse(monkeypatch):
    conf = session(monkeypatch, "expire", env=KEYS, key=("K2_MAINTENANCE_S3_ACCESS_KEY", "K2_MAINTENANCE_S3_SECRET_KEY"),
                   catalogs=["fx_build", "lake"])
    assert keys_of(conf) == {"fx_build": ("k2-maintenance-ak", "maintenance-sk"), "lake": ("k2-maintenance-ak", "maintenance-sk")}
    assert conf["spark.sql.catalog.fx_build.warehouse"] == "fx" and conf["spark.sql.catalog.lake.warehouse"] == "k2"
    assert conf["spark.sql.defaultCatalog"] == "lake"
    assert "spark-sk" not in conf.values()


def test_a_session_without_its_key_fails_and_never_falls_back_to_the_root_pair(monkeypatch):
    sc = import_lake_module("spark_conf")
    monkeypatch.setattr(sc, "os", types.SimpleNamespace(environ={"MINIO_ROOT_USER": "root-ak", "MINIO_ROOT_PASSWORD": "root-sk"}))
    with pytest.raises(KeyError, match="K2_SPARK_S3_ACCESS_KEY"):
        sc.lake_session("job")


@pytest.mark.parametrize("catalog", ["fx", "_build", "k2", "other_lake"])
def test_a_catalog_that_is_neither_lake_nor_a_build_catalog_is_refused(monkeypatch, catalog):
    with pytest.raises(ValueError, match="neither"):
        session(monkeypatch, "job", env=KEYS, catalogs=[catalog])
