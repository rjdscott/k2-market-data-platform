"""The proofs behind access-verify, the tested-data marker and the catalog drift check (step S20).
tests/access/prove.sh runs each subcommand inside a staged throwaway lake's tools container
(scripts/lib/throwaway-lake.sh py); each changes the lake one way, undoes a change, or runs a
control and asserts how it ends, exiting 1 when the control did not behave.

    prove.py negative        one reader rule loosened in the running copy of the rules: access-verify
                             must fail exactly the read rows of that role's members; then restored
    prove.py live            access-verify's live scope against this lake (K2_SECRETS laid out from the
                             lake's passwords): only the planted-view and drift rows skip; with one
                             canary reader's password file gone, exactly that reader's rows fail
    prove.py plant <kind>    view: a definer view by REST, an undeclared name, the publisher's name
                                   forged as its owner, SQL over the base's main
                             graft: another table's snapshot added to the base by REST, the tag moved to it
                             table: a table by REST in the reader namespace
                             wider: the reader view replaced, as its publisher, by one over main
                             alias: the reader view replaced, as its publisher, with a masked column
                                    also served under a second name (the review of S12, M1)
                             base-view: the base dropped and a view of constants created at its name, as
                                    its builder, through Trino (the review of S12, C1); no undo
    prove.py unplant <kind>

The product under attack is the release's first dbt or code product, its first output.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "/src/scripts")
import access_verify as av  # noqa: E402
from k2gen import model  # noqa: E402
from k2gen.emit import lake, trino_rules  # noqa: E402
from k2gen.model import quote_ident, quote_relation  # noqa: E402
from verify import catalog_drift as cd  # noqa: E402

ENV_FILE = Path("/work/env.json")
TRINO, ENV = av.connect(ENV_FILE)
RELEASE = Path(ENV["release"])
M = av.load(RELEASE)
P = next(p for p in sorted(M.products, key=lambda p: p.id) if any(t.build.kind in ("dbt", "code") for t in p.tables))
T = next(t for t in P.tables if t.build.kind in ("dbt", "code"))
VIEW = next(model.View(v["relation"], v["base"], tuple(v["columns"]), v["owner"])
            for v in json.loads((RELEASE / model.VIEWS).read_text())["views"] if v["relation"] == model.reader_relation(T))
CAT = cd.Catalog(f"{ENV['lakekeeper']}/catalog", model.warehouse(P.domain))
NS, BASE_NS = model.reader_namespace(P.name), model.base_namespace(P.name)
VIEW_BUILD = quote_relation(model.view_build_relation(P.domain, P.name, T.name))
MASKED = next((role, pol.mask[0]) for role, pol in sorted(T.policies.items()) if pol.mask)  # (role, a column it masks)


def say(line: str) -> None:
    print(f"prove: {line}", flush=True)


def write_in_place(path: Path, data: bytes) -> None:
    """Rewritten in place, never renamed over: a container keeps reading a renamed file's old inode.
    prove.sh makes the file writable from the host first: Docker Desktop's file sharing refuses a
    chmod from the container on a file the host copied into a read-only directory."""
    with open(path, "r+b") as f:
        f.write(data)
        f.truncate()


def wait(predicate: Any, what: str, limit: float = 30) -> float:
    t0 = time.monotonic()
    while not predicate():
        if time.monotonic() - t0 > limit:
            raise SystemExit(f"prove: FAIL: {what} not seen in {limit} s")
        time.sleep(0.2)
    return time.monotonic() - t0


def run_access_verify(scope: str, out: str) -> tuple[int, dict[int, tuple[str, str]]]:
    code = av.run(RELEASE, scope, ENV_FILE if scope == "release" else None, Path(out))
    return code, {r["n"]: (r["verdict"], r["reason"]) for r in json.loads(Path(out).read_text())["rows"] if r["n"]}


def negative() -> int:
    role, column = MASKED
    members = {g.principal for g in P.grants if g.role == role}
    path = RELEASE / trino_rules.RULES
    original = path.read_bytes()
    rules = json.loads(original)
    (rule,) = [r for r in rules["tables"] if r.get("group") == trino_rules.exact([model.group(P.name, role)])]
    rule.pop("filter", None)
    rule.pop("columns", None)
    member = sorted(members)[0]
    shown = f"SELECT count(*) FROM {quote_relation(VIEW.relation)} WHERE {quote_ident(column)} IS NOT NULL"
    write_in_place(path, (json.dumps(rules, indent=2) + "\n").encode())
    try:
        say(f"loosened: the {role} rule on {VIEW.relation} without its filter and masks, live after "
            f"{wait(lambda: TRINO.ok(member, shown)[0][0] > 0, 'the loosened rule'):.1f} s")
        code, rows = run_access_verify("release", "/work/negative.json")
    finally:
        write_in_place(path, original)
        say(f"restored, live after {wait(lambda: TRINO.ok(member, shown)[0][0] == 0, 'the restored rule'):.1f} s")
    failed = {n for n, (v, _) in rows.items() if v == "FAIL"}
    want = {n for n, e in enumerate(M.expectations, 1) if isinstance(e, model.TableExpectation) and e.principal in members}
    say(f"negative control: access-verify exit {code}; failed rows {sorted(failed)}; the {role} read rows are {sorted(want)}")
    return 0 if code == 1 and failed == want and want else 1


def live() -> int:
    """The live scope's choices, on this lake: the platform's and the canary's rows run, nothing else."""
    sec = Path("/work/live-secrets")
    shutil.rmtree(sec, ignore_errors=True)
    (sec / "principals").mkdir(parents=True)
    (sec / "tls").mkdir()
    shutil.copy(ENV["ca"], sec / "tls/ca.pem")
    canary = {g.principal for p in M.products if p.id == av.CANARY_PRODUCT for g in p.grants}
    for u in trino_rules.known_principals(M):
        if u.startswith(("svc.", "dbt_", "pub_")) or u in canary:
            f = sec / "principals" / f"{u}.password"
            f.write_text(TRINO.password(u) + "\n")
            f.chmod(0o600)
    host, port = ENV["trino"].removeprefix("https://").split(":")
    os.environ.update(K2_SECRETS=str(sec), K2_TRINO_HOST=host, K2_TRINO_PORT=port)
    os.environ.pop("K2_TRINO_CA", None)
    os.environ.pop("K2_CATALOG_URI", None)
    code, rows = run_access_verify("live", "/work/live.json")
    skipped = {n for n, (v, _) in rows.items() if v == "SKIP"}
    want = {n for n, e in enumerate(M.expectations, 1) if av.planted(e, M) or isinstance(e, model.DriftExpectation)}
    say(f"live scope: exit {code}; skipped rows {sorted(skipped)}, the planted-view and drift rows are {sorted(want)}")
    ok = code == 0 and skipped == want
    gone = sorted(canary)[-1]
    (sec / "principals" / f"{gone}.password").unlink()
    code, rows = run_access_verify("live", "/work/live-missing.json")
    failed = {n for n, (v, _) in rows.items() if v == "FAIL"}
    theirs = {n for n, e in enumerate(M.expectations, 1) if getattr(e, "principal", None) == gone}
    say(f"live scope without {gone}'s password file: exit {code}; failed rows {sorted(failed)}; its rows are {sorted(theirs)}")
    shutil.rmtree(sec)
    return 0 if ok and code == 1 and failed == theirs else 1


def constant(c: model.Column) -> str:
    """A forged value of c's type that every reader policy lets through."""
    if c.physical_type in ("bigint", "integer"):
        return f"CAST(9999 AS {c.physical_type})"
    if c.physical_type == "varchar":
        return "'zz-forged'"
    if c.physical_type.startswith("timestamp"):
        return f"CAST(current_timestamp - INTERVAL '1' DAY AS {c.physical_type})"
    return f"CAST(NULL AS {c.physical_type})"


def plant(kind: str) -> int:
    if kind == "view":
        av.plant(ENV, M, P, "foreign_view")
    elif kind == "graft":
        dst = CAT.table(BASE_NS, T.name)
        src_name = next(n for n in sorted(CAT.tables(BASE_NS)) if n != T.name)
        src = CAT.table(BASE_NS, src_name)
        snap = dict(next(s for s in src["snapshots"] if s["snapshot-id"] == src["current-snapshot-id"]))
        snap["parent-snapshot-id"] = dst["current-snapshot-id"]
        snap["schema-id"] = dst["current-schema-id"]
        snap["sequence-number"] = dst["last-sequence-number"] + 1
        old = dst["refs"][model.AUDITED_TAG]["snapshot-id"]
        st, doc = cd.call("POST", f"{CAT.base}/namespaces/{BASE_NS}/tables/{T.name}", {
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": model.AUDITED_TAG, "snapshot-id": old}],
            "updates": [{"action": "add-snapshot", "snapshot": snap},
                        {"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": snap["snapshot-id"]}]})
        assert st == 200, f"graft: HTTP {st} {doc}"
        Path("/work/graft.json").write_text(json.dumps({"tag": old, "grafted": snap["snapshot-id"]}))
        say(f"grafted {src_name}'s snapshot {snap['snapshot-id']} into {T.name} as its {model.AUDITED_TAG} tag (was {old})")
    elif kind == "table":
        st, doc = cd.call("POST", f"{CAT.base}/namespaces/{NS}/tables", {"name": "planted", "schema": {
            "type": "struct", "schema-id": 0, "fields": [{"id": 1, "name": "x", "required": False, "type": "long"}]}})
        assert st == 200, f"table: HTTP {st} {doc}"
    elif kind == "wider":
        cols = ", ".join(quote_ident(c) for c in VIEW.columns)
        TRINO.ok(VIEW.owner, f"CREATE OR REPLACE VIEW {VIEW_BUILD} SECURITY DEFINER AS SELECT {cols} FROM {quote_relation(VIEW.base)}")
    elif kind == "alias":
        role, column = MASKED
        cols = ", ".join([*(quote_ident(c) for c in VIEW.columns), f"{quote_ident(column)} AS {quote_ident(column + '_unmasked')}"])
        TRINO.ok(VIEW.owner, f"CREATE OR REPLACE VIEW {VIEW_BUILD} SECURITY DEFINER AS SELECT {cols} FROM "
                             f"{quote_relation(VIEW.base)} FOR VERSION AS OF {model.sql_literal(model.AUDITED_TAG)}")
        member = next(g.principal for g in P.grants if g.role == role)
        n = TRINO.ok(member, f"SELECT count(*) FROM {quote_relation(VIEW.relation)} WHERE {quote_ident(column + '_unmasked')} IS NOT NULL")
        say(f"aliased: {member} ({role}, {column} masked) now reads {n[0][0]} row(s) of {column}_unmasked unmasked")
    elif kind == "base-view":
        build = quote_relation(model.build_relation(P.domain, P.name, T.name))
        builder = model.builder(P.name)
        TRINO.ok(builder, f"DROP TABLE {build}")
        values = ", ".join(f"{constant(c)} AS {quote_ident(c.name)}" for c in T.columns)
        TRINO.ok(builder, f"CREATE VIEW {build} SECURITY INVOKER AS SELECT {values}")
        member = sorted(g.principal for g in P.grants)[0]
        a = TRINO.run(member, f"SELECT * FROM {quote_relation(VIEW.relation)}")
        say(f"base replaced by a view of constants as {builder}; {member} now reads: {a.says()} {a.rows[:2]}")
    else:
        raise SystemExit(f"prove: no plant {kind}")
    say(f"planted {kind}")
    return 0


def unplant(kind: str) -> int:
    if kind == "view":
        st, _ = cd.call("DELETE", f"{CAT.base}/namespaces/{NS}/views/foreign_view")
        assert st in (200, 204), f"unplant view: HTTP {st}"
    elif kind == "graft":
        g = json.loads(Path("/work/graft.json").read_text())
        st, doc = cd.call("POST", f"{CAT.base}/namespaces/{BASE_NS}/tables/{T.name}", {
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": model.AUDITED_TAG, "snapshot-id": g["grafted"]}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": g["tag"]}]})
        assert st == 200, f"unplant graft: HTTP {st} {doc}"
    elif kind == "table":
        st, _ = cd.call("DELETE", f"{CAT.base}/namespaces/{NS}/tables/planted?purgeRequested=true")
        assert st in (200, 204), f"unplant table: HTTP {st}"
    elif kind in ("wider", "alias"):
        TRINO.ok(VIEW.owner, lake.create_view(VIEW))
    else:
        raise SystemExit(f"prove: {kind} has no undo")
    say(f"unplanted {kind}")
    return 0


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    sys.exit({"negative": negative, "live": live, "plant": plant, "unplant": unplant}[cmd](*args))
