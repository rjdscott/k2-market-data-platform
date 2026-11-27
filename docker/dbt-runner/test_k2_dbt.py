"""The dbt wrapper's decisions against an in-memory lake: no Trino, no catalog, no dbt.

Fake keeps tables (snapshots with their columns and whether their tests passed, main, the tag),
reader views, run records and the statements each role ran, and checks after every change the
security property of step 6: every view reads a tag on a snapshot whose tests passed (no "leak")
and selects only columns that snapshot holds with the view's types (no "broken" read).
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "platform" / "k2gen"), str(ROOT / "scripts" / "verify"),
                str(ROOT / "platform" / "runs")]
import k2_dbt as w  # noqa: E402
import k2runs  # noqa: E402
from k2gen import model as k2  # noqa: E402
from k2gen.emit import lake  # noqa: E402

RELEASE = "v1.0.0-0123456789ab"
OUT = "governance_build.access_canary__base.events"
OUT2 = "governance_build.access_canary__base.totals"
C = ("id", "region", "note", "amount")
VIEW = k2.View("governance.access_canary.events", "governance.access_canary__base.events", C, "pub_access_canary")
VIEW2 = k2.View("governance.access_canary.totals", "governance.access_canary__base.totals", C, "pub_access_canary")
V1 = {"id": "bigint", "region": "varchar", "note": "varchar"}
V2 = {"id": "bigint", "region": "varchar", "amount": "decimal(18,8)"}  # note dropped, amount added
INPUT = w.Input("fx.ecb-fx.rates", "fx.ecb_fx__base.rates", True, "audited", "ecb_fx/rates", "active")
UUID = "0123abcd-0000-4000-8000-000000000000"


def ctx(run="access_canary/r1", inputs=(INPUT,), outputs=((OUT, VIEW),)):
    return w.Ctx(RELEASE, Path(f"/k2/releases/{RELEASE}"), "governance.access-canary", run, "governance", "access_canary",
                 "dbt_access_canary", "pub_access_canary", tuple(w.Output(r, v, ("region",)) for r, v in outputs), tuple(inputs),
                 datetime.now(timezone.utc))


class Crash(BaseException):
    """The process dies here: nothing after it runs, no handler sees it."""


def build_rel(reader: str, suffix: str = "") -> str:
    catalog, ns, table = reader.split(".")
    return f"{catalog}_build.{ns}{suffix}.{table}"


class Fake:
    def __init__(self, views=(VIEW,)):
        self.tables: dict[str, dict] = {}
        self.views: dict[str, dict[str, str]] = {}
        self.view_of = {build_rel(v.relation, "__base"): v for v in views}
        self.inputs: dict[str, list[dict]] = {INPUT.relation: [{"audited": ("TAG", 7), "main": ("BRANCH", 7)}]}
        self.records: list[dict] = []
        self.statements: list[tuple[str, str]] = []
        self.violations: list[tuple] = []
        self.commits: list[list[dict]] = []
        self.ids = iter(range(1000, 10**6))
        self.build: dict[str, dict[str, str]] = {}
        self.dbt_code, self.dbt_status, self.dbt_calls = 0, "pass", []
        self.commit_status = None
        self.crash = None
        self.after_dbt = None
        self.extra_drift: list[str] = []
        self.fail_on: str | None = None
        self.meta: dict[int, dict] = {}  # each snapshot's commit time and committing query
        self.queries = iter(range(1, 10**5))
        self.report_queries = True  # dbt's run_results carry each node's (last statement's) query id
        self.list_queries = True  # Trino lists the builder's queries with their client tags
        self.listed: dict[str, list[str]] = {}  # query id: its client tags, as GET /v1/query lists them
        self.rows = True  # the build's tables have rows; an INSERT of none commits no snapshot
        self.tags: list[str] = []  # the client tag of each dbt invocation
        self.orphans: list[str] = []  # the builder's queries an earlier run left running
        self.kills: list[str] = []
        self.unkillable = False
        self.input_at = datetime.now(timezone.utc) - timedelta(minutes=30)  # every input's audited commit
        self.commit_reads: list[tuple[str, int]] = []
        self.extra_results: list[dict] = []  # more of dbt's run_results, a warn among them

    # -- set-up
    def seed(self, rel=OUT, cols=V1, tagged=True, record=True):
        sid = self._snap(rel, cols, True)
        if tagged:
            self.tables[rel]["tag"] = sid
            if record:
                self.records.append(dict(run_id="access_canary/r0", product_id="governance.access-canary", task="build",
                                         event="audited", table_name=rel, snapshot_before=None, snapshot_after=sid))
            v = self.view_of[rel]
            self.views[v.relation] = {c: cols[c] for c in v.columns if c in cols}
        return sid

    def _snap(self, rel, cols, passed, query_id=None):
        sid = next(self.ids)
        self.meta[sid] = {"committed_at": datetime.now(timezone.utc), "query_id": query_id}
        t = self.tables.setdefault(rel, {"snaps": {}, "schema": None, "main": None, "tag": None})
        t["snaps"][sid] = (dict(cols), passed)
        t["schema"], t["main"] = dict(cols), sid
        return sid

    def check(self):
        for vrel, vcols in self.views.items():
            t = self.tables.get(build_rel(vrel, "__base"))
            if t is None or t["tag"] is None:
                self.violations.append(("broken", vrel, "no tag"))
                continue
            cols, passed = t["snaps"][t["tag"]]
            if not passed:
                self.violations.append(("leak", vrel, t["tag"]))
            self.violations += [("broken", vrel, c) for c, ty in vcols.items() if cols.get(c) != ty]

    def leaks(self):
        return [v for v in self.violations if v[0] == "leak"]

    # -- the io the wrapper calls
    def refs(self, role, rel):
        if role == "runs":
            answers = self.inputs[rel]
            return dict(answers.pop(0) if len(answers) > 1 else answers[0])
        assert role == "builder", role
        t = self.tables.get(rel)
        if t is None:
            return None
        out = {}
        if t["main"] is not None:
            out["main"] = ("BRANCH", t["main"])
        if t["tag"] is not None:
            out["audited"] = ("TAG", t["tag"])
        return out

    def columns(self, rel, snapshot):
        t = self.tables[rel]
        return dict(t["schema"] if snapshot is None else t["snaps"][snapshot][0])

    def snapshots(self, rel):
        return set(self.tables[rel]["snaps"])

    def snapshot(self, rel, sid):
        assert sid in self.tables[rel]["snaps"]
        return dict(self.meta[sid])

    def running(self):
        return sorted(self.orphans)

    def committed(self, rel, sid):
        self.commit_reads.append((rel, sid))
        return self.input_at

    def kill(self, query_id):
        self.kills.append(query_id)
        if not self.unkillable:
            self.orphans.remove(query_id)

    def sql(self, role, stmt):
        self.statements.append((role, stmt))
        if self.fail_on and self.fail_on in stmt:
            raise RuntimeError("Trino refused the statement")
        q = r'"(\w+)"\."(\w+)"\."(\w+)"'
        if m := re.fullmatch(rf"CREATE OR REPLACE VIEW {q} SECURITY DEFINER AS SELECT (.+?) FROM {q} FOR VERSION AS OF 'audited' WHERE EXISTS .+", stmt):
            if role != "publisher":
                raise RuntimeError("Access Denied: Cannot create view")
            t = self.tables.get(f"{m[5]}_build.{m[6]}.{m[7]}")
            if t is None or t["tag"] is None:
                raise RuntimeError("Cannot find snapshot with reference name: audited")
            tagged = t["snaps"][t["tag"]][0]
            cols = re.findall(r'"(\w+)"', m[4])
            if missing := [c for c in cols if c not in tagged]:
                raise RuntimeError(f"Column '{missing[0]}' cannot be resolved")
            self.views[f"{m[1].removesuffix('_build')}.{m[2]}.{m[3]}"] = {c: tagged[c] for c in cols}
        elif m := re.fullmatch(rf"ALTER TABLE {q} EXECUTE rollback_to_snapshot\((\d+)\)", stmt):
            assert role == "builder", "rollback as " + role
            self.tables[".".join(m.groups()[:3])]["main"] = int(m[4])  # rows only: the schema stays (spike f)
        elif m := re.fullmatch(rf"CREATE OR REPLACE TABLE {q}(?: WITH \(partitioning = ARRAY\['region'\]\))? AS SELECT \* FROM {q} FOR VERSION AS OF BIGINT '(\d+)'", stmt):
            assert role == "builder", "replace as " + role
            rel = ".".join(m.groups()[:3])
            cols, passed = self.tables[rel]["snaps"][int(m[7])]
            self._snap(rel, cols, passed)
        elif m := re.fullmatch(rf"DROP TABLE {q}", stmt):
            assert role == "builder", "drop as " + role
            del self.tables[".".join(m.groups())]
        else:
            raise AssertionError(f"unexpected statement {stmt}")
        self.check()

    def record(self, **row):
        if self.crash == "widen" and row["event"] == "audited":
            raise Crash
        self.records.append(row)

    def history(self, product):
        return [{k: r.get(k) for k in ("run_id", "event", "table_name", "snapshot_before", "snapshot_after")}
                for r in self.records if r["product_id"] == product and r["task"] == "build"]

    def query(self, tag):  # one statement of a dbt invocation: its id, listed with the invocation's client tag
        qid = f"20261127_000000_{next(self.queries):05d}_dbtqy"
        self.listed[qid] = [tag] if tag else []
        return qid

    def tagged(self, tag):
        return {q for q, tags in self.listed.items() if tag in tags} if self.list_queries else set()

    def dbt(self, argv, tag=None):
        self.dbt_calls.append(argv)
        self.tags.append(tag)
        results = []
        for rel in self.view_of:  # every output is rebuilt; its tests pass only on a passing build
            # dbt-trino's table of an enforced contract, as measured on Trino 484: CREATE OR REPLACE
            # TABLE commits an empty snapshot, then INSERT commits the rows only when there are some;
            # run_results names the INSERT alone (scripts/verify/run-path.sh, claim `every`).
            cols = self.build.get(rel) or (self.tables[rel]["schema"] if rel in self.tables else V1)
            create, insert = self.query(tag), self.query(tag)
            self._snap(rel, cols, self.dbt_code == 0, create)
            if self.rows:
                self._snap(rel, cols, self.dbt_code == 0, insert)
            results.append({"status": self.dbt_status, "adapter_response": {"query_id": insert} if self.report_queries else {}})
        results += self.extra_results
        self._snap("governance_build.access_canary__base.dq_test_results", {"run_id": "varchar"}, True)  # the results hook
        self.check()
        if self.after_dbt:
            self.after_dbt(self)
        return self.dbt_code, {"metadata": {"invocation_id": UUID}, "results": results}

    def commit(self, warehouse, changes):
        if self.crash == "narrow":
            raise Crash
        self.commits.append(changes)
        if self.commit_status:
            return self.commit_status
        for ch in changes:
            t = self.tables[f"{warehouse}_build.{ch['identifier']['namespace'][0]}.{ch['identifier']['name']}"]
            for r in ch["requirements"]:
                if t["main" if r["ref"] == "main" else "tag"] != r["snapshot-id"]:
                    return 409
        for ch in changes:
            t = self.tables[f"{warehouse}_build.{ch['identifier']['namespace'][0]}.{ch['identifier']['name']}"]
            t["tag"] = ch["updates"][0]["snapshot-id"]
        self.check()
        if self.crash == "tag":
            raise Crash
        return 204

    def view_sql(self, view):
        cols = self.views.get(view.relation)
        return None if cols is None else lake.view_sql(view, list(cols))

    def drift(self):
        diffs = list(self.extra_drift)
        for rel, v in self.view_of.items():
            t = self.tables.get(rel)
            if not t or t["tag"] is None:
                continue
            audited = [r["snapshot_after"] for r in self.records if r["table_name"] == rel and r["event"] == "audited"]
            if not audited or audited[-1] != t["tag"]:
                diffs.append(f"DRIFT tag {rel}: {t['tag']} against record {audited[-1:]}")
            tagged = t["snaps"][t["tag"]][0]
            if list(self.views.get(v.relation, {})) != [c for c in v.columns if c in tagged]:
                diffs.append(f"DRIFT view {v.relation}")
        return {"differences": diffs, "pending": [], "skipped": []}


def events(fake, run=None):
    return [(r["event"], r.get("table_name")) for r in fake.records if run is None or r["run_id"] == run]


# ---- W1-14: resolve, started, one dbt build ---------------------------------------------------

def test_a_pass_pins_each_input_runs_one_build_and_records_started():
    fake = Fake()
    s0 = fake.seed()
    c = ctx()
    fake.input_at = c.started_at - timedelta(minutes=30)
    code, expire = w.run(c, fake)
    assert (code, expire) == (0, [])
    (argv,) = fake.dbt_calls
    assert argv[:4] == ["dbt", "build", "--selector", "access_canary"]
    assert json.loads(argv[argv.index("--vars") + 1]) == {
        "k2_inputs": {"ecb_fx/rates": {"snapshot": 7}}, "k2_run_id": "access_canary/r1", "k2_release": RELEASE}
    started = [r for r in fake.records if r["event"] == "started"]
    assert [(r["table_name"], r["snapshot_before"]) for r in started] == [(OUT, s0)]
    assert started[0]["inputs"] == [{"contractId": INPUT.contract, "relation": INPUT.relation, "snapshot": 7,
                                     "state": "active", "lagMinutes": 30.0}]
    assert fake.commit_reads == [(INPUT.relation, 7)]  # once per input, not per output


def test_a_required_input_never_tested_exits_4_before_anything_is_written():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{"main": ("BRANCH", 3)}]  # a table, never tagged
    assert w.run(ctx(), fake) == (4, [])
    assert fake.dbt_calls == [] and events(fake, "access_canary/r1") == []


def test_an_optional_input_never_tested_is_left_out_of_k2_inputs():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{}]
    optional = dataclasses.replace(INPUT, required=False)
    assert w.run(ctx(inputs=(optional,)), fake)[0] == 0
    assert json.loads(fake.dbt_calls[0][fake.dbt_calls[0].index("--vars") + 1])["k2_inputs"] == {}


def k2_inputs(fake):
    return json.loads(fake.dbt_calls[0][fake.dbt_calls[0].index("--vars") + 1])["k2_inputs"]


# ---- W0-7: an optional input read empty when absent ---------------------------------------------

@pytest.mark.parametrize("when_absent", ["empty", "emptyIntoTable"])
@pytest.mark.parametrize("state,refs", [("paused", {"audited": ("TAG", 7)}), ("active", {})])  # paused, or never tested
def test_an_input_read_empty_when_absent_is_passed_absent_and_recorded_with_no_snapshot(state, refs, when_absent):
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [refs]
    port = dataclasses.replace(INPUT, required=False, state=state, when_absent=when_absent)
    assert w.run(ctx(inputs=(port,)), fake)[0] == 0
    assert k2_inputs(fake) == {"ecb_fx/rates": {"absent": True}}
    (started,) = [r for r in fake.records if r["event"] == "started"]
    assert started["inputs"] == [{"contractId": INPUT.contract, "relation": INPUT.relation, "snapshot": None,
                                  "state": state, "lagMinutes": None}]


@pytest.mark.parametrize("when_absent", ["fail", "empty"])
def test_a_present_input_is_pinned_as_before_whatever_its_port_says(when_absent):
    fake = Fake()
    fake.seed()
    port = dataclasses.replace(INPUT, required=False, when_absent=when_absent)
    assert w.run(ctx(inputs=(port,)), fake)[0] == 0
    assert k2_inputs(fake) == {"ecb_fx/rates": {"snapshot": 7}}
    assert [r["inputs"][0]["snapshot"] for r in fake.records if r["event"] == "started"] == [7]


def test_a_paused_input_whose_port_says_fail_is_read_at_its_tested_snapshot():
    fake = Fake()
    fake.seed()
    port = dataclasses.replace(INPUT, required=False, state="paused")
    assert w.run(ctx(inputs=(port,)), fake)[0] == 0
    assert k2_inputs(fake) == {"ecb_fx/rates": {"snapshot": 7}}


def test_a_required_input_never_tested_exits_4_whatever_its_port_says():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{}]
    assert w.run(ctx(inputs=(dataclasses.replace(INPUT, when_absent="empty"),)), fake) == (4, [])
    assert fake.dbt_calls == []


def test_when_absent_is_read_from_the_pipeline_fail_when_left_out_and_refused_when_unknown():
    tables = {INPUT.contract: (INPUT.relation, "audited", "paused")}
    port = {"contractId": INPUT.contract, "relation": INPUT.relation, "required": False, "maxLagMinutes": None}

    def read(**extra):
        pipeline = {**PIPELINE, "inputs": [{**port, **extra}]}
        return w.ctx_from(RELEASE, Path("/r"), "governance.access-canary", "access_canary/r1", pipeline, VIEWS, tables, SCHEMAS).inputs

    assert read() == (dataclasses.replace(INPUT, required=False, state="paused"),)  # a release compiled before W0-7
    assert read(whenAbsent="empty")[0].when_absent == "empty"
    with pytest.raises(w.Refused):
        read(whenAbsent="skip")


def test_the_second_read_catches_a_tag_that_moved_and_resolution_restarts():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{"audited": ("TAG", 7)}, {"audited": ("TAG", 8)}, {"audited": ("TAG", 8)}]
    assert w.run(ctx(), fake)[0] == 0
    assert json.loads(fake.dbt_calls[0][fake.dbt_calls[0].index("--vars") + 1])["k2_inputs"]["ecb_fx/rates"] == {"snapshot": 8}


def test_inputs_that_never_settle_fail_the_build_before_dbt():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{"audited": ("TAG", n)} for n in range(20)]
    assert w.run(ctx(), fake) == (2, [])
    assert fake.dbt_calls == []


def test_a_branch_named_audited_is_no_tested_snapshot():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{"audited": ("BRANCH", 7)}]
    assert w.run(ctx(), fake)[0] == 2 and fake.dbt_calls == []


def test_the_dbt_environment_is_an_allow_list_and_the_argv_holds_no_secret():
    environ = {"PATH": "/venv/bin", "K2_TRINO_HOST": "trino", "K2_TRINO_INTERNAL_PORT": "8443",
               "K2_TRINO_USER": "dbt_access_canary", "K2_PUB_USER": "pub_access_canary", "K2_RUNS_USER": "svc.runs",
               "K2_CATALOG_URI": "http://lakekeeper:8181/catalog", "K2_RELEASE": RELEASE, "AWS_SECRET_ACCESS_KEY": "x"}
    env = w.dbt_env(environ, "s3cr3t-builder", "k2-dbt-" + "0" * 32)
    assert set(env) == {"PATH", "HOME", "LANG", "K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_TRINO_USER",
                        "DBT_ENV_SECRET_K2_TRINO_PASSWORD", "K2_DBT_CLIENT_TAG", "DBT_SEND_ANONYMOUS_USAGE_STATS", "DBT_USE_COLORS"}
    assert env["K2_DBT_CLIENT_TAG"] == "k2-dbt-" + "0" * 32
    profile = (ROOT / "dbt" / "profiles.yml").read_text()  # the image's profile puts the tag on every statement
    assert "client_tags: [\"{{ env_var('K2_DBT_CLIENT_TAG', 'k2-dbt-manual') }}\"]" in profile
    assert [k for k, v in env.items() if "s3cr3t" in v] == ["DBT_ENV_SECRET_K2_TRINO_PASSWORD"]
    assert env["K2_TRINO_USER"] == "dbt_access_canary" and env["DBT_USE_COLORS"] == "false"
    argv = w.dbt_argv(ctx(), {INPUT.contract: 7})
    assert not any("password" in a or "s3cr3t" in a for a in argv)


@pytest.mark.parametrize("code,statuses,expected", [
    (0, ["pass", "pass"], 0), (1, ["pass", "fail"], 1), (1, ["fail", "error"], 2), (1, ["pass"], 2),  # a failed hook
    (2, None, 2), (0, None, 2), (0, ["fail"], 2), (1, None, 2)])
def test_verdict_any_non_zero_exit_fails_and_only_a_test_failure_is_1(code, statuses, expected):
    results = None if statuses is None else {"results": [{"status": s} for s in statuses]}
    assert w.verdict(code, results) == expected


# ---- W1-15: rollback ---------------------------------------------------------------------------

def test_a_failed_test_with_the_schema_unchanged_rolls_back_and_names_the_snapshot_to_expire():
    fake = Fake()
    s0 = fake.seed()
    fake.dbt_code, fake.dbt_status = 1, "fail"
    code, expire = w.run(ctx(), fake)
    failed = sorted(set(fake.tables[OUT]["snaps"]) - {s0})  # the CREATE's empty snapshot and the INSERT's
    assert len(failed) == 2 and code == 1 and fake.tables[OUT]["main"] == s0 and fake.tables[OUT]["tag"] == s0
    assert ("builder", f'ALTER TABLE {k2.quote_relation(OUT)} EXECUTE rollback_to_snapshot({s0})') in fake.statements
    assert expire == [{"table": OUT, "ids": failed}]
    (rb,) = [r for r in fake.records if r["event"] == "rolled_back"]
    assert (rb["snapshot_before"], rb["snapshot_after"], rb["dbt_invocation_id"]) == (s0, s0, UUID)
    assert fake.violations == []


def test_a_failed_build_that_changed_the_schema_is_restored_by_create_or_replace():
    fake = Fake()
    s0 = fake.seed()
    fake.build[OUT], fake.dbt_code, fake.dbt_status = V2, 1, "fail"
    code, expire = w.run(ctx(), fake)
    t = fake.tables[OUT]
    assert code == 1 and t["schema"] == V1 and t["snaps"][t["main"]][0] == V1 and t["tag"] == s0
    assert any(s.startswith("CREATE OR REPLACE TABLE") and s.endswith(f"FOR VERSION AS OF BIGINT '{s0}'")
               for _, s in fake.statements)
    restored = t["main"]
    assert expire == [{"table": OUT, "ids": sorted(set(t["snaps"]) - {s0, restored})}] and restored not in expire[0]["ids"]


def test_a_failed_first_build_drops_the_table_it_made_and_leaves_the_results_table():
    fake = Fake()
    fake.dbt_code, fake.dbt_status = 1, "fail"
    fake.build[OUT] = V1
    code, expire = w.run(ctx(), fake)
    assert (code, expire) == (1, []) and OUT not in fake.tables
    assert ("builder", f"DROP TABLE {k2.quote_relation(OUT)}") in fake.statements
    assert "governance_build.access_canary__base.dq_test_results" in fake.tables  # not an output: never restored
    assert not any("dq_test_results" in s for _, s in fake.statements)


def test_a_build_error_exits_2_and_restores():
    fake = Fake()
    s0 = fake.seed()
    fake.dbt_code, fake.dbt_status = 2, "error"
    assert w.run(ctx(), fake)[0] == 2 and fake.tables[OUT]["main"] == s0
    assert events(fake, "access_canary/r1") == [("started", OUT), ("rolled_back", OUT)]


def test_a_failed_restore_exits_3():
    fake = Fake()
    fake.seed()
    fake.dbt_code, fake.dbt_status, fake.fail_on = 1, "fail", "rollback_to_snapshot"
    assert w.run(ctx(), fake)[0] == 3


def test_a_restore_by_create_or_replace_keeps_the_contracts_partitioning_and_sort():
    sql = w.restore_statement(OUT, 5, True, V1, V2, ("region", "day(event_ts)"), ("id",))
    assert sql == (f"CREATE OR REPLACE TABLE {k2.quote_relation(OUT)} WITH (partitioning = ARRAY['region', 'day(event_ts)'], "
                   f"sorted_by = ARRAY['id']) AS SELECT * FROM {k2.quote_relation(OUT)} FOR VERSION AS OF BIGINT '5'")
    forged = w.restore_statement(OUT, 5, True, V1, V2, ("x']) AS SELECT 1 --",))
    assert "ARRAY['x'']) AS SELECT 1 --']" in forged  # one literal, never SQL
    fake = Fake()
    fake.seed()
    fake.build[OUT], fake.dbt_code, fake.dbt_status = V2, 1, "fail"
    assert w.run(ctx(), fake)[0] == 1
    assert any("WITH (partitioning = ARRAY['region'])" in s for _, s in fake.statements if s.startswith("CREATE OR REPLACE TABLE"))


def test_a_tagged_table_is_never_dropped():
    with pytest.raises(w.Refused):
        w.restore_statement(OUT, None, True, None, V1)
    assert w.restore_statement(OUT, 5, True, V1, dict(reversed(V1.items()))).startswith("CREATE OR REPLACE TABLE")  # order counts


# ---- W1-16: publish ----------------------------------------------------------------------------

def test_a_pass_moves_every_outputs_tag_in_one_compare_and_set_commit():
    fake = Fake(views=(VIEW, VIEW2))
    s0, t0 = fake.seed(OUT), fake.seed(OUT2)
    assert w.run(ctx(outputs=((OUT, VIEW), (OUT2, VIEW2))), fake) == (0, [])
    (changes,) = fake.commits
    tested = {OUT: fake.tables[OUT]["main"], OUT2: fake.tables[OUT2]["main"]}
    assert [c["requirements"] for c in changes] == [
        [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested[OUT]},
         {"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": s0}],
        [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested[OUT2]},
         {"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": t0}]]
    assert all(fake.tables[r]["tag"] == tested[r] for r in tested)
    assert [(r["event"], r.get("snapshot_after")) for r in fake.records if r["run_id"] == "access_canary/r1"][-2:] == [
        ("audited", tested[OUT]), ("audited", tested[OUT2])]
    assert not [s for _, s in fake.statements if "VIEW" in s]  # no column changed: the tag move is the publish
    assert fake.violations == []


def test_a_schema_change_narrows_before_the_tag_and_widens_after_it():
    fake = Fake()
    fake.seed()
    fake.build[OUT] = V2
    order = []
    fake.after_dbt = lambda f: order.append("dbt")
    real_sql, real_commit = fake.sql, fake.commit
    fake.sql = lambda role, s: (order.append(re.findall(r'SELECT (.+?) FROM', s)[0]), real_sql(role, s))[1]
    fake.commit = lambda wh, ch: (order.append("commit"), real_commit(wh, ch))[1]
    assert w.run(ctx(), fake)[0] == 0
    assert order == ["dbt", '"id", "region"', "commit", '"id", "region", "amount"']
    assert fake.views[VIEW.relation] == V2 and fake.violations == []


def test_a_first_publish_creates_the_view_after_the_tag():
    fake = Fake()
    assert w.run(ctx(), fake)[0] == 0
    assert fake.views[VIEW.relation] == V1 and fake.tables[OUT]["tag"] == fake.tables[OUT]["main"]
    assert fake.commits[0][0]["requirements"][1] == {"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": None}
    assert fake.violations == []


def test_a_409_exits_3_with_views_narrowed_and_the_tag_unmoved():
    fake = Fake()
    s0 = fake.seed()
    fake.build[OUT], fake.commit_status = V2, 409
    assert w.run(ctx(), fake)[0] == 3
    assert fake.tables[OUT]["tag"] == s0 and fake.views[VIEW.relation] == {"id": "bigint", "region": "varchar"}
    assert "audited" not in [e for e, _ in events(fake, "access_canary/r1")] and fake.violations == []


def test_a_write_after_the_tested_read_fails_the_compare_and_set():
    fake = Fake()
    s0 = fake.seed()
    real = fake.commit
    fake.commit = lambda wh, ch: (fake._snap(OUT, V1, False), real(wh, ch))[1]  # main moves before the commit
    assert w.run(ctx(), fake)[0] == 3
    assert fake.tables[OUT]["tag"] == s0 and fake.leaks() == []


def test_a_tag_moved_while_the_views_narrow_fails_the_compare_and_set():
    fake = Fake()
    fake.seed()
    fake.build[OUT] = V2
    real, moved = fake.sql, 999

    def sql(role, stmt):  # another publisher, between this run's reads and its commit; main stays the tested one
        real(role, stmt)
        fake.tables[OUT]["snaps"][moved] = (V1, True)
        fake.tables[OUT]["tag"] = moved
    fake.sql = sql
    assert w.run(ctx(), fake)[0] == 3 and fake.tables[OUT]["tag"] == moved
    assert "audited" not in [e for e, _ in events(fake, "access_canary/r1")]


def test_a_tag_moved_during_the_build_exits_3_and_no_view_is_touched():
    fake = Fake()
    fake.seed()
    fake.build[OUT] = V2
    fake.after_dbt = lambda f: f.tables[OUT].update(tag=f.tables[OUT]["main"])
    assert w.run(ctx(), fake)[0] == 3
    assert not [s for _, s in fake.statements if "VIEW" in s] and fake.commits == []


def test_no_answer_from_the_catalog_on_the_commit_exits_3_narrowed():
    fake = Fake()
    fake.seed()
    fake.build[OUT] = V2

    def unreachable(wh, ch):
        raise w.catalog_drift.Unreachable("timed out")
    fake.commit = unreachable
    assert w.run(ctx(), fake)[0] == 3 and fake.views[VIEW.relation] == {"id": "bigint", "region": "varchar"}


def test_no_common_column_takes_the_deliberate_path_and_never_leaks():
    fake = Fake()
    fake.seed()
    fake.build[OUT] = {"id": "varchar", "region": "bigint"}  # every common name retyped
    assert w.run(ctx(), fake)[0] == 3  # published, and pages
    assert fake.tables[OUT]["tag"] == fake.tables[OUT]["main"] and fake.views[VIEW.relation] == fake.build[OUT]
    assert fake.leaks() == []  # readers' view failed closed between the commit and the widen


@pytest.mark.parametrize("crash", ["narrow", "tag", "widen"])
def test_a_crash_at_each_point_of_the_publish_never_exposes_untested_data(crash):
    fake = Fake()
    fake.seed()
    fake.build[OUT], fake.crash = V2, crash
    with pytest.raises(Crash):
        w.run(ctx(), fake)
    assert fake.violations == []


ORPHAN = "20261127_010203_00042_orph1"


def test_a_commit_after_the_tests_by_a_query_dbt_did_not_run_is_refused_and_never_tagged():
    # an orphaned CTAS of an earlier run commits between this run's tests and the publish (review Q1)
    fake = Fake()
    s0 = fake.seed()
    fake.after_dbt = lambda f: f._snap(OUT, V1, False, query_id=ORPHAN)
    assert w.run(ctx(), fake)[0] == 3
    assert fake.tables[OUT]["tag"] == s0 and fake.commits == [] and fake.leaks() == []
    assert not [s for _, s in fake.statements if "VIEW" in s]


@pytest.mark.parametrize("lacking", ["dbt", "snapshot"])
def test_with_a_query_id_missing_on_one_side_the_dbt_invocations_time_decides(lacking):
    fake = Fake()
    fake.seed()
    unnamed = lambda f: f.meta[f.tables[OUT]["main"]].update(query_id=None)  # noqa: E731
    fake.report_queries = fake.list_queries = lacking != "dbt"
    fake.after_dbt = None if lacking == "dbt" else unnamed
    assert w.run(ctx(), fake)[0] == 0  # committed inside the invocation
    tagged = fake.tables[OUT]["tag"]
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    fake.after_dbt = lambda f: (unnamed(f), f.meta[f.tables[OUT]["main"]].update(committed_at=later))
    assert w.run(ctx(run="access_canary/r2"), fake)[0] == 3  # committed after dbt returned
    assert fake.tables[OUT]["tag"] == tagged and fake.leaks() == []


def test_an_empty_table_published_by_its_create_which_dbt_never_reports_is_its_own_commit():
    # live, 2026-11-27: governance.quality-results built test_results with no rows; main was the
    # CREATE OR REPLACE's snapshot, and run_results named only the INSERT, which committed nothing
    fake = Fake()
    fake.seed()
    fake.rows = False
    assert w.run(ctx(), fake)[0] == 0
    main = fake.tables[OUT]["main"]
    assert fake.tables[OUT]["tag"] == main and fake.meta[main]["query_id"] in fake.tagged(fake.tags[-1])
    assert fake.violations == []


def test_each_run_has_its_own_client_tag_so_an_earlier_runs_orphan_is_refused():
    # run r1's statement outlives it (its client gone) and commits after r2's tests: it carries r1's tag
    fake = Fake()
    fake.seed()
    assert w.run(ctx(), fake)[0] == 0
    tagged = fake.tables[OUT]["tag"]

    def orphan(f):
        f.listed[ORPHAN] = [f.tags[0]]
        f._snap(OUT, V1, False, query_id=ORPHAN)
    fake.after_dbt = orphan
    assert w.run(ctx(run="access_canary/r1"), fake)[0] == 3  # a retry of the same run id: a new tag all the same
    assert fake.tags[0] != fake.tags[1] and all(re.fullmatch(r"k2-dbt-[0-9a-f]{32}", t) for t in fake.tags)
    assert fake.tables[OUT]["tag"] == tagged and fake.leaks() == []


def test_only_well_formed_query_ids_count_as_reported():
    results = {"results": [{"adapter_response": {"query_id": "20261127_000000_00001_abcde"}}, {"adapter_response": {}},
                           {"adapter_response": {"query_id": "x' OR 1=1"}}, {"status": "pass"}, {"adapter_response": None}]}
    assert w.reported(results) == {"20261127_000000_00001_abcde"} and w.reported(None) == set()


def test_an_orphaned_builder_query_is_killed_before_reconcile_reads_anything():
    fake = Fake()
    fake.seed()
    fake.orphans = [ORPHAN, "20261127_010203_00043_orph2"]
    real = fake.history

    def history(product):  # reconcile's first read
        assert fake.orphans == [] and fake.kills == [ORPHAN, "20261127_010203_00043_orph2"]
        return real(product)
    fake.history = history
    assert w.run(ctx(), fake) == (0, [])


def test_a_builder_query_that_outlives_the_kill_exits_3_before_anything_is_read(monkeypatch):
    monkeypatch.setattr(w, "ORPHAN_WAIT", 0)
    fake = Fake()
    fake.seed()
    before = list(fake.records)
    fake.orphans, fake.unkillable = [ORPHAN], True
    fake.history = lambda product: pytest.fail("reconcile read the run records")
    assert w.run(ctx(), fake) == (3, []) and fake.kills == [ORPHAN]
    assert fake.dbt_calls == [] and fake.statements == [] and fake.records == before


def test_the_live_orphan_calls_list_the_builders_own_and_cancel_only_a_query_id(monkeypatch):
    live = object.__new__(w.Live)
    live.ctx = ctx()
    sent = []
    listing = [{"queryId": ORPHAN, "state": "RUNNING", "session": {"user": "dbt_access_canary"}},
               {"queryId": "20261127_010203_00043_done1", "state": "FINISHED", "session": {"user": "dbt_access_canary"}},
               {"queryId": "20261127_010203_00044_fail1", "state": "FAILED", "session": {"user": "dbt_access_canary"}},
               {"queryId": "20261127_010203_00045_other", "state": "RUNNING", "session": {"user": "dbt_other"}}]
    monkeypatch.setattr(live, "rest", lambda method, path: sent.append((method, path)) or (listing if method == "GET" else None),
                        raising=False)
    assert live.running() == [ORPHAN] and sent == [("GET", "/v1/query")]
    tag = "k2-dbt-" + "a" * 32
    listing[1]["session"]["clientTags"] = [tag]  # this run's statement
    listing[2]["session"]["clientTags"] = ["k2-dbt-" + "b" * 32]  # an earlier run's
    listing[3]["session"]["clientTags"] = [tag]  # another user's, the tag copied
    listing.append({"queryId": "x' OR 1=1", "state": "FINISHED", "session": {"user": "dbt_access_canary", "clientTags": [tag]}})
    assert live.tagged(tag) == {"20261127_010203_00043_done1"}
    live.kill(ORPHAN)
    assert sent[-1] == ("DELETE", f"/v1/query/{ORPHAN}")
    for bad in ("20261127_010203_00042_orph1/../../v1/node", "", "x"):
        with pytest.raises(w.Refused):
            live.kill(bad)

    def refused(method, path):
        raise w.urllib.error.HTTPError(path, 403, "Forbidden", {}, None)
    monkeypatch.setattr(live, "rest", refused, raising=False)
    with pytest.raises(w.Refused, match="HTTP 403"):
        live.kill(ORPHAN)


# ---- W1-17: reconcile --------------------------------------------------------------------------

def test_a_clean_state_reconciles_to_nothing():
    fake = Fake()
    fake.seed()
    before = list(fake.records)
    w.reconcile(ctx(), fake)
    assert fake.statements == [] and fake.records == before


def test_an_unfinished_run_is_restored_to_its_tag_and_reconciled():
    fake = Fake()
    s0 = fake.seed()
    fake.records.append(dict(run_id="access_canary/r0", product_id="governance.access-canary", task="build",
                             event="started", table_name=OUT, snapshot_before=s0, snapshot_after=None))
    fake._snap(OUT, V2, False)  # the dead run's build
    w.reconcile(ctx(), fake)
    t = fake.tables[OUT]
    assert t["schema"] == V1 and t["snaps"][t["main"]][0] == V1 and t["tag"] == s0  # rows and schema of the tag
    assert (fake.records[-1]["event"], fake.records[-1]["run_id"], fake.records[-1]["snapshot_after"]) == (
        "reconciled", "access_canary/r0", t["main"])
    n = len(fake.statements)
    w.reconcile(ctx(), fake)  # main is the recorded restore: clean, nothing more to do
    assert len(fake.statements) == n and fake.violations == []


def test_an_unfinished_run_on_a_never_tagged_table_drops_it():
    fake = Fake()
    fake.seed(tagged=False)
    fake.records.append(dict(run_id="access_canary/r0", product_id="governance.access-canary", task="build",
                             event="started", table_name=OUT, snapshot_before=None, snapshot_after=None))
    w.reconcile(ctx(), fake)
    assert OUT not in fake.tables and fake.records[-1]["event"] == "reconciled"


def test_a_tag_ahead_of_the_records_gets_its_audited_row_and_its_view():
    fake = Fake()
    fake.seed()
    s1 = fake._snap(OUT, V2, True)
    fake.records.append(dict(run_id="access_canary/r0", product_id="governance.access-canary", task="build",
                             event="started", table_name=OUT, snapshot_before=None, snapshot_after=None))
    fake.tables[OUT]["tag"] = s1  # it died after the commit
    fake.views[VIEW.relation] = {"id": "bigint", "region": "varchar"}  # narrowed
    w.reconcile(ctx(), fake)
    assert (fake.records[-1]["event"], fake.records[-1]["snapshot_after"], fake.records[-1]["run_id"]) == (
        "audited", s1, "access_canary/r0")
    assert fake.views[VIEW.relation] == V2 and fake.drift()["differences"] == []


def test_a_tag_no_unfinished_run_explains_exits_3_before_dbt():
    fake = Fake()
    fake.seed()
    fake.tables[OUT]["tag"] = fake._snap(OUT, V1, True)  # moved by someone, the records say otherwise
    assert w.run(ctx(), fake) == (3, []) and fake.dbt_calls == []


def test_any_drift_left_after_the_repairs_exits_3_before_dbt():
    fake = Fake()
    fake.seed()
    fake.extra_drift = ["DRIFT view governance.access_canary.planted: the release declares no such view"]
    assert w.run(ctx(), fake) == (3, []) and fake.dbt_calls == []


def test_a_rerun_with_the_same_run_id_is_idempotent():
    fake = Fake()
    fake.seed()
    fake.build[OUT], fake.crash = V2, "narrow"
    with pytest.raises(Crash):
        w.run(ctx(), fake)
    fake.crash = None
    assert w.run(ctx(), fake) == (0, []) and w.run(ctx(), fake) == (0, [])
    assert fake.violations == [] and fake.drift()["differences"] == []


@pytest.mark.parametrize("crash,first_run_left", [("narrow", "reconciled"), ("tag", "audited"), ("widen", "audited")])
def test_after_a_crash_at_each_point_of_the_publish_the_next_run_finishes_it(crash, first_run_left):
    fake = Fake()
    fake.seed()
    fake.build[OUT], fake.crash = V2, crash
    with pytest.raises(Crash):
        w.run(ctx(), fake)
    assert fake.violations == []
    fake.crash = None
    assert w.run(ctx(run="access_canary/r2"), fake) == (0, [])
    assert fake.violations == []
    assert events(fake, "access_canary/r1")[-1] == (first_run_left, OUT)
    t = fake.tables[OUT]
    assert t["tag"] == t["main"] and fake.views[VIEW.relation] == V2 and fake.drift()["differences"] == []


def test_the_reconciles_drift_check_covers_this_product_and_holds_tags_to_svc_runs(monkeypatch):
    seen = {}

    def drift(uri, want, newest_audited=None, only=None):
        seen.update(want=want, only=only, recorded=newest_audited(OUT))
        return {"differences": [], "pending": [], "skipped": []}
    monkeypatch.setattr(w.catalog_drift, "expected", lambda path: "the release")
    monkeypatch.setattr(w.catalog_drift, "drift", drift)
    live = object.__new__(w.Live)
    live.ctx, live.uri, live.conns = ctx(), "http://lakekeeper:8181/catalog", {"runs": "svc.runs"}
    live.k2runs = types.SimpleNamespace(newest_audited=lambda conn, table: 5 if (conn, table) == ("svc.runs", OUT) else None)
    assert live.drift()["differences"] == []
    assert seen == {"want": "the release", "only": {("governance", "access_canary__base"), ("governance", "access_canary")},
                    "recorded": 5}


def test_a_snapshot_id_from_a_run_record_is_parsed_never_spliced():
    fake = Fake()
    fake.seed()
    fake.records.append(dict(run_id="access_canary/r0", product_id="governance.access-canary", task="build",
                             event="audited", table_name=OUT, snapshot_before=None, snapshot_after="1 OR 1=1"))
    assert w.run(ctx(), fake) == (3, []) and fake.statements == []


# ---- W1-7: each input's lag, a degraded publish ---------------------------------------------------

def warn(name):
    return {"unique_id": name, "status": "warn", "adapter_response": {}}


def state(fake):
    """k2runs.state over the fake's records, in the order written."""
    return k2runs.state([{**r, "recorded_at": n} for n, r in enumerate(fake.records)])


def test_a_pass_with_a_warn_publishes_degraded_in_the_xcom_and_every_audited_row():
    fake = Fake(views=(VIEW, VIEW2))
    fake.seed(OUT), fake.seed(OUT2)
    fake.extra_results = [warn(f"test.k2.held_rows_zero_{n}." + "x" * 300) for n in range(7)]
    xcom = {}
    assert w.run(ctx(outputs=((OUT, VIEW), (OUT2, VIEW2))), fake, xcom) == (0, [])
    names = [f"test.k2.held_rows_zero_{n}." + "x" * 300 for n in range(5)]
    assert xcom == {"degraded": True, "warned": [n[:w.WARNED_CHARS] for n in names]}
    audited = [r for r in fake.records if r["run_id"] == "access_canary/r1" and r["event"] == "audited"]
    assert [r["table_name"] for r in audited] == [OUT, OUT2]
    for r in audited:
        assert json.loads(r["detail"]) == xcom and len(r["detail"]) <= 1000  # k2runs.record keeps 1000
    assert not [r for r in fake.records if r["event"] == "started" and "detail" in r]
    assert state(fake) == "degraded"
    assert fake.tables[OUT]["tag"] == fake.tables[OUT]["main"] and fake.violations == []  # published as before


def test_a_pass_with_no_warn_writes_what_it_wrote_before(monkeypatch, capsys):
    fake = Fake()
    fake.seed()
    fake.extra_results = [{"unique_id": "test.k2.ok", "status": "pass"}, {"unique_id": "model.k2.m", "status": "success"}]
    monkeypatch.setattr(w, "load", lambda *a: ctx())
    monkeypatch.setattr(w, "Live", lambda c, env: fake)
    assert w.main(["build", "--release", RELEASE, "--product", "governance.access-canary", "--run-id", "access_canary/r1"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == '{"exit":0,"expire":[]}'
    (audited,) = [r for r in fake.records if r["run_id"] == "access_canary/r1" and r["event"] == "audited"]
    assert set(audited) == {"run_id", "product_id", "release_id", "task", "started_at", "event", "table_name",
                            "snapshot_before", "snapshot_after", "dbt_invocation_id"}  # no detail, as before
    assert state(fake) == "complete"


@pytest.mark.parametrize("code,status,exit_code", [(1, "fail", 1), (2, "error", 2)])
def test_a_failure_beside_a_warn_rolls_back_and_is_never_degraded(code, status, exit_code):
    fake = Fake()
    s0 = fake.seed()
    fake.dbt_code, fake.dbt_status = code, status
    fake.extra_results = [warn("test.k2.held_rows_zero")]
    xcom = {}
    assert w.run(ctx(), fake, xcom)[0] == exit_code
    assert xcom == {} and fake.tables[OUT]["main"] == s0 and fake.tables[OUT]["tag"] == s0
    assert events(fake, "access_canary/r1") == [("started", OUT), ("rolled_back", OUT)]
    assert not [r for r in fake.records if "degraded" in str(r.get("detail"))]
    statuses = [warn("test.k2.w"), *({"unique_id": f"test.k2.{s}", "status": s} for s in ("fail", "error", "pass", "skipped"))]
    assert w.warned({"results": statuses}) == ["test.k2.w"] and w.warned(None) == []  # only warn is a warn


def test_a_publish_refused_after_a_warn_is_not_degraded():
    fake = Fake()
    fake.seed()
    fake.build[OUT], fake.commit_status = V2, 409
    fake.extra_results = [warn("test.k2.held_rows_zero")]
    xcom = {}
    assert w.run(ctx(), fake, xcom)[0] == 3 and xcom == {}  # nothing published, nothing degraded


def test_each_inputs_lag_is_the_run_start_minus_its_audited_commit_never_below_zero():
    c = ctx()
    fake = Fake()
    fake.seed()
    fake.input_at = c.started_at - timedelta(minutes=90, seconds=30)
    assert w.run(c, fake)[0] == 0
    assert [r["inputs"][0]["lagMinutes"] for r in fake.records if r["event"] == "started"] == [90.5]
    fake.input_at = c.started_at + timedelta(minutes=5)  # published after this run started
    assert w.run(dataclasses.replace(c, run_id="access_canary/r2"), fake)[0] == 0
    assert [r["inputs"][0]["lagMinutes"] for r in fake.records if r["event"] == "started"][-1] == 0.0
    assert w.lag(c.started_at, None) is None


def test_an_optional_input_with_no_snapshot_keeps_lag_null_and_reads_no_commit_time():
    fake = Fake()
    fake.seed()
    fake.inputs[INPUT.relation] = [{}]
    assert w.run(ctx(inputs=(dataclasses.replace(INPUT, required=False),)), fake)[0] == 0
    (started,) = [r for r in fake.records if r["event"] == "started"]
    assert started["inputs"][0]["snapshot"] is None and started["inputs"][0]["lagMinutes"] is None
    assert fake.commit_reads == []


def test_the_commit_time_is_the_snapshots_own_or_before_a_compaction_its_parents():
    def s(sid, ms, op="append", parent=None):
        return {"snapshot-id": sid, "timestamp-ms": ms, "summary": {"operation": op}, "parent-snapshot-id": parent}
    meta = {"snapshots": [s(1, 60_000), s(2, 120_000, "replace", 1), s(3, 180_000, "replace", 2), s(4, 240_000, "overwrite", 3),
                          s(5, 300_000, "replace", 99), s(6, 360_000, "replace", 7), s(7, 420_000, "replace", 6)]}
    at = lambda sid: w.commit_time(meta, sid, OUT).timestamp()  # noqa: E731
    assert w.commit_time(meta, 1, OUT) == datetime(1970, 1, 1, 0, 1, tzinfo=timezone.utc)
    assert (at(3), at(2), at(4)) == (60, 60, 240)  # compactions walk back to the data; a new write is its own
    assert at(5) == 300  # its parent expired: its own time
    assert at(6) in (360, 420)  # a parent cycle ends
    for bad in (None, {}, meta):
        with pytest.raises(w.Refused):
            w.commit_time(bad, 8, OUT)


def test_the_live_commit_time_reads_the_inputs_own_warehouse(monkeypatch):
    opened, read = [], []

    class Catalog:
        def __init__(self, uri, warehouse):
            opened.append(warehouse)

        def table(self, ns, t):
            read.append((ns, t))
            return {"snapshots": [{"snapshot-id": 7, "timestamp-ms": 1_000}]}
    monkeypatch.setattr(w.catalog_drift, "Catalog", Catalog)
    live = object.__new__(w.Live)
    live.ctx, live.uri, live.catalogs = ctx(), "http://lakekeeper:8181/catalog", {}
    for rel in ("lake.kraken_trades.bronze", "fx.ecb_fx__base.rates", "fx.ecb_fx__base.rates"):
        assert live.committed(rel, 7) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert opened == ["k2", "fx"] and read == [("kraken_trades", "bronze"), ("ecb_fx__base", "rates"), ("ecb_fx__base", "rates")]
    with pytest.raises(w.Refused):
        live.committed('fx.a"b.c', 7)


# ---- trust boundaries --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["a.b", 'a.b.c"; DROP TABLE x --', "A.b.c", "a.b.c$refs", "a.b." + "x" * 64, "a..c", None])
def test_a_relation_that_is_not_three_identifiers_is_refused(bad):
    with pytest.raises(w.Refused):
        w.relation(bad)


@pytest.mark.parametrize("bad", ["1; DROP TABLE x", "1.0", 1.5, True, -1, 2**63, "١٢", "", " 1", None])
def test_a_snapshot_id_that_is_not_an_integer_is_refused(bad):
    with pytest.raises(w.Refused):
        w.snap(bad)


PIPELINE = {"product": "governance.access-canary", "dagId": "access_canary", "inputs": [],
            "build": {"engine": "dbt", "selector": "access_canary", "writer": "dbt_access_canary",
                      "publisher": "pub_access_canary", "outputs": [OUT]}}
VIEWS = [{"relation": VIEW.relation, "base": VIEW.base, "columns": list(C), "owner": "pub_access_canary"}]


SCHEMAS = {"governance.access_canary__base.events": {"columns": [], "partitioning": ["region"], "sort": []}}


def ctx_from(run="access_canary/r1", pipeline=PIPELINE, views=VIEWS, schemas=SCHEMAS):
    return w.ctx_from(RELEASE, Path("/r"), "governance.access-canary", run, pipeline, views, {}, schemas)


def test_the_release_is_read_strictly():
    assert ctx_from().outputs == (w.Output(OUT, VIEW, ("region",), ()),)
    with pytest.raises(w.Refused):
        ctx_from(schemas={})
    for run in ("other/r1", "access_canary/a/b", "access_canary/r 1", "access_canary/"):
        with pytest.raises(w.Refused):
            ctx_from(run=run)
    elsewhere = {**PIPELINE, "build": {**PIPELINE["build"], "outputs": ["governance_build.other__base.events"]}}
    with pytest.raises(w.Refused):
        ctx_from(pipeline=elsewhere)
    with pytest.raises(w.Refused):
        ctx_from(views=[{**VIEWS[0], "owner": "pub_other"}])


def test_the_environment_must_name_this_runs_principals():
    good = {"K2_TRINO_HOST": "trino", "K2_TRINO_INTERNAL_PORT": "8443", "K2_TRINO_USER": "dbt_access_canary",
            "K2_PUB_USER": "pub_access_canary", "K2_RUNS_USER": "svc.runs", "K2_CATALOG_URI": "http://lakekeeper:8181/catalog",
            "K2_RELEASE": RELEASE}
    w.check_env(ctx(), good)
    for name, value in [("K2_TRINO_USER", "dbt_other"), ("K2_PUB_USER", "dbt_access_canary"), ("K2_RUNS_USER", "svc.platform"),
                        ("K2_RELEASE", "v9.9.9-0123456789ab"), ("K2_TRINO_HOST", "trino;rm"), ("K2_CATALOG_URI", "")]:
        with pytest.raises(w.Refused):
            w.check_env(ctx(), {**good, name: value})


def test_no_password_reaches_a_log_line_or_an_exception_message(tmp_path, monkeypatch, capsys):
    secret = "s3cr3t-Pa55word"
    for role in w.PASSWORDS:
        (tmp_path / role).write_text(secret + "\n")
    monkeypatch.setattr(w, "PASSWORDS", {role: str(tmp_path / role) for role in w.PASSWORDS})
    assert w.password("builder") == secret
    fake = Fake()

    def leaky(role, rel):
        if role == "runs":
            raise RuntimeError(f"login failed: user svc.runs password {secret}")
        return Fake.refs(fake, role, rel)
    fake.refs = leaky
    assert w.run(ctx(), fake)[0] == 2
    err = capsys.readouterr().err
    assert secret not in err and "***" in err


def test_the_xcom_is_the_last_stdout_line_on_every_exit_path(monkeypatch, capsys):
    def last():
        out = capsys.readouterr().out.strip().splitlines()
        return json.loads(out[-1])
    assert w.main(["--help"]) == 0 and capsys.readouterr().out.startswith("usage: k2-dbt")  # no run, no XCom
    assert w.main(["nonsense"]) == 3 and last() == {"exit": 3, "expire": []}
    assert w.main(["build", "--help"]) == 3 and last() == {"exit": 3, "expire": []}
    monkeypatch.setattr(w, "load", lambda *a: (_ for _ in ()).throw(FileNotFoundError("no release")))
    assert w.main(["build", "--release", RELEASE, "--product", "governance.access-canary", "--run-id", "access_canary/r1"]) == 3
    assert last() == {"exit": 3, "expire": []}
    monkeypatch.setattr(w, "load", lambda *a: ctx())
    monkeypatch.setattr(w, "Live", lambda c, env: None)
    for code in (0, 1, 2, 3, 4):
        expire = [{"table": OUT, "ids": [5]}] if code in (1, 2) else []
        monkeypatch.setattr(w, "run", lambda c, io, xcom, code=code, expire=expire: (code, expire))
        assert w.main(["build", "--release", RELEASE, "--product", "governance.access-canary", "--run-id", "x/y"]) == code
        assert last() == {"exit": code, "expire": expire}


# ---- k2-dbt docs (W3-11): the dbt docs site, published as <out>/current, with no warehouse ---------

FAKE_PASSWORD = "fake-Pa55word-in-the-environment"
FAKE_DBT = """#!{python}
import json, os, sys
argv = sys.argv[1:]
with open({calls!r}, "a") as f:
    f.write(json.dumps(argv) + "\\n")
target = argv[argv.index("--target-path") + 1]
os.makedirs(target, exist_ok=True)
seen = json.dumps({{"argv": argv, "env": dict(os.environ)}})  # a manifest that leaked its whole environment
step = "parse" if argv[0] == "parse" else "docs"
if os.path.exists({fail!r} + step):
    sys.exit(2)
if step == "parse":
    open(os.path.join(target, "manifest.json"), "w").write(seen)
    open(os.path.join(target, "partial_parse.msgpack"), "w").write("not published")
else:
    open(os.path.join(target, "index.html"), "w").write("<html>" + seen + "</html>")
    open(os.path.join(target, "catalog.json"), "w").write(seen)
"""


@pytest.fixture
def docs_env(tmp_path, monkeypatch):
    """A release with a compiled dbt project, a fake dbt on PATH, and an environment holding a password."""
    (tmp_path / "releases" / RELEASE / "compiled" / "dbt").mkdir(parents=True)
    (tmp_path / "releases" / RELEASE / "compiled" / "dbt" / "dbt_project.yml").write_text("name: k2\n")
    monkeypatch.setattr(w, "RELEASES", tmp_path / "releases")
    monkeypatch.setattr(w, "WORK", tmp_path / "work")
    monkeypatch.setattr(w, "password", lambda role: pytest.fail(f"k2-dbt docs read the {role} password"))
    (tmp_path / "bin").mkdir()
    dbt = tmp_path / "bin" / "dbt"
    dbt.write_text(FAKE_DBT.format(python=sys.executable, calls=str(tmp_path / "calls"), fail=str(tmp_path / "fail-")))
    dbt.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("DBT_ENV_SECRET_K2_TRINO_PASSWORD", FAKE_PASSWORD)
    monkeypatch.setenv("K2_TRINO_PASSWORD", FAKE_PASSWORD)
    return tmp_path


def calls(root):
    return [json.loads(line) for line in (root / "calls").read_text().splitlines()]


def test_docs_publishes_the_three_files_behind_current_and_no_secret(docs_env):
    out = docs_env / "site" / "dbt-docs"
    assert w.main(["docs", "--release", RELEASE, "--out", str(out)]) == 0
    tree = out / os.readlink(out / "current")
    assert os.readlink(out / "current").startswith("tree-") and tree.parent == out  # relative: any mount point
    assert sorted(p.name for p in tree.iterdir()) == ["catalog.json", "index.html", "manifest.json"]
    assert oct(tree.stat().st_mode & 0o777) == "0o755" and all(p.stat().st_mode & 0o777 == 0o644 for p in tree.iterdir())
    for p in tree.iterdir():
        assert FAKE_PASSWORD not in p.read_text(), p.name
    project = str(docs_env / "releases" / RELEASE / "compiled" / "dbt")
    (parse, generate) = calls(docs_env)
    assert parse[0] == "parse" and generate[:4] == ["docs", "generate", "--no-compile", "--empty-catalog"]
    for argv in (parse, generate):
        assert argv[argv.index("--project-dir") + 1] == project
        assert argv[argv.index("--profiles-dir") + 1] == "/opt/k2/dbt"
        assert argv[argv.index("--target-path") + 1].startswith(str(docs_env / "work"))
    seen = json.loads((tree / "manifest.json").read_text())["env"]
    assert seen["DBT_ENV_SECRET_K2_TRINO_PASSWORD"] == "unused" and "K2_TRINO_PASSWORD" not in seen
    assert seen["K2_TRINO_HOST"].endswith(".invalid")  # RFC 6761: never resolves, so no Trino is reached
    assert seen["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"


def test_docs_keeps_the_tree_before_and_removes_older_ones(docs_env):
    out = docs_env / "site"
    trees = []
    for _ in range(3):
        assert w.main(["docs", "--release", RELEASE, "--out", str(out)]) == 0
        trees.append(os.readlink(out / "current"))
    assert len(set(trees)) == 3
    assert sorted(p.name for p in out.iterdir()) == sorted(["current", trees[1], trees[2]])


@pytest.mark.parametrize("step", ["parse", "docs"])
def test_a_failed_dbt_step_publishes_nothing_and_the_site_stays(docs_env, step):
    out = docs_env / "site"
    assert w.main(["docs", "--release", RELEASE, "--out", str(out)]) == 0
    before = sorted(p.name for p in out.iterdir()), os.readlink(out / "current")
    (docs_env / f"fail-{step}").touch()
    assert w.main(["docs", "--release", RELEASE, "--out", str(out)]) == 1
    assert (sorted(p.name for p in out.iterdir()), os.readlink(out / "current")) == before


def test_docs_refuses_a_bad_release_a_missing_project_or_a_current_that_is_no_link(docs_env, capsys):
    out = docs_env / "site"
    assert w.main(["docs", "--release", "../etc", "--out", str(out)]) == 1
    assert w.main(["docs", "--release", "v2.0.0-0123456789ab", "--out", str(out)]) == 1
    assert not (docs_env / "calls").exists()  # dbt never ran
    (out / "current").mkdir(parents=True)
    assert w.main(["docs", "--release", RELEASE, "--out", str(out)]) == 1
    assert w.main(["docs", "--release", RELEASE]) == 2
    assert "is not a symlink" in capsys.readouterr().err
