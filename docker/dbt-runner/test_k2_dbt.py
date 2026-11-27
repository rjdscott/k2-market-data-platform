"""The dbt wrapper's decisions against an in-memory lake: no Trino, no catalog, no dbt.

Fake keeps tables (snapshots with their columns and whether their tests passed, main, the tag),
reader views, run records and the statements each role ran, and checks after every change the
security property of step 6: every view reads a tag on a snapshot whose tests passed (no "leak")
and selects only columns that snapshot holds with the view's types (no "broken" read).
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "platform" / "k2gen"), str(ROOT / "scripts" / "verify")]
import k2_dbt as w  # noqa: E402
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
                 "dbt_access_canary", "pub_access_canary", tuple(w.Output(r, v) for r, v in outputs), tuple(inputs),
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

    def _snap(self, rel, cols, passed):
        sid = next(self.ids)
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
        elif m := re.fullmatch(rf"CREATE OR REPLACE TABLE {q} AS SELECT \* FROM {q} FOR VERSION AS OF BIGINT '(\d+)'", stmt):
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

    def dbt(self, argv):
        self.dbt_calls.append(argv)
        for rel in self.view_of:  # every output is rebuilt; its tests pass only on a passing build
            self._snap(rel, self.build.get(rel) or (self.tables[rel]["schema"] if rel in self.tables else V1), self.dbt_code == 0)
        self._snap("governance_build.access_canary__base.dq_test_results", {"run_id": "varchar"}, True)  # the results hook
        self.check()
        if self.after_dbt:
            self.after_dbt(self)
        return self.dbt_code, {"metadata": {"invocation_id": UUID}, "results": [{"status": self.dbt_status}]}

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
    code, expire = w.run(ctx(), fake)
    assert (code, expire) == (0, [])
    (argv,) = fake.dbt_calls
    assert argv[:4] == ["dbt", "build", "--selector", "access_canary"]
    assert json.loads(argv[argv.index("--vars") + 1]) == {
        "k2_inputs": {"ecb_fx/rates": {"snapshot": 7}}, "k2_run_id": "access_canary/r1", "k2_release": RELEASE}
    started = [r for r in fake.records if r["event"] == "started"]
    assert [(r["table_name"], r["snapshot_before"]) for r in started] == [(OUT, s0)]
    assert started[0]["inputs"] == [{"contractId": INPUT.contract, "relation": INPUT.relation, "snapshot": 7,
                                     "state": "active", "lagMinutes": None}]


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
    env = w.dbt_env(environ, "s3cr3t-builder")
    assert set(env) == {"PATH", "HOME", "LANG", "K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_TRINO_USER",
                        "DBT_ENV_SECRET_K2_TRINO_PASSWORD", "DBT_SEND_ANONYMOUS_USAGE_STATS", "DBT_USE_COLORS"}
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
    failed = max(fake.tables[OUT]["snaps"])
    assert code == 1 and fake.tables[OUT]["main"] == s0 and fake.tables[OUT]["tag"] == s0
    assert ("builder", f'ALTER TABLE {k2.quote_relation(OUT)} EXECUTE rollback_to_snapshot({s0})') in fake.statements
    assert expire == [{"table": OUT, "ids": [failed]}]
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


def ctx_from(run="access_canary/r1", pipeline=PIPELINE, views=VIEWS):
    return w.ctx_from(RELEASE, Path("/r"), "governance.access-canary", run, pipeline, views, {})


def test_the_release_is_read_strictly():
    assert ctx_from().outputs == (w.Output(OUT, VIEW),)
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
    assert w.main(["nonsense"]) == 3 and last() == {"exit": 3, "expire": []}
    monkeypatch.setattr(w, "load", lambda *a: (_ for _ in ()).throw(FileNotFoundError("no release")))
    assert w.main(["build", "--release", RELEASE, "--product", "governance.access-canary", "--run-id", "access_canary/r1"]) == 3
    assert last() == {"exit": 3, "expire": []}
    monkeypatch.setattr(w, "load", lambda *a: ctx())
    monkeypatch.setattr(w, "Live", lambda c, env: None)
    for code in (0, 1, 2, 3, 4):
        expire = [{"table": OUT, "ids": [5]}] if code in (1, 2) else []
        monkeypatch.setattr(w, "run", lambda c, io, code=code, expire=expire: (code, expire))
        assert w.main(["build", "--release", RELEASE, "--product", "governance.access-canary", "--run-id", "x/y"]) == code
        assert last() == {"exit": code, "expire": expire}
