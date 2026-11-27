"""emit/trino_etc.py (S11): Trino's etc directory, compiled from the good fixture set and from the
repository's own documents, each held to its golden files (the good set's under golden/trino_etc/,
the repository's under golden/trino_etc/repository/), and the settings the fences rest on asserted
one by one, so a golden regenerated carelessly still fails here.

K2GEN_UPDATE_GOLDEN=1 rewrites this emitter's golden files and nothing else; review the diff as code.
"""

import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from test_repository_documents import repository_set

from k2gen import model
from k2gen.emit import trino_etc
from k2gen.model import DocumentSet

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
GOLDEN = Path(__file__).parent / "golden" / "trino_etc"
ETC = "compiled/trino/etc/"
ENV_REF = re.compile(r"\$\{ENV:([A-Z][A-Z0-9_]*)\}")


def good_set() -> DocumentSet:
    documents = {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}
    provenance = model.Provenance("git", "v0.1.0", "0" * 40, "Example Owner <owner@example.org>",
                                  "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return DocumentSet(documents, provenance)


SETS: dict[str, tuple[Callable[[], DocumentSet], Path]] = {
    "good": (good_set, GOLDEN), "repository": (repository_set, GOLDEN / "repository")}


def compiled(name: str) -> dict[str, str]:
    m = model.load(SETS[name][0]())
    return trino_etc.emit(m)


def props(text: str) -> dict[str, str]:
    """A .properties file as these files write it: `key=value` lines and `#` comments, no key twice."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            key, sep, value = line.partition("=")
            assert sep and key not in out, line
            out[key] = value
    return out


@pytest.mark.parametrize("name", SETS)
def test_each_set_compiles_to_its_golden_files(name: str) -> None:
    files, root = compiled(name), SETS[name][1]
    if os.environ.get("K2GEN_UPDATE_GOLDEN"):
        shutil.rmtree(root / "compiled", ignore_errors=True)
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
    golden = {p.relative_to(root).as_posix(): p.read_text() for p in sorted((root / "compiled").rglob("*")) if p.is_file()}
    assert files == golden


@pytest.mark.parametrize("name", SETS)
def test_only_etc_and_none_of_the_files_two_other_steps_emit(name: str) -> None:
    files = compiled(name)
    assert all(p.startswith(ETC) for p in files)
    # rules.json and groups.txt are S12's, beside etc/; the authenticator file in etc/ is S13's.
    assert f"{ETC}password-authenticator.properties" not in files
    assert not [p for p in files if p.endswith(("rules.json", "groups.txt", "password.db"))]
    assert all(model.need_of(p) == "trinoRestart" for p in files)


def test_https_only_with_password_login() -> None:
    config = props(compiled("good")[f"{ETC}config.properties"])
    assert config["http-server.http.enabled"] == "false"
    assert config["http-server.https.enabled"] == "true" and config["http-server.https.port"] == "8443"
    assert config["http-server.https.keystore.path"] == "/run/k2-secrets/tls/trino.pem"
    assert config["http-server.https.keystore.path"] in {f.mount for f in model.SECRET_FILES if f.path == "tls/trino.pem"}
    assert config["http-server.authentication.type"] == "PASSWORD"
    assert config["internal-communication.shared-secret"] == "${ENV:K2_TRINO_SHARED_SECRET}"
    assert config["internal-communication.https.required"] == "true"
    assert config["discovery.uri"] == "https://127.0.0.1:8443"
    assert not [k for k in config if k.startswith("http-server.http.") and k != "http-server.http.enabled"]
    # No Web UI: its session outlives a password's removal (ADR-060's Outcome, identity row I19).
    assert config["web-ui.enabled"] == "false" and not [k for k in config if k.startswith("web-ui.") and k != "web-ui.enabled"]


@pytest.mark.parametrize("name", SETS)
def test_every_credential_is_an_environment_name_never_a_value(name: str) -> None:
    used: set[str] = set()
    for path, text in compiled(name).items():
        if not path.endswith(".properties"):
            continue
        for key, value in props(text).items():
            if re.search(r"secret|password|access-key", key) and not key.endswith((".path", "-file")):
                assert ENV_REF.fullmatch(value), f"{path}: {key} holds a value, not ${{ENV:...}}"
            assert "${" not in ENV_REF.sub("", value), f"{path}: {key}: only ${{ENV:...}} is substituted"
            used |= set(ENV_REF.findall(value))
    assert used == {"K2_TRINO_SHARED_SECRET", "K2_TRINO_S3_ACCESS_KEY", "K2_TRINO_S3_SECRET_KEY"}


@pytest.mark.parametrize("name", SETS)
def test_lake_read_only_and_per_domain_a_reader_without_statistics_and_a_writer(name: str) -> None:
    files = compiled(name)
    m = model.load(SETS[name][0]())
    catalogs = {p.removeprefix(f"{ETC}catalog/").removesuffix(".properties"): props(t)
                for p, t in files.items() if p.startswith(f"{ETC}catalog/")}
    readers = {model.reader_catalog(d.name): d for d in m.domains}
    writers = {model.build_catalog(d.name): d for d in m.domains}
    assert set(catalogs) == {"lake"} | readers.keys() | writers.keys() and readers
    for name_, c in catalogs.items():
        assert c["connector.name"] == "iceberg" and c["iceberg.catalog.type"] == "rest"
        assert c["iceberg.rest-catalog.uri"] == "http://lakekeeper:8181/catalog"
        assert c["s3.endpoint"] == "http://minio:9000" and c["s3.path-style-access"] == "true"
        assert c["iceberg.rest-catalog.vended-credentials-enabled"] == "false"
        assert (c["iceberg.rest-catalog.warehouse"] == "k2") == (name_ == "lake")  # nothing else on the lake's warehouse
    assert catalogs["lake"]["iceberg.security"] == "read_only"
    for name_, d in readers.items():
        c = catalogs[name_]
        assert c["iceberg.rest-catalog.warehouse"] == model.warehouse(d.name)
        assert c["iceberg.security"] == "read_only" and c["iceberg.table-statistics-enabled"] == "false"
    for name_, d in writers.items():
        c = catalogs[name_]
        assert c["iceberg.rest-catalog.warehouse"] == model.warehouse(d.name)
        assert "iceberg.security" not in c and "iceberg.table-statistics-enabled" not in c
        assert c["iceberg.register-table-procedure.enabled"] == c["iceberg.add-files-procedure.enabled"] == "false"


def test_the_good_sets_domains_name_their_catalog_files() -> None:
    assert {p for p in compiled("good") if "/catalog/" in p} == {
        f"{ETC}catalog/{c}.properties" for c in ("lake", "sensor_net", "sensor_net_build", "ops_insight", "ops_insight_build")}


def test_rules_and_groups_are_read_through_the_pointer_every_five_seconds() -> None:
    files = compiled("good")
    access, groups = props(files[f"{ETC}access-control.properties"]), props(files[f"{ETC}group-provider.properties"])
    assert access == {"access-control.name": "file", "security.refresh-period": "5s",
                      "security.config-file": "/k2/releases/pointers/trino/compiled/trino/rules.json"}
    assert groups == {"group-provider.name": "file", "file.refresh-period": "5s",
                      "file.group-file": "/k2/releases/pointers/trino/compiled/trino/groups.txt"}
    assert access["security.config-file"].startswith(model.RELEASES_MOUNT + "/pointers/trino/")


@pytest.mark.parametrize(("user", "group"), [
    ("dbt_station_readings", "pipeline"), ("pub_station_readings", "pipeline"), ("svc.runs", "pipeline"),
    ("svc.platform", "pipeline"), ("svc.catalog", "pipeline"),
    ("svc.api", "interactive"), ("p.canary-full", "interactive"), ("svc.runsx", "interactive"),
    ("svcxruns", "interactive"), ("xdbt_a", "interactive"), ("k2_release_0123456789ab", "interactive")])
def test_resource_groups_put_pipeline_principals_in_pipeline_and_everyone_else_in_interactive(user: str, group: str) -> None:
    files = compiled("good")
    assert props(files[f"{ETC}resource-groups.properties"]) == {
        "resource-groups.configuration-manager": "file", "resource-groups.config-file": "etc/resource-groups.json"}
    config = json.loads(files[f"{ETC}resource-groups.json"])
    assert {g["name"] for g in config["rootGroups"]} == {"pipeline", "interactive"}
    # Trino's selectors: the first whose `user` regex matches the whole user name wins.
    chosen = next(s["group"] for s in config["selectors"] if "user" not in s or re.fullmatch(s["user"], user))
    assert chosen == group
