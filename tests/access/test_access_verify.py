"""What access-verify and the catalog drift check decide, without a lake (step S20).

The verdicts (scripts/access_verify.py): a denial row passes on PERMISSION_DENIED alone, and an
error row on NOT_SUPPORTED alone, so a syntax error, a missing table or a refused login never
passes either. The drift check (scripts/verify/catalog_drift.py) against a catalog faked in
memory: clean when it equals the release, PENDING before a first publish, and each plant the
throwaway proof makes (tests/access/prove.sh) named. The live scope's choices need k2gen's model,
so that test runs in k2gen's own environment, as a subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import access_verify as av  # noqa: E402  (imports k2gen only inside its functions)

cd = av.catalog_drift

GENERATED = 'SELECT "id", "note" FROM "governance"."c__base"."t" FOR VERSION AS OF \'audited\''
STORED = 'SELECT\n  "id"\n, "note"\nFROM\n  "governance"."c__base"."t" FOR VERSION AS OF \'audited\'\n'


@pytest.mark.parametrize("error,passes", [
    ("PERMISSION_DENIED", True), ("SYNTAX_ERROR", False), ("TABLE_NOT_FOUND", False), ("SCHEMA_NOT_FOUND", False),
    ("COLUMN_NOT_FOUND", False), ("NOT_SUPPORTED", False), ("HTTP_401", False), (None, False)])
def test_a_denial_is_permission_denied_and_nothing_else(error, passes):
    assert av.denial(av.Answer(error=error, message="x"))[0] is passes


@pytest.mark.parametrize("error,passes", [
    ("NOT_SUPPORTED", True), ("PERMISSION_DENIED", False), ("SYNTAX_ERROR", False), ("TABLE_NOT_FOUND", False), (None, False)])
def test_an_error_row_is_not_supported_and_nothing_else(error, passes):
    assert av.unsupported(av.Answer(error=error, message="x"))[0] is passes


def test_no_bounds_fails_on_a_bound_a_row_count_an_error_or_nothing():
    clean = [["id", None, 0, 0, None, None, None], [None, None, None, None, None, None, None]]
    assert av.no_bounds(av.Answer(rows=clean))[0]
    assert not av.no_bounds(av.Answer(rows=[["id", None, 0, 0, None, "1", "5"]]))[0]
    assert not av.no_bounds(av.Answer(rows=[[None, None, None, None, 5.0, None, None]]))[0]
    assert not av.no_bounds(av.Answer(rows=[]))[0]
    assert not av.no_bounds(av.Answer(error="PERMISSION_DENIED"))[0]


def test_tokens_ignore_trinos_reformatting_and_see_every_change():
    assert cd.tokens(STORED) == cd.tokens(GENERATED)
    assert cd.tokens(GENERATED.replace('"id"', "id")) == cd.tokens(GENERATED)  # Trino folds both
    for changed in (GENERATED.replace(" FOR VERSION AS OF 'audited'", ""),  # wider: main
                    GENERATED.replace('"note"', '"note", "note" AS "note_2"'),  # an alias past a mask
                    GENERATED.replace("'audited'", "'AUDITED'")):  # a literal is exact
        assert cd.tokens(changed) != cd.tokens(GENERATED)


LOC = "s3://lake/governance/t-uuid"


def table(tag: int | None = 2, log=(1, 2), manifest=f"{LOC}/metadata/snap-2.avro", tag_type="tag") -> dict:
    refs = {"main": {"snapshot-id": 2, "type": "branch"}}
    if tag is not None:
        refs["audited"] = {"snapshot-id": tag, "type": tag_type}
    snaps = [{"snapshot-id": 1, "manifest-list": f"{LOC}/metadata/snap-1.avro", "schema-id": 0},
             {"snapshot-id": 2, "manifest-list": manifest, "schema-id": 0},
             {"snapshot-id": 9, "manifest-list": "s3://lake/governance/other-uuid/metadata/snap-9.avro", "schema-id": 0}]
    return {"location": LOC, "refs": refs, "snapshots": snaps, "snapshot-log": [{"snapshot-id": s} for s in log],
            "current-schema-id": 0, "schemas": [{"schema-id": 0, "fields": [{"name": "id"}, {"name": "note"}]}]}


def test_tag_problems():
    assert cd.tag_problems(table(), "audited") == []
    assert "no 'audited' tag" in cd.tag_problems(table(tag=None), "audited")[0]
    assert "not a tag" in cd.tag_problems(table(tag_type="branch"), "audited")[0]
    grafted = " ".join(cd.tag_problems(table(tag=9), "audited"))
    assert "never this table's main" in grafted and "another table's snapshot" in grafted


class FakeCatalog:
    """One warehouse in memory: {namespace: {"tables": {name: metadata}, "views": {name: metadata}}}."""

    state: dict = {}

    def __init__(self, uri, warehouse):
        self.w = self.state[warehouse]

    def namespaces(self):
        return list(self.w)

    def tables(self, ns):
        return list(self.w[ns]["tables"])

    def views(self, ns):
        return list(self.w[ns]["views"])

    def table(self, ns, t):
        return self.w.get(ns, {}).get("tables", {}).get(t)

    def view(self, ns, v):
        return self.w.get(ns, {}).get("views", {}).get(v)


def view_meta(sql=STORED, owner="pub_c", dialects=("trino",)) -> dict:
    return {"current-version-id": 1, "properties": {cd.OWNER_PROPERTY: owner} if owner else {},
            "versions": [{"version-id": 1, "representations": [{"type": "sql", "sql": sql, "dialect": d} for d in dialects]}]}


class View:
    relation, base, columns, owner = "governance.c.t", "governance.c__base.t", ("id", "note"), "pub_c"


WANT = {"namespaces": {"governance": {"c", "c__base"}}, "tables": {("governance", "c__base"): {"t", "seed"}},
        "views": {("governance", "c"): {"t": {"view": View, "base": ("c__base", "t")}}},
        "pending_tables": {("governance", "c__base"): {"t", "seed"}},
        "sql": lambda view, columns, tagged: GENERATED if list(columns) == ["id", "note"] else "other"}


def catalog(**change) -> dict:
    ns = {"c": {"tables": {}, "views": {"t": view_meta()}}, "c__base": {"tables": {"t": table(), "seed": table()}, "views": {}}}
    for path, value in change.items():
        where, kind, name = path.split("__")
        where = {"reader": "c", "base": "c__base"}[where]
        if value is None:
            ns[where][kind].pop(name)
        else:
            ns[where][kind][name] = value
    return {"governance": ns}


@pytest.fixture
def drift(monkeypatch):
    monkeypatch.setattr(cd, "Catalog", FakeCatalog)

    def run(state: dict) -> dict:
        FakeCatalog.state = state
        return cd.drift("http://fake/catalog", WANT)

    return run


def test_a_catalog_equal_to_the_release_is_clean(drift):
    found = drift(catalog())
    assert found["differences"] == [] and len(found["skipped"]) == 1 and "phase 002" in found["skipped"][0]


def test_a_product_before_its_first_publish_is_pending_not_drift(drift):
    found = drift(catalog(reader__views__t=None, base__tables__t=None, base__tables__seed=None))
    assert found["differences"] == [] and len(found["pending"]) == 3


@pytest.mark.parametrize("change,named", [
    ({"reader__views__foreign": view_meta(sql="SELECT * FROM governance.c__base.t")}, "governance.c.foreign: the release declares no such view"),
    ({"reader__tables__planted": table()}, "governance.c.planted: in a reader namespace"),
    ({"reader__views__t": view_meta(owner="pub_x")}, "trino.run-as-owner 'pub_x'"),
    ({"reader__views__t": view_meta(owner=None)}, "an invoker view"),
    ({"reader__views__t": view_meta(dialects=("trino", "spark"))}, "representations"),
    ({"reader__views__t": view_meta(sql=GENERATED.replace(" FOR VERSION AS OF 'audited'", ""))}, "governance.c.t: SQL"),
    ({"reader__views__t": view_meta(sql=GENERATED.replace('"note"', '"note", "note" AS "note_2"'))}, "governance.c.t: SQL"),
    ({"base__tables__t": table(tag=9)}, "never this table's main"),
    ({"base__tables__t": None, "base__views__t": view_meta(sql="SELECT 9999 AS id")}, "DRIFT base governance.c__base.t: a VIEW"),
    ({"base__views__extra": view_meta()}, "governance.c__base.extra: the release declares no such view"),
    ({"reader__views__t": None}, "missing; its product has published"),
])
def test_each_plant_is_named(drift, change, named):
    found = drift(catalog(**change))
    assert any(named in line for line in found["differences"]), found


def test_an_extra_namespace_is_named(drift):
    state = catalog()
    state["governance"]["planted"] = {"tables": {}, "views": {}}
    assert any("namespace governance.planted" in line for line in drift(state)["differences"])


LIVE = r"""
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import access_verify as av
from k2gen import model
good = Path("platform/k2gen/tests/fixtures/v2/good")
docs = {p.relative_to(good).as_posix(): p.read_bytes() for p in sorted(good.rglob("*")) if p.is_file()}
prov = model.Provenance("git", "v0.0.0-test", "0" * 40, "t <t@example.org>", "2026-11-27T00:00:00Z", model.set_sha256(docs),
                        tuple(model.DocumentProvenance(p, hashlib.sha256(d).hexdigest(), "0" * 40, None) for p, d in docs.items()))
m = model.load(model.DocumentSet(docs, prov))
p = next(p for p in m.products if any(g.principal.startswith("p.") for g in p.grants) and any(t.build.kind == "dbt" for t in p.tables))
t = next(t for t in p.tables if t.build.kind == "dbt")
person = next(g.principal for g in p.grants if g.principal.startswith("p."))
rows = {
    "a consumer of a product that is not the canary": model.DeniedExpectation(person, model.input_relation(t), ("sql",)),
    "a service row naming another product": model.StatementExpectation("svc.platform", f"DROP VIEW {model.view_build_relation(p.domain, p.name, t.name)}", "denied", {}),
    "a service row naming no product": model.StatementExpectation("svc.api", 'SET SESSION AUTHORIZATION "svc.platform"', "denied", {}),
    "the drift row": model.DriftExpectation("catalog", "clean"),
}
print(json.dumps({k: av.live_skip(e, m) for k, e in rows.items()}))
"""


def test_the_live_scope_runs_only_platform_held_passwords_on_the_canary():
    out = subprocess.run(["uv", "run", "--project", "platform/k2gen", "--locked", "-q", "python", "-c", LIVE],
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    skips = json.loads(out.stdout.strip().splitlines()[-1])
    assert "owner's, not the platform's" in skips["a consumer of a product that is not the canary"]
    assert "not the canary" in skips["a service row naming another product"]
    assert skips["a service row naming no product"] is None
    assert skips["the drift row"] is None


def test_the_lake_env_json_is_read_as_the_lake_writes_it(tmp_path):
    """A release-scope run finds each principal's password where the lake put it (the review of
    S13 and S20, H1: env.json once named one `passwordFile`; the lake now writes a map)."""
    secrets = tmp_path / "secrets"
    (secrets / "throwaway").mkdir(parents=True)
    (secrets / "principals").mkdir()
    files = {"p.canary-full": secrets / "throwaway/p.canary-full.password", "svc.api": secrets / "principals/svc.api.password"}
    for user, f in files.items():
        f.write_text(f"pw-of-{user}\n")
    (secrets / "principals/svc.extra.password").write_text("pw-of-svc.extra\n")  # in no map: the per-principal file
    env_file = tmp_path / "env.json"
    av.lake_env.write(env_file, release="/work/releases/r", trino="https://trino:8443", lakekeeper="http://lakekeeper:8181",
                      secrets=str(secrets), ca=str(tmp_path / "ca.pem"), passwords={u: str(f) for u, f in files.items()},
                      warehouses=["k2"])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=t",
                    "-days", "1", "-keyout", str(tmp_path / "ca.key"), "-out", str(tmp_path / "ca.pem")], capture_output=True, check=True)
    trino, env = av.connect(env_file)
    assert [trino.password(u) for u in ("p.canary-full", "svc.api", "svc.extra")] == \
        ["pw-of-p.canary-full", "pw-of-svc.api", "pw-of-svc.extra"]
    assert env["users"] == sorted(files) and env["release"] == "/work/releases/r"
    with pytest.raises(av.NoPassword, match="no password file for p.nobody"):
        trino.password("p.nobody")
    del env["passwords"]
    (tmp_path / "old.json").write_text(json.dumps(env | {"passwordFile": str(files["svc.api"])}))
    with pytest.raises(ValueError, match="lacks passwords"):
        av.connect(tmp_path / "old.json")


def test_the_lake_writes_env_json_only_through_lake_env():
    lake = (ROOT / "scripts/lib/throwaway-lake.sh").read_text()
    assert "lake_env.write(Path(\"/work/env.json\")" in lake and "env.json\").write_text" not in lake
    assert "passwordFile" not in (ROOT / "scripts/access_verify.py").read_text()
