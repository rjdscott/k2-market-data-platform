"""The catalog site: one static HTML page per product of a release, plus an index (plan 003, "The
catalog site, first version"; W3-9, W3-10).

    python k2site.py --release /k2/releases/<id> --out /site [--no-facts] [--strict]

Read-only and generated: the release's compiled/catalog/ documents (catalog-product.schema.json)
and the run-time facts svc.catalog may read through Trino (read_facts): each output's tested
snapshot from its base's `t$refs`, when the run records first saw that snapshot, each product's
newest run, and the newest dbt invocation's test counts from governance.quality_results. No
JavaScript, no external asset, no credential: every value is HTML-escaped and only an https: or
site-relative URL becomes a link.

Each page section is one function (`_sec_*`), so phase 006 adds search, lineage and quality
history beside them. Rendering is pure (`render`): the same release, facts and generated-at time
give the same bytes. `publish` writes a fresh tree under <out>, then renames a symlink
<out>/current onto it, so a reader sees the old site or the new one and never half of one; a
mounted volume root cannot itself be renamed, which is why the swap is one level down. The
server serves <out>/current/.

Ships in the release (k2gen model.PLATFORM_CODE), so the generator always matches the documents
it renders: /k2/releases/<id>/platform/platform/catalog-site/k2site.py.
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runs"))  # beside it in a release too
import k2runs  # noqa: E402

USER = "svc.catalog"
PASSWORD_FILE = "/run/k2-secrets/catalog.password"  # k2gen.model.password_mount("svc.catalog")
CA_FILE = "/run/k2-secrets/tls/ca.pem"  # as k2runs.connect
TIMEOUT_S = 30  # each query: Trino's query_max_run_time and the client's HTTP timeout
CATALOG_INDEX = "compiled/catalog/index.json"  # k2gen.model.CATALOG_INDEX
VIEWS = "compiled/trino/views.json"  # k2gen.model.VIEWS: each reader view's base
RUNS = '"governance"."run_records"."run_record"'  # svc.catalog's exact table rule in rules.json
TESTS = '"governance"."quality_results"."test_results"'  # likewise; the reader view
RUN_WINDOW_DAYS = 35  # ponytail: runs older than this are not read; widen when a product runs less than monthly
NOT_YET = {"TABLE_NOT_FOUND", "SCHEMA_NOT_FOUND"}  # a base before its first build, as k2-api reads it
REF_TYPE = {"audited": "TAG", "main": "BRANCH"}  # k2gen.model.tested_ref, as `$refs` types it
POLICY_KINDS = ("delay", "mask", "rowScope")  # k2-v2.schema.json#/$defs/rolePolicy; a new one fails the tests
CURRENT = "current"  # the symlink the server reads through
TREE = "tree-"  # each generated tree's name prefix
NAME = re.compile(r"[a-z][a-z0-9_]*")
RELATIVE = re.compile(r"(\.\./)*[a-z0-9][a-z0-9._/-]*")  # a site-relative path this module writes; no scheme

# --- run-time facts ------------------------------------------------------------------------------


class FactsUnavailable(Exception):
    """Trino could not be asked; the message is an error name or type, never a credential."""


def connect() -> Any:
    """An HTTPS connection as svc.catalog, with k2runs' environment names and file paths."""
    import trino  # lazily: rendering needs only the standard library

    try:
        with open(PASSWORD_FILE) as f:
            password = f.read().strip()
        host, port = os.environ["K2_TRINO_HOST"], int(os.environ["K2_TRINO_INTERNAL_PORT"])
    except (OSError, KeyError, ValueError) as e:
        raise FactsUnavailable(f"no connection settings ({type(e).__name__})") from None
    return trino.dbapi.connect(
        host=host, port=port, user=USER, auth=trino.auth.BasicAuthentication(USER, password),
        http_scheme="https", verify=CA_FILE, source="k2site", timezone="UTC", request_timeout=TIMEOUT_S,
        legacy_prepared_statements=False, session_properties={"query_max_run_time": f"{TIMEOUT_S}s"})


def _rows(conn: Any, sql: str, params: list[Any]) -> list[list[Any]] | None:
    """The rows, or None for a table not built yet; any other failure is FactsUnavailable."""
    import requests
    import trino

    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [list(r) for r in cur.fetchall()]
    except trino.exceptions.TrinoUserError as e:
        if e.error_name in NOT_YET:
            return None
        raise FactsUnavailable(e.error_name) from None
    except (trino.exceptions.Error, trino.exceptions.HttpError, requests.RequestException, OSError) as e:
        raise FactsUnavailable(type(e).__name__) from None


def _quoted(relation: str, suffix: str = "") -> str:
    """`a.b.c` as `"a"."b"."c"`; with `$refs`, that table's metadata table. Names come from the
    release and are held to the catalog's grammar, since a name cannot be a bound value."""
    parts = relation.split(".")
    if len(parts) != 3 or not all(NAME.fullmatch(p) for p in parts):
        raise ValueError(f"{relation!r} is not <catalog>.<schema>.<table>")
    return f'"{parts[0]}"."{parts[1]}"."{parts[2]}{suffix}"'


def load(release: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    index = json.loads((release / CATALOG_INDEX).read_text())
    docs = [json.loads((release / f"compiled/catalog/products/{e['id']}.json").read_text()) for e in index["products"]]
    return index, docs


def read_facts(conn: Any, release: Path) -> dict[str, Any]:
    """Every fact the pages show, read as svc.catalog with bound values. A product never built has
    no entry in `outputs` with a snapshot; Trino unreachable raises FactsUnavailable."""
    _, docs = load(release)
    bases = {v["relation"]: v["base"] for v in json.loads((release / VIEWS).read_text())["views"]}
    ids = [d["id"] for d in docs]
    products: dict[str, dict[str, Any]] = {pid: {"outputs": {}, "lastRun": None, "tests": None, "state": None, "gaps": []} for pid in ids}
    for doc in docs:
        for out in doc["outputs"]:
            if not out["relation"] or not out["testedRef"]:
                continue
            base = bases[out["relation"]] if out["build"] in ("dbt", "code") else out["relation"]
            ref = out["testedRef"]
            got = _rows(conn, f'SELECT "snapshot_id" FROM {_quoted(base, "$refs")} WHERE "name" = ? AND "type" = ?',
                        [ref, REF_TYPE[ref]])
            snapshot = got[0][0] if got else None
            at = None
            if snapshot is not None:
                seen = _rows(conn, f'SELECT min("recorded_at") FROM {RUNS} WHERE "product_id" = ? AND "snapshot_after" = ? '
                                   "AND \"event\" IN ('audited', 'passed')", [doc["id"], snapshot])
                at = seen[0][0] if seen else None
            products[doc["id"]]["outputs"][out["contractId"]] = {"snapshot": snapshot, "publishedAt": at}
    marks = ", ".join("?" * len(ids))
    runs = _rows(conn, f"""WITH runs AS (
  SELECT "product_id", "run_id", max("recorded_at") AS at,
         count_if("event" IN ('failed', 'rolled_back')) = 0 AS ok,
         coalesce(array_join(array_sort(array_distinct(
           array_agg("task") FILTER (WHERE "event" IN ('failed', 'rolled_back')))), ', '), '') AS failed
  FROM {RUNS}
  WHERE "product_id" IN ({marks}) AND "recorded_at" > current_timestamp - INTERVAL '{RUN_WINDOW_DAYS}' DAY
  GROUP BY "product_id", "run_id")
SELECT "product_id", max_by("run_id", at), max(at), max_by(ok, at), max_by(failed, at), max(at) FILTER (WHERE ok)
FROM runs GROUP BY "product_id\"""", ids)
    for pid, run_id, at, ok, failed, last_ok in runs or []:
        products[pid]["lastRun"] = {"runId": run_id, "at": at, "ok": ok, "failedTasks": failed, "lastOkAt": last_ok}
    recs = _rows(conn, f"""SELECT "product_id", "run_id", "task", "event", "recorded_at", "inputs", "detail" FROM {RUNS}
WHERE "product_id" IN ({marks}) AND "recorded_at" > current_timestamp - INTERVAL '{RUN_WINDOW_DAYS}' DAY
AND ("task" = 'build' OR "event" = 'offset_gap')""", ids)  # ponytail: every build row of the window; narrow to the newest run when a product builds often
    by: dict[str, list[dict[str, Any]]] = {}
    for pid, *cells in recs or []:
        by.setdefault(pid, []).append(dict(zip(("run_id", "task", "event", "recorded_at", "inputs", "detail"), cells)))
    for pid, rows in by.items():
        products[pid]["gaps"] = k2runs.offset_gaps(rows)
        if any(r["task"] == "build" for r in rows):
            products[pid]["state"] = k2runs.state(rows)
    tests = _rows(conn, f"""WITH newest AS (
  SELECT "product_id", max_by("dbt_invocation_id", "run_started_at") AS inv, max("run_started_at") AS at
  FROM {TESTS} WHERE "product_id" IN ({marks}) GROUP BY "product_id")
SELECT t."product_id", n.inv, n.at, t."status", count(*)
FROM {TESTS} t JOIN newest n ON t."product_id" = n."product_id" AND t."dbt_invocation_id" = n.inv
GROUP BY t."product_id", n.inv, n.at, t."status\"""", ids)
    for pid, inv, at, status, n in tests or []:
        entry = products[pid]["tests"] or {"invocation": inv, "at": at, "counts": {}}
        entry["counts"][status] = n
        products[pid]["tests"] = entry
    return {"available": True, "products": products}


# --- words ---------------------------------------------------------------------------------------

e = html.escape  # quote=True: safe in text and in a double-quoted attribute


def when(t: datetime | None) -> str:
    return "never" if t is None else t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def listed(items: list[Any], conjunction: str = "and") -> str:
    words = [str(i) for i in items]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {conjunction} {words[-1]}"


def safe_url(url: str) -> str | None:
    """An https: URL, a path on this server, or a relative path this module made; else None."""
    if url.startswith("https://") or (url.startswith("/") and not url.startswith("//")) or RELATIVE.fullmatch(url):
        return url
    return None


def link(url: str, text: str) -> str:
    """A link when the URL is safe; otherwise the text and the URL as plain text, never an href."""
    ok = safe_url(url)
    return f'<a href="{e(ok)}">{e(text)}</a>' if ok else f"{e(text)} (<code>{e(url)}</code>, not linked)"


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        span, _, step = part.partition("/")
        if span == "*":
            a, b = lo, hi
        elif "-" in span:
            a, b = (int(x) for x in span.split("-"))
        else:
            a = b = int(span)
            b = hi if step else b
        out.update(range(a, b + 1, int(step or 1)))
    return out


def fires_before(cron: str, now: datetime, n: int = 2) -> list[datetime]:
    """The newest `n` times at or before `now` the five-field UTC cron fires, newest first."""
    mi, ho, dom, mon, dow = cron.split()
    minutes, hours = sorted(_field(mi, 0, 59), reverse=True), sorted(_field(ho, 0, 23), reverse=True)
    days, months, weekdays = _field(dom, 1, 31), _field(mon, 1, 12), {d % 7 for d in _field(dow, 0, 7)}
    found: list[datetime] = []
    day: date = now.date()
    for _ in range(400):
        on_dom, on_dow = day.day in days, (day.weekday() + 1) % 7 in weekdays
        matches = (on_dom and on_dow) if "*" in (dom, dow) else (on_dom or on_dow)  # cron's OR rule
        if day.month in months and matches:
            for h in hours:
                for m in minutes:
                    t = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
                    if t <= now:
                        found.append(t)
                        if len(found) == n:
                            return found
        day -= timedelta(days=1)
    return found


def freshness(doc: dict[str, Any], pf: dict[str, Any] | None, now: datetime) -> str:
    """The product against its schedule, from its newest run with no failed step."""
    if doc["status"] == "paused":
        return "Paused: nothing rebuilds it, so its data is stale by design."
    if doc["status"] == "retired":
        return "Retired: nothing rebuilds it."
    cron = doc.get("periodicity")
    if not cron:
        return "Nothing schedules this product, so freshness is not measured."
    last_ok = ((pf or {}).get("lastRun") or {}).get("lastOkAt")
    if last_ok is None:
        return f"No successful run recorded in the last {RUN_WINDOW_DAYS} days (schedule <code>{e(cron)}</code>, UTC)."
    fires = fires_before(cron, now)
    if not fires or last_ok >= fires[0]:
        return f"Current: the newest successful run finished {e(when(last_ok))}, after the run due {e(when(fires[0] if fires else None))}."
    if len(fires) > 1 and last_ok >= fires[1]:
        return (f"Due: the run due {e(when(fires[0]))} has not finished successfully yet; "
                f"the one before it finished {e(when(last_ok))}.")
    return (f"Late: the newest successful run finished {e(when(last_ok))}, before the runs due "
            f"{e(when(fires[1]))} and {e(when(fires[0]))}.")


def tests_badge(pf: dict[str, Any] | None, available: bool) -> str:
    """Pass or fail in words, not only in colour."""
    if not available:
        return '<span class="badge none">tests: unknown</span>'
    counts = ((pf or {}).get("tests") or {}).get("counts") or {}
    bad = sum(n for s, n in counts.items() if s in ("fail", "error"))
    if bad:
        return f'<span class="badge fail">tests failing: {bad}</span>'
    if counts:
        return '<span class="badge pass">tests passing</span>'
    return '<span class="badge none">no test results</span>'


def state_badge(st: str | None) -> str:
    """The product's state in words, inputs named; None when it has never built."""
    if st is None:
        return '<span class="badge none">not built yet</span>'
    return f'<span class="badge {"pass" if st == "complete" else "fail"}">{e(st)}</span>'


def _gaps(gaps: list[dict[str, Any]] | None) -> str:
    """Offsets an accept-data-loss run gave up, newest first."""
    if not gaps:
        return ""
    items = []
    for g in gaps:
        spans = [f"partition {p['partition']} offsets {p['fromOffset']} to {p['toOffset']}" for p in g["partitions"]]
        items.append(f"<li>{e(when(g['at']))}: {e(listed(spans))}; reason: {e(g['reason'])}</li>")
    return "<h3>Offset gaps accepted</h3>\n<ul>\n" + "\n".join(items) + "\n</ul>\n"


def newest_publish(pf: dict[str, Any] | None) -> datetime | None:
    times = [o["publishedAt"] for o in ((pf or {}).get("outputs") or {}).values() if o.get("publishedAt")]
    return max(times) if times else None


def policy_words(policies: dict[str, Any], problems: list[str], where: str) -> list[str]:
    """What a role's reader sees, one clause per policy kind the schema allows; an unknown kind is
    a visible line and a problem, never dropped."""
    clauses = []
    if "rowScope" in policies:
        s = policies["rowScope"]
        clauses.append(f"only rows whose <code>{e(s['column'])}</code> is {e(listed(s['values'], 'or'))}")
    if "delay" in policies:
        d = policies["delay"]
        clauses.append(f"only rows whose <code>{e(d['column'])}</code> is at least {e(str(d['minutes']))} "
                       f"minute{'s' * (d['minutes'] != 1)} old")
    if "mask" in policies:
        cols = policies["mask"]
        clauses.append(f"column{'s' * (len(cols) != 1)} {listed([f'<code>{e(c)}</code>' for c in cols])} "
                       f"read{'s' * (len(cols) == 1)} as null")
    for kind in sorted(set(policies) - set(POLICY_KINDS)):
        problems.append(f"{where}: unrendered policy {kind}")
        clauses.append(f'<span class="unrendered">unrendered policy: {e(kind)}</span>')
    return clauses


def first_sentence(text: str, cap: int = 160) -> str:
    head = text.split(". ", 1)[0].rstrip(".") + "."
    return head if len(head) <= cap else head[:cap].rsplit(" ", 1)[0] + "..."


# --- page sections -------------------------------------------------------------------------------

CSS = """:root{color-scheme:light dark;--bg:#fff;--fg:#1b1f24;--muted:#57606a;--line:#d0d7de;--code:#f3f4f6;
--pass:#1a7f37;--fail:#cf222e;--none:#6e7781;--link:#0550ae}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9da7b3;--line:#30363d;--code:#161b22;
--pass:#3fb950;--fail:#f85149;--none:#8b949e;--link:#79c0ff}}
body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1rem}
a{color:var(--link)}h1,h2,h3{line-height:1.25}h2{border-bottom:1px solid var(--line);padding-bottom:.25rem;margin-top:2rem}
code,pre{background:var(--code);font:14px/1.4 ui-monospace,monospace;border-radius:4px}code{padding:0 .2rem}
pre{padding:.75rem;overflow-x:auto;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{border:1px solid var(--line);padding:.3rem .5rem;
text-align:left;vertical-align:top}.table-wrap{overflow-x:auto}dt{font-weight:600}dd{margin:0 0 .5rem 1rem}
.muted{color:var(--muted)}.badge{border:1px solid;border-radius:1rem;padding:0 .5rem;font-size:14px;white-space:nowrap}
.pass{color:var(--pass)}.fail{color:var(--fail)}.none{color:var(--none)}.unrendered{color:var(--fail);font-weight:600}
.facts-unavailable{border-left:4px solid var(--fail);padding-left:.75rem}"""


def page(title: str, body: str, home: str) -> str:
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{e(title)}</title>\n'
            f"<style>\n{CSS}\n</style>\n</head>\n<body>\n"
            f'<nav><a href="{e(home)}">All products</a> | <a href="/dbt-docs/">dbt docs</a></nav>\n'
            f"<main>\n{body}</main>\n</body>\n</html>\n")


def _dl(rows: list[tuple[str, str]]) -> str:
    """A definition list; each value is already HTML."""
    return "<dl>\n" + "".join(f"<dt>{e(k)}</dt><dd>{v}</dd>\n" for k, v in rows if v) + "</dl>\n"


def facts_notice(facts: dict[str, Any], now: datetime) -> str:
    if facts.get("available"):
        return ""
    return (f'<p class="facts-unavailable">Run-time facts unavailable at {e(when(now))}: {e(facts.get("reason", ""))}. '
            "The rest of this page is from the release and is current.</p>\n")


def _sec_header(doc: dict[str, Any]) -> str:
    d, p = doc["domain"], doc["name"]
    rows = [("Owner", e(doc["owner"])), ("State", e(doc["status"])), ("Type", e(doc["type"])),
            ("Version", e(doc["version"])), ("Release tag", e(doc["tag"])),
            ("Catalog document", link(f"/v1/products/{d}/{p}", f"/v1/products/{d}/{p}"))]
    return f"<h1>{e(p)}</h1>\n<p class=\"muted\">Domain {e(d)}, product id <code>{e(doc['id'])}</code></p>\n{_dl(rows)}"


def _sec_description(doc: dict[str, Any]) -> str:
    out = f"<h2>Purpose</h2>\n<p>{e(doc['purpose'])}</p>\n"
    if doc.get("usage"):
        out += f"<h2>How to use it</h2>\n<p>{e(doc['usage'])}</p>\n"
    if doc.get("limitations"):
        out += f"<h2>Limitations</h2>\n<p>{e(doc['limitations'])}</p>\n"
    return out


def _sec_lifecycle(doc: dict[str, Any]) -> str:
    lc = doc.get("lifecycleChange")
    if not lc:
        return ""
    rows = [("State", e(doc["status"])), ("Since", e(lc["since"])), ("Why", e(lc["reason"])), ("Decided in", e(lc["decision"])),
            ("Successor", e(lc.get("successor", ""))), ("Sunset", e(lc.get("sunset", ""))), ("Resuming", e(lc.get("resume", "")))]
    return f"<h2>Lifecycle</h2>\n{_dl(rows)}"


def _sec_status(doc: dict[str, Any], facts: dict[str, Any], now: datetime) -> str:
    out = "<h2>Run-time status</h2>\n"
    if not facts.get("available"):
        return out + facts_notice(facts, now)
    pf = facts["products"].get(doc["id"])
    run = (pf or {}).get("lastRun")
    if run:
        outcome = "passed" if run["ok"] else f"failed at {e(run['failedTasks'] or 'a step')}"
        last = f"<code>{e(run['runId'])}</code>, finished {e(when(run['at']))}: {outcome}"
    else:
        last = f"no run recorded in the last {RUN_WINDOW_DAYS} days"
    tests = (pf or {}).get("tests")
    if tests:
        counts = ", ".join(f"{n} {e(s)}" for s, n in sorted(tests["counts"].items()))
        tested = f"{tests_badge(pf, True)} {counts} (dbt invocation <code>{e(tests['invocation'])}</code>, started {e(when(tests['at']))})"
    else:
        tested = tests_badge(pf, True)
    published = newest_publish(pf)
    st = (pf or {}).get("state")
    rows = [("State", state_badge(st)), ("Newest tested publish", e(when(published)) if published else "not yet published"),
            ("Last run", last), ("Tests, newest invocation", tested), ("Freshness", freshness(doc, pf, now))]
    return out + _dl(rows) + _gaps((pf or {}).get("gaps")) + f'<p class="muted">As read at {e(when(now))}.</p>\n'


def _sec_terms(doc: dict[str, Any]) -> str:
    rows = []
    if terms := doc.get("terms"):
        rows.append(("Redistribution", e(terms["redistribution"])))
        if terms.get("attribution"):
            rows.append(("Attribution", e(terms["attribution"])))
    else:
        rows.append(("Terms", "not recorded; the owner records them before any grant outside the owning team"))
    if doc.get("licence"):
        rows.append(("Licence", link(doc["licence"], doc["licence"])))
    for item in doc.get("links", []):
        rows.append((f"Link ({item['type']})", link(item["url"], item["url"]) + (f" {e(item['description'])}" if item.get("description") else "")))
    for s in doc.get("support", []):
        rows.append((f"Support ({s['channel']})", link(s["url"], s["url"]) + (f" {e(s['description'])}" if s.get("description") else "")))
    return f"<h2>Terms and licence</h2>\n{_dl(rows)}"


def _sec_inputs(doc: dict[str, Any], known: set[str]) -> str:
    if not doc["inputs"]:
        return ""
    items = []
    for i in doc["inputs"]:
        product = i["contractId"].rsplit(".", 1)[0]
        d, p = product.split(".", 1)
        name = link(f"../../{d}/{p}/index.html", product) if product in known else e(product)
        lag = f", at most {e(str(i['maxLagMinutes']))} minutes late" if i.get("maxLagMinutes") is not None else ""
        need = "required" if i["required"] else "optional"
        items.append(f"<li><code>{e(i['relation'])}</code> from {name} ({need}{lag})</li>")
    return "<h2>Inputs</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>\n"


def _columns(out: dict[str, Any]) -> str:
    rows = []
    for c in out["columns"]:
        notes = [f"{k} {e(str(c[k]))}" for k in ("unit", "time", "logicalType", "classification") if c.get(k)]
        if c.get("unique"):
            notes.append("unique")
        notes += [f"references <code>{e(r['relation'])}.{e(r['column'])}</code>" + (f" as of {e(r['asOf'])} time" if r.get("asOf") else "")
                  for r in c.get("references", [])]
        key = f"yes ({c['primaryKeyPosition']})" if c["primaryKey"] and c.get("primaryKeyPosition") else ("yes" if c["primaryKey"] else "")
        extra = f'<br><span class="muted">{"; ".join(notes)}</span>' if notes else ""
        rows.append(f"<tr><th scope=\"row\"><code>{e(c['name'])}</code></th><td><code>{e(c['type'])}</code></td>"
                    f"<td>{key}</td><td>{'yes' if c['required'] else 'no'}</td><td>{e(c['description'])}{extra}</td></tr>")
    return ('<div class="table-wrap"><table>\n<caption class="muted">Columns, in order</caption>\n'
            '<thead><tr><th scope="col">Column</th><th scope="col">Type</th><th scope="col">Key</th>'
            '<th scope="col">Never null</th><th scope="col">Description</th></tr></thead>\n<tbody>\n'
            + "\n".join(rows) + "\n</tbody></table></div>\n")


def _keys(out: dict[str, Any]) -> str:
    key = sorted((c for c in out["columns"] if c["primaryKey"]), key=lambda c: c.get("primaryKeyPosition") or 0)
    if not key:
        return "<p>Key: none declared.</p>\n"
    names = [f"<code>{e(c['name'])}</code>" for c in key]
    return f"<p>Key: {listed(names)}{', in that order' if len(names) > 1 else ''}.</p>\n"


def _roles(out: dict[str, Any], problems: list[str]) -> str:
    if not out["roles"]:
        return "<p>Access: this table offers no reader role.</p>\n"
    items = []
    for r in out["roles"]:
        clauses = policy_words(r.get("policies") or {}, problems, f"{out['contractId']} role {r['role']}")
        sees = "; ".join(clauses) if clauses else "every row and column"
        holders = sorted(x["principal"] for x in out["readers"] if x["role"] == r["role"])
        held = f" Held by {listed([f'<code>{e(h)}</code>' for h in holders])}." if holders else " Held by no one yet."
        approvers = f" Requests approved by {e(r['approvers'])}." if r.get("approvers") else ""
        items.append(f"<li>Readers with role <code>{e(r['role'])}</code> see {sees}.{held}{approvers}</li>")
    return "<p>Access, by role:</p>\n<ul>\n" + "\n".join(items) + "\n</ul>\n"


def _how_to_read(doc: dict[str, Any], out: dict[str, Any]) -> str:
    if not out["relation"]:
        return ""
    table = out["contractId"].rsplit(".", 1)[1]
    route = f"/v1/data/{doc['domain']}/{doc['name']}/{table}?limit=10"
    return (f"<p>Read it in SQL through Trino:</p>\n<pre><code>SELECT * FROM {e(out['relation'])} LIMIT 10</code></pre>\n"
            f"<p>or over REST from k2-api, with your API key in the Authorization header:</p>\n"
            f"<pre><code>GET {e(route)}</code></pre>\n")


def _sec_table(doc: dict[str, Any], out: dict[str, Any], of: dict[str, Any] | None, available: bool, problems: list[str]) -> str:
    table = out["contractId"].rsplit(".", 1)[1]
    head = f'<h3 id="{e(table)}">{e(table)}</h3>\n'
    if out["relation"]:
        head += f"<p>Reader name <code>{e(out['relation'])}</code></p>\n"
    head += f"<p>{e(out['purpose'])}</p>\n"
    if not available:
        published = "unknown: run-time facts unavailable"
    elif out["relation"] and of and of.get("snapshot") is not None:
        published = f"{e(when(of.get('publishedAt')))}, snapshot <code>{e(str(of['snapshot']))}</code>"
    else:
        published = "not yet published"
    serves = out.get("serves")
    sla = "; ".join(f"{e(s['property'])} {e(str(s['value']))}{' ' + e(s['unit']) if s.get('unit') else ''}"
                    + (f" on <code>{e(s['element'])}</code>" if s.get("element") else "") for s in out.get("sla", []))
    rows = [("Layer", e(out["layer"])), ("Built by", e(out.get("build", ""))), ("Contract version", e(out["version"])),
            ("Contract", link(f"/v1/products/{doc['domain']}/{doc['name']}/contracts/{table}.odcs.yaml", f"{table}.odcs.yaml")),
            ("Newest tested publish", published if out["relation"] else ""),
            ("Tested snapshot", f"the <code>{e(out['testedRef'])}</code> ref" if out.get("testedRef") else ""),
            ("Served by", f"{e(serves['engine'])}, compose profile <code>{e(serves['profile'])}</code>, {e(serves['status'])}" if serves else ""),
            ("Partitioning", ", ".join(f"<code>{e(p)}</code>" for p in out.get("partitioning", []))),
            ("Retention", f"{out['retentionDays']} days" if out.get("retentionDays") else "the platform's 7-day floor"),
            ("Service level", sla),
            ("dbt model", link("/" + out["dbtDocs"], "this table on the dbt docs site") if out.get("dbtDocs") else "not built by dbt")]
    quality = "".join(f"<li><code>{e(q.get('name', q['type']))}</code>: {e(q.get('description', ''))}</li>\n" for q in out.get("quality", []))
    quality = f"<p>Quality rules:</p>\n<ul>\n{quality}</ul>\n" if quality else ""
    return head + _dl(rows) + _columns(out) + _keys(out) + _roles(out, problems) + quality + _how_to_read(doc, out)


def _sec_tables(doc: dict[str, Any], facts: dict[str, Any], problems: list[str]) -> str:
    pf = (facts.get("products") or {}).get(doc["id"]) or {}
    parts = [_sec_table(doc, o, (pf.get("outputs") or {}).get(o["contractId"]), bool(facts.get("available")), problems)
             for o in sorted(doc["outputs"], key=lambda o: o["contractId"])]
    return "<h2>Tables</h2>\n" + "".join(parts)


def _sec_join_keys(doc: dict[str, Any]) -> str:
    if not doc.get("joinKeys"):
        return ""
    items = [f"<li><code>{e(j['from']['relation'])}.{e(j['from']['column'])}</code> joins "
             f"<code>{e(j['to']['relation'])}.{e(j['to']['column'])}</code>"
             + (f" as of {e(j['asOf'])} time" if j.get("asOf") else "") + "</li>" for j in doc["joinKeys"]]
    return "<h2>Join keys</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>\n"


def _sec_examples(doc: dict[str, Any]) -> str:
    if not doc.get("examples"):
        return ""
    items = [f"<p>{e(x['description'])}</p>\n<pre><code>{e(x['sql'])}</code></pre>\n" for x in doc["examples"]]
    return "<h2>Example queries</h2>\n" + "".join(items)


def _sec_dbt(doc: dict[str, Any]) -> str:
    models = [o for o in sorted(doc["outputs"], key=lambda o: o["contractId"]) if o.get("dbtDocs")]
    items = "".join(f"<li>{link('/' + o['dbtDocs'], o['contractId'].rsplit('.', 1)[1])}</li>\n" for o in models)
    body = f"<ul>\n{items}</ul>\n" if items else "<p>No table of this product is built by dbt.</p>\n"
    return f'<h2>Models on the dbt docs site</h2>\n<p><a href="/dbt-docs/">The dbt docs site</a> documents every model of the release.</p>\n{body}'


def product_page(doc: dict[str, Any], facts: dict[str, Any], now: datetime, known: set[str], problems: list[str]) -> str:
    body = (_sec_header(doc) + _sec_description(doc) + _sec_lifecycle(doc) + _sec_status(doc, facts, now)
            + _sec_terms(doc) + _sec_inputs(doc, known) + _sec_tables(doc, facts, problems)
            + _sec_join_keys(doc) + _sec_examples(doc) + _sec_dbt(doc)
            + f'<p class="muted">Generated {e(when(now))} from release tag {e(doc["tag"])}.</p>\n')
    return page(f"{doc['name']}: K2 data product", body, "../../index.html")


def index_page(index: dict[str, Any], docs: list[dict[str, Any]], facts: dict[str, Any], now: datetime) -> str:
    available = bool(facts.get("available"))
    body = (f"<h1>K2 data products</h1>\n<p>Every product of release tag {e(index['tag'])}: what it is, who owns it "
            f"and whether its newest build passed its tests.</p>\n" + facts_notice(facts, now))
    for domain in sorted({d["domain"] for d in docs}):
        rows = []
        for doc in sorted((d for d in docs if d["domain"] == domain), key=lambda d: d["name"]):
            pf = (facts.get("products") or {}).get(doc["id"])
            newest = newest_publish(pf)
            published = when(newest) if newest else ("unknown" if not available else "not yet published"
                                                     if doc["status"] == "active" else f"not rebuilt ({doc['status']})")
            name = link(f"{doc['domain']}/{doc['name']}/index.html", doc["name"])
            rows.append(f'<tr><th scope="row">{name}</th>'
                        f"<td>{e(first_sentence(doc['purpose']))}</td><td>{e(doc['status'])}</td><td>{e(doc['owner'])}</td>"
                        f"<td>{e(published)}</td><td>{tests_badge(pf, available)}</td></tr>")
        body += (f"<h2>{e(domain)}</h2>\n<div class=\"table-wrap\"><table>\n<thead><tr><th scope=\"col\">Product</th>"
                 '<th scope="col">Purpose</th><th scope="col">State</th><th scope="col">Owner</th>'
                 '<th scope="col">Newest tested publish</th><th scope="col">Tests</th></tr></thead>\n<tbody>\n'
                 + "\n".join(rows) + "\n</tbody></table></div>\n")
    body += f'<p class="muted">Generated {e(when(now))}.</p>\n'
    return page("K2 data products", body, "index.html")


# --- the site ------------------------------------------------------------------------------------


def render(release: Path, facts: dict[str, Any], now: datetime) -> tuple[dict[str, str], list[str]]:
    """Every page by its path in the site, and the problems (an unrendered policy) found."""
    index, docs = load(release)
    known, problems = {d["id"] for d in docs}, []
    pages = {"index.html": index_page(index, docs, facts, now)}
    for doc in docs:
        pages[f"{doc['domain']}/{doc['name']}/index.html"] = product_page(doc, facts, now, known, problems)
    return pages, problems


def publish(pages: dict[str, str], out: Path) -> Path:
    """Write a new tree beside the old, then rename the `current` symlink onto it (atomic on one
    filesystem); the tree before is kept for a reader mid-request, older ones are removed."""
    out.mkdir(parents=True, exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix=TREE, dir=out))
    for rel, text in sorted(pages.items()):
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
    for d in [tree, *(p for p in tree.rglob("*") if p.is_dir())]:
        d.chmod(0o755)  # mkdtemp makes 0700; the server reads as another user
    current = out / CURRENT
    if current.exists() and not current.is_symlink():
        raise RuntimeError(f"{current} is not a symlink; move it away once, then rerun")
    previous = os.readlink(current) if current.is_symlink() else None
    staged = out / f".{CURRENT}.new"
    staged.unlink(missing_ok=True)
    staged.symlink_to(tree.name)  # relative, so it resolves under any mount point
    os.replace(staged, current)
    for old in out.glob(f"{TREE}*"):
        if old.name not in (tree.name, previous):
            shutil.rmtree(old)
    return tree


def generate(release: Path, facts: dict[str, Any], out: Path, now: datetime) -> list[str]:
    pages, problems = render(release, facts, now)
    publish(pages, out)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", type=Path, required=True, help="the release directory to render")
    ap.add_argument("--out", type=Path, required=True, help="the site root; the server serves <out>/current/")
    ap.add_argument("--no-facts", action="store_true", help="do not ask Trino; pages say the facts were not read")
    ap.add_argument("--strict", action="store_true", help="exit 1 when the run-time facts could not be read")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if args.no_facts:
        facts: dict[str, Any] = {"available": False, "reason": "not read (--no-facts)"}
    else:
        try:
            facts = read_facts(connect(), args.release)
        except FactsUnavailable as err:
            facts = {"available": False, "reason": f"Trino did not answer ({err})"}
    problems = generate(args.release, facts, args.out, now)
    for p in problems:
        print(f"k2site: {p}", file=sys.stderr)
    print(f"k2site: wrote {args.out / CURRENT}; facts {'read' if facts['available'] else facts['reason']}")
    if problems:
        return 2
    return 1 if args.strict and not facts["available"] else 0


if __name__ == "__main__":
    sys.exit(main())
