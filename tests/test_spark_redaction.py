"""The Spark image's redaction regex against every property docker/lake/spark_conf.py
sets. The names are parsed from the file, so a credential added under a new name
fails here before a job serves it in clear on the job UI, the History Server or
an event log. Spark blanks a value when the regex finds a match anywhere in the
property's name (Scala findFirstIn, which is re.search)."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPARK_3_5_9_DEFAULT = r"(?i)secret|password|token|access[.]?key"   # read from the image's JVM, 2026-11-26


def image_regex() -> str:
    found = re.findall(r"'spark\.redaction\.regex\s+(\S+)'", (ROOT / "docker/spark/Dockerfile").read_text())
    assert len(found) == 1, "docker/spark/Dockerfile must write spark.redaction.regex into spark-defaults.conf once"
    return found[0]


def session_properties() -> dict[str, bool]:
    """Every `.config(name, value)` in spark_conf.py -> whether its value is read from
    a credential-like environment variable (USER, PASSWORD, KEY, SECRET, TOKEN, CREDENTIAL)."""
    tree = ast.parse((ROOT / "docker/lake/spark_conf.py").read_text())
    env = {}   # local name -> the environment variable it was read from
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            v = node.value
            key = v.slice if isinstance(v, ast.Subscript) else v.args[0] if isinstance(v, ast.Call) and v.args else None
            if "environ" in ast.unparse(v) and isinstance(key, ast.Constant):
                env[node.targets[0].id] = key.value
    credential = re.compile(r"USER|PASSWORD|KEY|SECRET|TOKEN|CREDENTIAL")
    props = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "config":
            name, value = node.args
            if isinstance(name, ast.JoinedStr):
                name = "".join(p.value if isinstance(p, ast.Constant) else {"CATALOG": "lake"}[p.value.id]
                               for p in name.values)
            else:
                name = name.value
            props[name] = isinstance(value, ast.Name) and bool(credential.search(env.get(value.id, "")))
    return props


def test_the_parse_sees_the_catalog_keys():
    props = session_properties()
    assert len(props) > 10
    assert {n for n, secret in props.items() if secret} == {
        "spark.sql.catalog.lake.s3.access-key-id", "spark.sql.catalog.lake.s3.secret-access-key"}


def test_every_property_that_carries_a_credential_is_redacted():
    rx = re.compile(image_regex())
    missed = [n for n, secret in session_properties().items() if secret and not rx.search(n)]
    assert missed == []


def test_every_credential_shaped_name_is_redacted():
    """By name too, in every separator spelling, whatever feeds the value."""
    rx = re.compile(image_regex())
    shaped = re.compile(r"secret|password|passwd|token|credential|accesskey|apikey|privatekey")
    names = list(session_properties()) + [
        "fs.s3a.access.key", "fs.s3a.accessKey", "s3.access_key_id", "s3.secret.key", "s3.session-token",
        "client.credentials-provider", "spark.ssl.keyPassword"]
    missed = [n for n in names if shaped.search(re.sub(r"[-._]", "", n.lower())) and not rx.search(n)]
    assert missed == []


def test_the_regex_is_not_a_blanket():
    rx = re.compile(image_regex())
    kept = [n for n in session_properties() if not rx.search(n)]
    assert "spark.sql.catalog.lake.s3.endpoint" in kept and "spark.sql.catalog.lake.uri" in kept


def test_spark_default_misses_the_hyphenated_key_which_is_why_the_image_sets_one():
    assert not re.search(SPARK_3_5_9_DEFAULT, "spark.sql.catalog.lake.s3.access-key-id")
