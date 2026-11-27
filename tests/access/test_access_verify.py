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


def test_an_ok_row_is_a_statement_that_completes():
    assert av.succeeded(av.Answer(rows=[["1"]]))[0]
    assert not av.succeeded(av.Answer(error="PERMISSION_DENIED", message="x"))[0]


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
        "views": {("governance", "c"): {"t": {"view": View, "base": ("c__base", "t"), "record": "governance_build.c__base.t"}}},
        "pending_tables": {("governance", "c__base"): {"t", "seed"}}, "results": {("governance", "c__base")},
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

    monkeypatch.delenv(cd.LEFT_ENV, raising=False)

    def run(state: dict, newest_audited=None, want=WANT, only=None, left=None) -> dict:
        FakeCatalog.state = state
        return cd.drift("http://fake/catalog", want, newest_audited, only, left)

    return run


def test_a_catalog_equal_to_the_release_is_clean(drift):
    found = drift(catalog(), newest_audited={"governance_build.c__base.t": 2}.get)
    assert found == {"differences": [], "pending": [], "skipped": [], "left": []}


def test_without_a_run_records_reader_each_tag_is_a_skip_never_a_pass(drift):
    found = drift(catalog())
    assert found["differences"] == [] and len(found["skipped"]) == 1 and "no run-records reader" in found["skipped"][0]


@pytest.mark.parametrize("recorded", [1, 3, None])
def test_a_tag_no_recorded_publish_moved_is_named(drift, recorded):
    asked = []
    found = drift(catalog(), newest_audited=lambda t: asked.append(t) or recorded)
    assert asked == ["governance_build.c__base.t"]
    assert any("DRIFT tag governance.c__base.t" in line and f"says {recorded}" in line for line in found["differences"]), found


def test_the_builds_results_table_is_expected_in_its_base_namespace_only(drift):
    assert drift(catalog(base__tables__dq_test_results=table(tag=None)))["differences"] == []
    named = drift(catalog(reader__tables__dq_test_results=table(tag=None)))["differences"]
    assert any("governance.c.dq_test_results: in a reader namespace" in line for line in named)
    planted = drift(catalog(base__tables__planted=table(tag=None)))["differences"]
    assert any("governance.c__base.planted" in line for line in planted)
    as_view = drift(catalog(base__views__dq_test_results=view_meta()))["differences"]
    assert any("governance.c__base.dq_test_results: the release declares no such view" in line for line in as_view)


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


class ViewD:
    relation, base, columns, owner = "governance.d.t", "governance.d__base.t", ("id", "note"), "pub_d"


WANT2 = {**WANT, "namespaces": {"governance": {"c", "c__base", "d", "d__base"}},
         "tables": {**WANT["tables"], ("governance", "d__base"): {"t"}},
         "views": {**WANT["views"], ("governance", "d"): {"t": {"view": ViewD, "base": ("d__base", "t"),
                                                                 "record": "governance_build.d__base.t"}}},
         "pending_tables": {**WANT["pending_tables"], ("governance", "d__base"): {"t"}},
         "results": {("governance", "c__base"), ("governance", "d__base")}}
RECORDS = {"governance_build.c__base.t": 2, "governance_build.d__base.t": 1}.get
MINE = {("governance", "c"), ("governance", "c__base")}


def d_mid_run(**change) -> dict:
    """Product c at rest; product d inside its run: dbt's transient table beside its base, its tag
    committed before its audited row (records say 1), its view still narrowed."""
    state = catalog(**change)
    state["governance"]["d"] = {"tables": {}, "views": {"t": view_meta(sql='SELECT "id" FROM "governance"."d__base"."t"')}}
    state["governance"]["d__base"] = {"tables": {"t": table(), "t__dbt_tmp": table(tag=None)}, "views": {}}
    return state


def test_the_wrappers_drift_check_is_its_own_products_alone(drift):
    assert drift(d_mid_run(), RECORDS, WANT2, MINE) == {"differences": [], "pending": [], "skipped": [], "left": []}


def test_release_verify_still_checks_every_product(drift):
    named = " | ".join(drift(d_mid_run(), RECORDS, WANT2)["differences"])
    assert "DRIFT tag governance.d__base.t" in named and "governance.d__base.t__dbt_tmp" in named
    assert "DRIFT view governance.d.t: SQL" in named


def test_the_wrappers_scope_still_names_its_own_drift(drift):
    planted = d_mid_run(base__tables__planted=table(tag=None), reader__views__t=view_meta(owner="pub_x"))
    named = " | ".join(drift(planted, lambda t: 1, WANT2, MINE)["differences"])
    assert "governance.c__base.planted" in named and "pub_x" in named and "DRIFT tag governance.c__base.t" in named
    assert "d__base" not in named and "governance.d." not in named


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
legs = {
    "a person's statement": model.StatementExpectation(person, "SELECT 1", "denied", {}),
    "a service statement": model.StatementExpectation("svc.api", "SELECT 1", "denied", {}),
    "a probe": model.ProbeExpectation("k2-net", "lakekeeper:8181", "refused"),
    "the drift row": model.DriftExpectation("catalog", "clean"),
    "a storage-key row": model.StorageKeyExpectation("tools", "k2-landing", "read", "allowed"),
    "a service's REST row": model.DeniedExpectation("svc.api", "system.runtime.queries", ("sql", "rest")),
}
print(json.dumps({"skips": {k: av.live_skip(e, m) for k, e in rows.items()},
                  "legs": {k: av.legs(e, m, "live", {"trino": "https://t:8443", "apiKeys": "/nonexistent"}) for k, e in legs.items()}}))
"""


def test_the_live_scope_runs_only_platform_held_passwords_on_the_canary():
    out = subprocess.run(["uv", "run", "--project", "platform/k2gen", "--locked", "-q", "python", "-c", LIVE],
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    skips, legs = got["skips"], got["legs"]
    assert "owner's, not the platform's" in skips["a consumer of a product that is not the canary"]
    assert "not the canary" in skips["a service row naming another product"]
    assert skips["a service row naming no product"] is None
    assert skips["the drift row"] is None
    # legs(): the one rule run() skips by and tests/access/prove.py live expects by. A person's
    # statement row has no table, and is skipped all the same (the blocker of W2-9's three rows).
    assert "owner's, not the platform's" in legs["a person's statement"][0][1]
    assert legs["a service statement"] == [["row", None]] and legs["a storage-key row"] == [["row", None]]
    assert legs["a probe"] == [["row", av.SKIP_PROBE]] and legs["the drift row"] == [["row", av.SKIP_DRIFT]]
    # A REST leg with no key file on live is a skip of that leg alone, naming the command; its SQL leg runs.
    (sql, _), (rest, why) = legs["a service's REST row"]
    assert (sql, rest) == ("sql", "rest") and legs["a service's REST row"][0][1] is None and "make api-key PRINCIPAL=svc.api" in why


def test_the_lake_env_json_is_read_as_the_lake_writes_it(tmp_path, monkeypatch):
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
    monkeypatch.delenv("K2_API_KEY_DIR", raising=False)
    # Each REST key where `make api-key TO_FILE=1` writes it, on a lake and live alike: api-keys/, never
    # api/, the directory k2-api mounts for keys.json.
    assert av.connect(env_file)[1]["apiKeys"] == f"{secrets}/api-keys"
    monkeypatch.setenv("K2_SECRETS", str(secrets))
    monkeypatch.setenv("K2_TRINO_CA", str(tmp_path / "ca.pem"))
    assert av.connect(None)[1]["apiKeys"] == str(secrets / "api-keys")
    monkeypatch.setenv("K2_API_KEY_DIR", str(tmp_path / "elsewhere"))
    assert av.connect(None)[1]["apiKeys"] == str(tmp_path / "elsewhere")
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


# ---- storage-key rows (phase 002, step W0-3) -----------------------------------------------------

STORAGE_ROWS = r"""
import json, sys
from k2gen import model
rows = {
    "refused": {"storageKey": {"engine": "tools", "target": "k2-lake/warehouse/governance", "operation": "write"}, "expect": "refused"},
    "allowed": {"storageKey": {"engine": "trino", "target": "k2-lake/warehouse/governance", "operation": "read"}, "expect": "allowed"},
    "bucket alone": {"storageKey": {"engine": "backup", "target": "k2-backup", "operation": "write"}, "expect": "allowed"},
    "unknown engine": {"storageKey": {"engine": "root", "target": "k2-lake", "operation": "read"}, "expect": "allowed"},
    "no operation": {"storageKey": {"engine": "spark", "target": "k2-lake"}, "expect": "allowed"},
    "delete": {"storageKey": {"engine": "spark", "target": "k2-lake", "operation": "delete"}, "expect": "allowed"},
    "s3 url": {"storageKey": {"engine": "spark", "target": "s3://k2-lake/warehouse", "operation": "read"}, "expect": "allowed"},
    "dot dot": {"storageKey": {"engine": "spark", "target": "k2-lake/../x", "operation": "read"}, "expect": "allowed"},
    "trailing slash": {"storageKey": {"engine": "spark", "target": "k2-lake/warehouse/", "operation": "read"}, "expect": "allowed"},
    "open": {"storageKey": {"engine": "spark", "target": "k2-lake", "operation": "read"}, "expect": "open"},
    "extra field": {"storageKey": {"engine": "spark", "target": "k2-lake", "operation": "read", "key": "x"}, "expect": "allowed"},
    "with a principal": {"storageKey": {"engine": "spark", "target": "k2-lake", "operation": "read"}, "expect": "allowed", "principal": "svc.api"},
    "probe": {"probe": {"from": "k2-net", "target": "lakekeeper:8181"}, "expect": "refused"},
    "probe expecting allowed": {"probe": {"from": "k2-net", "target": "lakekeeper:8181"}, "expect": "allowed"},
    "drift": {"drift": "catalog", "expect": "clean"},
    "denied false": {"principal": "svc.api", "table": "governance.c.t", "denied": False},
    "statement": {"principal": "svc.api", "sql": "SELECT 1", "expect": "ok"},
}
errors = {k: model.schema_errors("expectations", {"expectations": [r]}) for k, r in rows.items()}
parsed = {k: repr(model._expectation(rows[k])) for k in ("refused", "allowed", "probe")}
print(json.dumps({"errors": errors, "parsed": parsed}))
"""


def test_the_schema_takes_a_storage_key_row_and_the_five_shapes_as_before():
    out = subprocess.run(["uv", "run", "--project", "platform/k2gen", "--locked", "-q", "python", "-c", STORAGE_ROWS],
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    accepted = {k for k, e in got["errors"].items() if not e}
    assert accepted == {"refused", "allowed", "bucket alone", "probe", "drift", "statement"}, got["errors"]
    assert got["parsed"]["refused"] == \
        "StorageKeyExpectation(engine='tools', target='k2-lake/warehouse/governance', operation='write', expect='refused')"
    assert got["parsed"]["allowed"].startswith("StorageKeyExpectation(engine='trino'")
    assert got["parsed"]["probe"] == "ProbeExpectation(origin='k2-net', target='lakekeeper:8181', expect='refused')"


def test_the_signature_is_aws_signature_v4():
    """AWS's own worked example (S3 API reference, Signature V4, "GET Bucket (List Objects)")."""
    auth = av.sigv4("GET", "examplebucket.s3.amazonaws.com", "/", "max-keys=2&prefix=J", "20130524T000000Z",
                    ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"))
    assert auth["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


class FakeS3:
    """MinIO's answers, in memory, by the access key a request is signed with: a policy per key
    (rw, ro, wo: write but no delete, go: GetObject alone, no list, none) and, as MinIO does, the policy checked before the
    bucket exists, so a refused key gets AccessDenied on a bucket that is not there; `ak-unknown` is a
    key MinIO does not know."""

    POLICY = {"ak-rw": "rw", "ak-ro": "ro", "ak-wo": "wo", "ak-none": "none", "ak-go": "go", "root-ak": "rw"}

    def __init__(self):
        import http.server
        import threading
        fake = self
        self.buckets = {"k2-lake": {"warehouse/governance/t-uuid/metadata/v1.json": b"{}"}, "k2-backup": {}}
        self.seen: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, status, code=None, body=b""):
                if code:
                    body = f"<Error><Code>{code}</Code><Message>m</Message></Error>".encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle_one(self):
                fake.seen.append(self.headers.get("Authorization", ""))
                ak = self.headers.get("Authorization", "").partition("Credential=")[2].partition("/")[0]
                path, _, query = self.path.partition("?")
                bucket, _, key = path.lstrip("/").partition("/")
                policy = fake.POLICY.get(ak)
                if policy is None:
                    return self.answer(403, "InvalidAccessKeyId")
                write = self.command in ("PUT", "DELETE")
                if policy == "none" or (write and policy in ("ro", "go")) or (self.command == "DELETE" and policy == "wo") \
                        or (self.command == "GET" and policy == "wo") or (policy == "go" and (not key or query)):
                    return self.answer(403, "AccessDenied")
                if bucket not in fake.buckets:
                    return self.answer(404, "NoSuchBucket")
                objects = fake.buckets[bucket]
                if self.command == "PUT":
                    objects[urllib.parse.unquote(key)] = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                    return self.answer(200)
                if self.command == "DELETE":
                    objects.pop(urllib.parse.unquote(key), None)
                    return self.answer(204)
                if query.startswith("location"):
                    return self.answer(200, body=b"<LocationConstraint/>")
                if "list-type=2" in query:
                    prefix = urllib.parse.parse_qs(query).get("prefix", [""])[0]
                    keys = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in sorted(objects) if k.startswith(prefix))[:1000]
                    return self.answer(200, body=f"<ListBucketResult>{keys}</ListBucketResult>".encode())
                if urllib.parse.unquote(key) in objects:
                    return self.answer(206, body=objects[urllib.parse.unquote(key)][:1])
                return self.answer(404, "NoSuchKey")

            do_GET = do_PUT = do_DELETE = handle_one

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"


import urllib.parse  # noqa: E402  (FakeS3's handler)
from types import SimpleNamespace  # noqa: E402

# Every name and secret holds a non-hex letter (k, s, n, u), so none can turn up inside a probe
# object's hex name: "bad", the unknown key's first name, was all hex and sometimes did.
SECRETS = {"ak-rw": "sk-rw-0123456789", "ak-ro": "sk-ro-0123456789", "ak-wo": "sk-wo-0123456789",
           "ak-none": "sk-none-0123456789", "ak-unknown": "sk-unknown-0123456789", "root-ak": "sk-root-0123456789",
           "ak-go": "sk-go-0123456789"}
PROBE_UUID = "6b3c0e3a-5f1e-4c2d-9a7b-1e2f3a4b5c6d"  # the write probe's name, fixed: no run differs from another


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setattr(av.uuid, "uuid4", lambda: av.uuid.UUID(PROBE_UUID))
    fake = FakeS3()
    yield fake
    fake.server.shutdown()


def keys(fake, endpoint=None, **engines) -> dict[str, str]:
    env = {"K2_S3_ENDPOINT": endpoint or fake.endpoint}
    for engine, ak in engines.items():
        env |= {f"K2_{engine.upper()}_S3_ACCESS_KEY": ak, f"K2_{engine.upper()}_S3_SECRET_KEY": SECRETS[ak]}
    return env


def row(engine, target, operation, expect):
    return SimpleNamespace(engine=engine, target=target, operation=operation, expect=expect)


@pytest.mark.parametrize("engines,r,passes,says", [
    ({"trino": "ak-ro"}, row("trino", "k2-lake/warehouse/governance", "read", "allowed"), True, "1 object listed and read"),
    ({"trino": "ak-ro"}, row("trino", "k2-lake/warehouse/fx", "read", "allowed"), True, "0 objects listed"),
    ({"tools": "ak-none", "trino": "ak-ro"}, row("tools", "k2-lake/warehouse/governance", "read", "refused"), True, "AccessDenied"),
    ({"tools": "ak-ro"}, row("tools", "k2-lake/warehouse/governance", "write", "refused"), True, "AccessDenied"),
    ({"spark": "ak-rw"}, row("spark", "k2-lake/warehouse/governance", "write", "allowed"), True, "deleted"),
    ({"spark": "ak-rw"}, row("spark", "k2-lake/warehouse/governance", "write", "refused"), False, "expected refused"),
    ({"tools": "ak-ro"}, row("tools", "k2-lake/warehouse/governance", "read", "refused"), False, "expected refused"),
    ({"spark": "ak-none"}, row("spark", "k2-lake/warehouse/governance", "read", "allowed"), False, "AccessDenied"),
    # a refusal counts only when some key shows the bucket is there
    ({"tools": "ak-none", "trino": "ak-ro"}, row("tools", "k2-typo/warehouse", "read", "refused"), False, "NoSuchBucket"),
    ({"tools": "ak-none"}, row("tools", "k2-lake/warehouse/governance", "read", "refused"), False, "no key here shows"),
    # a key MinIO does not know is a 403 too, but not AccessDenied
    ({"tools": "ak-unknown"}, row("tools", "k2-lake/warehouse/governance", "read", "refused"), False, "InvalidAccessKeyId"),
    ({"spark": "ak-rw"}, row("spark", "k2-typo", "write", "allowed"), False, "NoSuchBucket"),
    # F11: a refused list is not a refused read: the key reads an object another key lists, or a
    # probe another key puts there for it; a key with GetObject and no ListBucket fails.
    ({"tools": "ak-none", "trino": "ak-ro"}, row("tools", "k2-lake/warehouse/governance", "read", "refused"), True, "listed by the trino key"),
    ({"tools": "ak-go", "trino": "ak-ro"}, row("tools", "k2-lake/warehouse/governance", "read", "refused"), False, "HTTP 206"),
    ({"tools": "ak-none", "spark": "ak-rw"}, row("tools", "k2-lake/warehouse/fx", "read", "refused"), True, "put by the spark key"),
    ({"tools": "ak-go", "spark": "ak-rw"}, row("tools", "k2-lake/warehouse/fx", "read", "refused"), False, "put by the spark key"),
    ({"tools": "ak-none", "trino": "ak-ro"}, row("tools", "k2-lake/warehouse/fx", "read", "refused"), False, "proves nothing"),
])
def test_a_storage_key_row_passes_on_its_outcome_alone(s3, engines, r, passes, says, capsys):
    passed, why = av.storage_key(r, keys(s3, **engines))
    assert passed is passes and says in why, why
    assert s3.buckets["k2-lake"].keys() == {"warehouse/governance/t-uuid/metadata/v1.json"}  # no probe object left
    shown = why + "".join(capsys.readouterr())
    assert not any(v in shown for v in [*SECRETS.values(), *SECRETS])  # neither half of any pair, ever


def test_a_write_probe_is_removed_by_maintenance_then_the_root_when_its_own_key_cannot_delete(s3):
    r = row("backup", "k2-backup", "write", "allowed")
    probe = f"k2-access-probe/{av.uuid.UUID(PROBE_UUID).hex}"
    root = {"MINIO_ROOT_USER": "root-ak", "MINIO_ROOT_PASSWORD": SECRETS["root-ak"]}
    # Nothing can delete it: the row fails naming what it left.
    passed, why = av.storage_key(r, keys(s3, backup="ak-wo", maintenance="ak-ro"))
    assert not passed and f"left s3://k2-backup/{probe} " in why and "neither the maintenance pair nor a root pair" in why
    assert list(s3.buckets["k2-backup"]) == [probe]
    s3.buckets["k2-backup"].clear()
    # The maintenance key first, with no root pair at all: a live run leaves nothing behind.
    passed, why = av.storage_key(r, keys(s3, backup="ak-wo", maintenance="ak-rw"))
    assert passed and "removed with the maintenance key" in why and s3.buckets["k2-backup"] == {}
    # Maintenance first even when a root pair is set; the root only when maintenance cannot.
    passed, why = av.storage_key(r, keys(s3, backup="ak-wo", maintenance="ak-rw") | root)
    assert passed and "removed with the maintenance key" in why and s3.buckets["k2-backup"] == {}
    passed, why = av.storage_key(r, keys(s3, backup="ak-wo", maintenance="ak-ro") | root)
    assert passed and "removed with the root key" in why and s3.buckets["k2-backup"] == {}
    passed, why = av.storage_key(r, keys(s3, backup="ak-wo") | root)
    assert passed and "removed with the root key" in why and s3.buckets["k2-backup"] == {}
    assert not any(v in why for v in [*SECRETS.values(), *SECRETS])


def test_an_unreachable_storage_fails_a_refused_row(s3):
    import socket
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    passed, why = av.storage_key(row("tools", "k2-lake/warehouse/governance", "read", "refused"), keys(s3, closed, tools="ak-none"))
    assert not passed and "cannot reach the storage" in why and SECRETS["ak-none"] not in why


def test_a_missing_key_fails_its_row_naming_the_variable(s3):
    env = keys(s3, trino="ak-ro")
    passed, why = av.storage_key(row("maintenance", "k2-lake/warehouse/governance", "read", "refused"), env)
    assert not passed and "K2_MAINTENANCE_S3_ACCESS_KEY" in why and "K2_MAINTENANCE_S3_SECRET_KEY" in why
    del env["K2_TRINO_S3_SECRET_KEY"]
    passed, why = av.storage_key(row("trino", "k2-lake/warehouse/governance", "read", "allowed"), env)
    assert not passed and "K2_TRINO_S3_SECRET_KEY" in why and "K2_TRINO_S3_ACCESS_KEY" not in why
    assert s3.seen == []  # nothing was sent without a full pair


# ---- stage's placeholders (phase 002, W2-9) ------------------------------------------------------

def read(**asserts) -> SimpleNamespace:
    return SimpleNamespace(principal="p.a", table="d.f.t", via=("sql",), visible=("id",), masked=(), min_row_age_minutes=None,
                           must_not_appear=None, must_appear=None) if not asserts else \
        SimpleNamespace(**{**vars(read()), **asserts})


@pytest.mark.parametrize("asserts,rows", [
    ({}, False), ({"masked": ("note",)}, True), ({"min_row_age_minutes": 0}, True), ({"must_not_appear": "id > 1"}, True),
    ({"must_appear": SimpleNamespace(where=None, min_rows=1)}, True)])
def test_a_read_row_asserts_rows_unless_it_names_columns_alone(asserts, rows):
    """A columns-only read row runs on a placeholder of its contract's shape; any row assertion needs the model's rows."""
    assert av.asserts_rows(read(**asserts)) is rows


# ---- REST rows through k2-api (phase 003, W3-7) --------------------------------------------------

KEY = "k2_secret-key-of-p-a-0123456789"  # holds non-hex letters: it can match no hex in a line


class FakeApi:
    """k2-api's /v1/data answers, in memory: each GET answers `self.answer` (status, body), recording
    the query and the Authorization header it came with."""

    def __init__(self):
        import http.server
        import threading
        fake = self
        self.answer: tuple[int, dict] = (200, {"release": "r", "snapshot": "1", "rows": [], "next": None})
        self.seen: list[tuple[str, dict, str]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path, _, query = self.path.partition("?")
                fake.seen.append((path, dict(urllib.parse.parse_qsl(query)), self.headers.get("Authorization", "")))
                status, body = fake.answer
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture
def api():
    fake = FakeApi()
    yield fake
    fake.server.shutdown()


class FakeTrino:
    """The SQL leg: every statement answers `answer`; records (user, sql, zone)."""

    def __init__(self, answer):
        self.answer, self.seen = answer, []

    def run(self, user, sql, session=None, as_user=None, zone=None):
        self.seen.append((user, sql, zone))
        return self.answer


GOLD = [("rate_date", "date"), ("currency", "varchar(3)"), ("rate", "decimal(20,10)"), ("is_carried", "boolean"),
        ("carried_days", "integer"), ("rate_available_at", "timestamp(6) with time zone"), ("n", "bigint")]
NAMES = [c for c, _ in GOLD]
SQL_ROW = ["2026-11-27", "USD", "1.0512000000", False, 0, "2026-11-27 15:00:00.000000 UTC", 9007199254740993]
# The same row as k2-api writes it: a decimal its exact string, a time ISO 8601 in UTC, a bigint a number.
REST_ROW = {"rate_date": "2026-11-27", "currency": "USD", "rate": "1.0512000000", "is_carried": False, "carried_days": 0,
            "rate_available_at": "2026-11-27T15:00:00+00:00", "n": 9007199254740993}
PATH = "/v1/data/fx-reference/ecb-fx-rates/gold_eur_rates_daily"


def leg(api, trino, r, pk=("rate_date", "currency")):
    passed, why = av.rest_read(trino, r, api.url, PATH, list(pk), KEY) if hasattr(r, "visible") else \
        av.rest_denied(api.url, PATH, KEY)
    assert KEY not in why and "Bearer" not in why
    return passed, why


def page(*rows):
    return 200, {"release": "r", "snapshot": "1", "rows": list(rows), "next": None}


def test_a_columns_only_rest_read_asks_one_row_by_key_and_matches_the_sql_row(api):
    api.answer = page(REST_ROW)
    trino = FakeTrino(av.Answer(rows=[SQL_ROW], columns=GOLD))
    passed, why = leg(api, trino, read(visible=tuple(NAMES)))
    assert passed and "its row equals the SQL leg's" in why, why
    (path, query, auth), = api.seen
    assert path == PATH and auth == f"Bearer {KEY}"
    assert query == {"limit": "1", "filter.rate_date": "2026-11-27", "filter.currency": "USD"}
    assert trino.seen == [("p.a", 'SELECT * FROM "d"."f"."t" LIMIT 1', "UTC")]


@pytest.mark.parametrize("changed,says", [
    ({"rate": "1.0513000000"}, "differ"), ({"rate_available_at": "2026-11-27T16:00:00+00:00"}, "differ"),
    ({"n": 9007199254740992}, "differ"), ({"extra": 1}, "columns")])
def test_a_rest_read_fails_on_any_value_or_column_the_sql_leg_does_not_show(api, changed, says):
    api.answer = page(REST_ROW | changed)
    passed, why = leg(api, FakeTrino(av.Answer(rows=[SQL_ROW], columns=GOLD)), read(visible=tuple(NAMES)))
    assert not passed and says in why, why


def test_a_rest_read_of_an_empty_table_proves_the_200_alone_and_says_so(api):
    passed, why = leg(api, FakeTrino(av.Answer(rows=[], columns=GOLD)), read(visible=tuple(NAMES)))
    assert passed and "only the 200 is proven" in why, why
    assert api.seen[0][1] == {"limit": "1"}
    api.answer = page()
    passed, why = leg(api, FakeTrino(av.Answer(rows=[SQL_ROW], columns=GOLD)), read(visible=tuple(NAMES)))
    assert not passed and "no row" in why, why


def test_a_rest_read_asserting_rows_compares_every_row_as_a_multiset(api):
    other = SQL_ROW[:1] + ["GBP"] + SQL_ROW[2:]
    r = read(visible=tuple(NAMES), must_appear=SimpleNamespace(where=None, min_rows=1))
    api.answer = page(REST_ROW | {"currency": "GBP"}, REST_ROW)
    passed, why = leg(api, FakeTrino(av.Answer(rows=[SQL_ROW, other], columns=GOLD)), r)
    assert passed and "2 row(s) equal the SQL leg's" in why, why
    assert api.seen[-1][1] == {"limit": str(av.REST_PAGE)}
    api.answer = page(REST_ROW, REST_ROW)
    assert not leg(api, FakeTrino(av.Answer(rows=[SQL_ROW, other], columns=GOLD)), r)[0]
    api.answer = (422, {"error": "result_too_large", "message": "m"})
    passed, why = leg(api, FakeTrino(av.Answer(rows=[SQL_ROW], columns=GOLD)), r)
    assert not passed and "result_too_large" in why


@pytest.mark.parametrize("status,error,passes,says", [
    (403, "forbidden", True, "403 forbidden"), (404, "not_found", False, "no refusal"),
    (404, "not_published", False, "no refusal"), (200, None, False, "expected 403"),
    (401, "unauthorized", False, "a fault of the harness"), (503, "trino_unavailable", False, "expected 403")])
def test_a_rest_denial_is_403_forbidden_and_nothing_else(api, status, error, passes, says):
    api.answer = (status, {"error": error, "message": "m"} if error else page()[1])
    passed, why = leg(api, None, SimpleNamespace(principal="p.a", table="d.f.t", via=("rest",)))
    assert passed is passes and says in why, why


def test_a_rest_read_refused_its_key_fails_and_an_unreachable_api_fails():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    passed, why = av.rest_read(FakeTrino(av.Answer(rows=[], columns=GOLD)), read(), closed, PATH, [], KEY)
    assert not passed and "did not answer" in why and KEY not in why


def test_a_missing_key_skips_a_live_rest_leg_naming_the_command_and_never_a_release_one(tmp_path):
    env = {"apiKeys": str(tmp_path)}
    r = SimpleNamespace(principal="p.a")
    hint = av.rest_skip(r, "live", env)
    assert "make api-key PRINCIPAL=p.a TO_FILE=1" in hint and "ROTATE=1" in hint and ">" not in hint  # the terminal guard refuses a redirect
    assert av.rest_skip(r, "release", env) is None  # the lake makes every key: a missing one fails the leg
    (tmp_path / "p.a.key").write_text(KEY + "\n")
    assert av.rest_skip(r, "live", env) is None and av.api_key(env, "p.a") == KEY


def test_the_route_is_the_catalogs_and_a_relation_no_output_has_has_none(tmp_path):
    (tmp_path / "compiled/catalog/products").mkdir(parents=True)
    (tmp_path / "compiled/catalog/index.json").write_text(json.dumps({"products": [{"id": "fx-reference.ecb-fx-rates"}]}))
    cols = [{"name": "currency", "primaryKey": True, "primaryKeyPosition": 2}, {"name": "rate", "primaryKey": False},
            {"name": "rate_date", "primaryKey": True, "primaryKeyPosition": 1}]
    (tmp_path / "compiled/catalog/products/fx-reference.ecb-fx-rates.json").write_text(json.dumps({"outputs": [
        {"contractId": "fx-reference.ecb-fx-rates.gold_eur_rates_daily", "relation": "fx_reference.ecb_fx_rates.gold_eur_rates_daily", "columns": cols},
        {"contractId": "fx-reference.ecb-fx-rates.bronze_daily", "relation": "lake.ecb_fx_rates.bronze_daily", "columns": []}]}))
    assert av.route(tmp_path, "fx_reference.ecb_fx_rates.gold_eur_rates_daily") == (PATH, ["rate_date", "currency"])
    assert av.route(tmp_path, "lake.ecb_fx_rates.bronze_daily") == ("/v1/data/fx-reference/ecb-fx-rates/bronze_daily", [])
    assert av.route(tmp_path, "fx_reference.ecb_fx_rates__base.gold_eur_rates_daily") is None


# --left-by (scripts/deploy.py's left_by): a release attempted since the live one made namespaces q and
# q__base (its ddl step), a rollback dropped nothing, and the live release's rules name neither.
LEFT8 = {"id": "v8-000000000008", "namespaces": {("governance", "q"), ("governance", "q__base")},
         "tables": {("governance", "q__base", "t"), ("governance", "q__base", "dq_test_results")},
         "views": {("governance", "q", "t"): "pub_q"}}


def left_behind(**change) -> dict:
    state = catalog()
    state["governance"]["q"] = {"tables": {}, "views": {"t": view_meta(owner="pub_q")}}
    state["governance"]["q__base"] = {"tables": {"t": table()}, "views": {}}
    for path, value in change.items():
        ns, kind, name = path.split("__", 2)
        ns = {"q": "q", "qbase": "q__base"}[ns]
        if value is None:
            state["governance"][ns][kind].pop(name)
        else:
            state["governance"][ns][kind][name] = value
    return state


def test_what_a_release_since_the_live_one_left_is_left_not_drift(drift):
    found = drift(left_behind(), left=[LEFT8])
    assert found["differences"] == [], found
    assert [x.split(":")[0] for x in found["left"]] == [
        "LEFT namespace governance.q", "LEFT view governance.q.t", "LEFT namespace governance.q__base", "LEFT table governance.q__base.t"]
    assert all("declared by v8-000000000008" in x and "nothing reads it under the live rules" in x for x in found["left"])
    assert "its owner pub_q and its readers' groups are in no rule" in found["left"][1]


def test_without_left_by_the_same_namespaces_are_drift(drift):
    named = drift(left_behind())["differences"]
    assert named == ["DRIFT namespace governance.q: the release declares no such namespace",
                     "DRIFT namespace governance.q__base: the release declares no such namespace"]


@pytest.mark.parametrize("state, named", [
    (catalog(base__tables__planted=table(tag=None)), "DRIFT table governance.c__base.planted"),  # beside a live base: never loosened
    (left_behind(qbase__tables__planted=table(tag=None)), "DRIFT table governance.q__base.planted: in governance.q__base, which v8"),
    (left_behind(qbase__tables__t=None, qbase__views__t=view_meta(owner="pub_q")), "DRIFT view governance.q__base.t"),  # its TABLE's name, a view
    (left_behind(q__views__t=None, q__tables__t=table()), "DRIFT table governance.q.t"),  # its VIEW's name, a table
    (left_behind(q__views__t=view_meta(owner="pub_x")), "DRIFT view governance.q.t: in governance.q, which v8-000000000008 left, and no release declares such a view (it claims owner 'pub_x')"),
    (left_behind(q__views__t=view_meta(owner=None)), "(it claims owner None)"),  # an invoker view at its name
])
def test_anything_else_a_left_release_did_not_declare_is_still_drift(drift, state, named):
    found = drift(state, left=[LEFT8])
    assert any(named in x for x in found["differences"]), found


def platform_tables(**change) -> tuple[dict, dict]:
    """The live release declares namespace run_records with run_record alone; the catalog holds the holds
    table too, which a release attempted since (I-8's first deploy, rolled back) created at its ddl."""
    want = {**WANT, "namespaces": {"governance": {"c", "c__base", "run_records"}},
            "tables": {**WANT["tables"], ("governance", "run_records"): {"run_record"}}, **change}
    state = catalog()
    state["governance"]["run_records"] = {"tables": {"run_record": table(tag=None), "holds": table(tag=None)}, "views": {}}
    return want, state


HOLDS9 = {"id": "v9-000000000009", "namespaces": {("governance", "run_records")},
          "tables": {("governance", "run_records", "run_record"), ("governance", "run_records", "holds")}, "views": {}}


def test_a_platform_table_a_release_since_added_is_left_while_no_live_rule_reaches_it(drift):
    want, state = platform_tables(reached=lambda w, ns, t: t == "run_record")
    found = drift(state, want=want, left=[HOLDS9])
    assert found["differences"] == [] and [x.split(":")[0] for x in found["left"]] == ["LEFT table governance.run_records.holds"]
    assert "no table rule of the live release grants a privilege on it" in found["left"][0]
    reached, _ = platform_tables(reached=lambda w, ns, t: True)  # a live rule names it: DRIFT, whoever declared it
    assert any("governance.run_records.holds" in x and "a rule of the live release reaches it" in x
               for x in drift(state, want=reached, left=[HOLDS9])["differences"])
    unknown, _ = platform_tables()  # no rules read: never LEFT
    assert any("DRIFT table governance.run_records.holds" in x for x in drift(state, want=unknown, left=[HOLDS9])["differences"])
    assert any("DRIFT table governance.run_records.holds" in x for x in drift(state, want=want)["differences"])  # no --left-by


def test_rule_reach_reads_the_releases_own_rules_and_catalogs(tmp_path):
    (tmp_path / "compiled/trino/etc/catalog").mkdir(parents=True)
    for c, w in (("governance", "governance"), ("governance_build", "governance"), ("lake", "k2")):
        (tmp_path / f"compiled/trino/etc/catalog/{c}.properties").write_text(f"# x\niceberg.rest-catalog.warehouse={w}\n")
    rules = [{"user": "svc\\.platform", "catalog": "governance_build", "schema": "run_records", "table": "run_record", "privileges": ["SELECT"]},
             {"user": "dbt_c", "catalog": "governance_build", "schema": "c__base", "privileges": ["SELECT", "OWNERSHIP"]},
             {"user": "svc\\.platform", "catalog": "system", "privileges": ["SELECT"]},
             {"user": ".*", "privileges": []}]
    (tmp_path / "compiled/trino/rules.json").write_text(json.dumps({"tables": rules}))
    reached = cd.rule_reach(tmp_path)
    assert reached("governance", "run_records", "run_record") and not reached("governance", "run_records", "holds")
    assert reached("governance", "c__base", "anything")  # a builder's rule covers its whole base namespace
    assert not reached("k2", "run_records", "run_record")  # another warehouse's catalogs


def test_a_namespace_no_release_declares_is_drift_with_left_by_given(drift):
    state = left_behind()
    state["governance"]["z"] = {"tables": {}, "views": {}}
    assert drift(state, left=[LEFT8])["differences"] == ["DRIFT namespace governance.z: the release declares no such namespace"]


def test_left_by_defaults_to_the_environment_for_the_live_suites_drift_row(drift, monkeypatch):
    monkeypatch.setattr(cd, "left_by", lambda path: LEFT8 if str(path) == "/k2/releases/v8-000000000008" else pytest.fail(path))
    monkeypatch.setenv(cd.LEFT_ENV, "/k2/releases/v8-000000000008")
    assert drift(left_behind())["differences"] == []  # what access_verify's `{drift: catalog}` row calls
