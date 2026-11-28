#!/usr/bin/env python3
"""make product-verify (plan 004, step W1-14): one Kafka product of a release, proved read-only from
snapshot summaries, catalog refs and run records, never a row of its tables.

    product_verify.py --release <release dir> --product <domain/name | domain.name | name> [--wait <s>]

Run in a tools one-shot of the live release (mk/product-verify.mk), the one place that reaches both
the catalog, which has no host port and no authentication (ADR-059 part 7), and Trino as svc.catalog,
whose password and CA the tools service mounts already (docker-compose.yml, tools). No table rule
lets any principal read the landing's or bronze's `$snapshots` (emit/trino_rules.py: svc.platform's
metadata reads stop at the `_build` base namespaces, REFS_READERS get `$refs` alone), so their
summaries come from the catalog's REST API, GET only, through scripts/verify/catalog_drift.py's
client; the run records come through the read-only `governance` catalog, svc.catalog's own rule.
The summary keys and the accounting rule are docker/lake/kafka.py's and offsets.py's, imported.

One line per check, `PASS|FAIL <check> <relation>: <detail>`:

landing  every connector commit on the landing's main lineage, oldest first: its rows
         (`added-records`) are its spans, `k2.kafka-offsets` less `k2.kafka-starts` per partition;
         each start is the end of the commit before, a jump allowed only over a loss that commit's
         cumulative `k2.kafka-gaps` records; the gaps never shrink; no other commit (a compaction)
         changes the row count.
bronze   every connector commit on bronze's main lineage: its through-offsets are the landing's end
         offsets at the snapshot it read (`k2.src-snapshot-id`), and its rows plus `k2.kafka-skipped`
         are the landing rows of its range, from the commit before's through-offsets (the first
         commit: the landing's first offsets) less the recorded losses (kafka.planned); no other
         commit changes the row count.
level    bronze's newest through-offsets are the landing's newest end offsets.
tag      each dbt output of the product and of every product downstream of it through input ports:
         its `audited` tag is the newest `audited` run record of the table.

Not proved: what expiry cut. The walk starts at the oldest connector commit main's lineage holds;
a bronze commit whose start lay in an expired snapshot is counted as unproved, never as a pass of
its own. maintain.py's daily offset_continuity reads the landing's rows over the whole table.

`landing`, `bronze` and bronze past the landing fail at once: each commit is atomic, so a run in
flight never makes them false. `level` and `tag` are false for a moment while a run is between its
landing commit and its publish, or between a tag and its run record (so bronze is read before the
landing, and tags before the records); those are read again every POLL s for --wait s at most.

Writes nothing: catalog GETs and one SELECT per read. Exit 0 every check passed; 1 one failed; 2
refused (no such Kafka product, a table not in the catalog, the catalog or Trino unreachable).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "docker" / "lake")]
import kafka as K  # noqa: E402  the connector's keys and its accounting (kafka.planned)
from access_verify import Trino  # noqa: E402  the access suite's HTTPS client
from connector import AUDIT  # noqa: E402  bronze's write-audit-publish branch
from hold import Refused, resolve  # noqa: E402  PRODUCT as make hold reads it
from verify.catalog_drift import CATALOG_URI, Catalog, Unreachable  # noqa: E402  the REST client, GET only

import offsets as O  # noqa: E402

# svc.catalog as the tools service mounts it, as platform/catalog-site/k2site.py reads it there
USER = "svc.catalog"
PASSWORD_FILE = "/run/k2-secrets/catalog.password"
CA_FILE = "/run/k2-secrets/tls/ca.pem"
RUNS = '"governance"."run_records"."run_record"'  # svc.catalog's exact table rule, read-only at the connector
AUDITED = "audited"  # k2gen.model.AUDITED_TAG
WAIT = 600  # seconds `level` and `tag` are read again for: two runs of a five-minute product
POLL = 20
NAME = re.compile(r"[a-z][a-z0-9_]*")


# --- the pure core -------------------------------------------------------------------------------


def lineage(meta: dict) -> list[dict]:
    """main's lineage in a table's REST metadata, oldest first, stopping where expiry cut it."""
    snaps = {s["snapshot-id"]: s for s in meta.get("snapshots") or []}
    head = ((meta.get("refs") or {}).get("main") or {}).get("snapshot-id")
    return [snaps[i] for i in reversed(O.main_lineage({i: s.get("parent-snapshot-id") for i, s in snaps.items()}, head))]


def whole(line: list) -> bool:
    """Whether the lineage reaches the table's root: nothing on it has expired."""
    return bool(line) and line[0].get("parent-snapshot-id") is None


def summary_of(s: dict) -> dict:
    return s.get("summary") or {}


def connector(s: dict) -> bool:
    """A connector commit, as kafka.newest picks one: k2.job connector, with end offsets."""
    return summary_of(s).get(O.JOB) == K.JOB_CONNECTOR and O.KAFKA_OFFSETS in summary_of(s)


def rows(s: dict) -> int:
    return int(summary_of(s).get("added-records") or 0)


def row_changes(line: list) -> list[str]:
    """Each snapshot that is no connector commit yet changed the row count from its parent's."""
    out = []
    for a, b in zip(line, line[1:]):
        x, y = summary_of(a).get("total-records"), summary_of(b).get("total-records")
        if not connector(b) and x is not None and y is not None and int(x) != int(y):
            out.append(f"snapshot {b['snapshot-id']} ({summary_of(b).get('operation')}, k2.job "
                       f"{summary_of(b).get(O.JOB)}) changed the rows from {x} to {y} and is no connector commit")
    return out


def offsets_of(s: dict, key: str, topic: str, problems: list) -> dict | None:
    """`{partition: offset}` of the product's topic in a summary's `key`, or None when absent."""
    if key not in summary_of(s):
        problems.append(f"commit {s['snapshot-id']} has no {key}")
        return None
    found = O.decode(summary_of(s)[key])
    if set(found) - {topic}:
        problems.append(f"commit {s['snapshot-id']}'s {key} names topics {sorted(found)}, not {topic} alone")
    return found.get(topic, {})


def covered(gaps: list, lo: int, hi: int) -> bool:
    """Whether offsets lo..hi - 1 lie inside one recorded loss `(committed, log start)`."""
    return any(a <= lo and hi <= b for a, b in gaps)


def landing_spans(line: list, topic: str) -> tuple[list[str], dict, dict | None, str]:
    """(problems, the newest end offsets, the landing's first offset per partition when its lineage
    is whole else None, what passed)."""
    problems = row_changes(line)
    prev: dict | None = None
    prev_gaps: dict = {}
    firsts: dict = {}
    commits = jumps = 0
    for s in filter(connector, line):
        sid, commits = s["snapshot-id"], commits + 1
        ends = offsets_of(s, O.KAFKA_OFFSETS, topic, problems) or {}
        starts = offsets_of(s, K.KAFKA_STARTS, topic, problems)
        gaps = K.gaps_of(summary_of(s)).get(topic, {})
        if starts is None:
            prev, prev_gaps = ends, gaps
            continue
        span = 0
        for p, start in sorted(starts.items()):
            end = ends.get(p)
            if end is None or end < start:
                problems.append(f"commit {sid} partition {p}: starts at {start}, ends at {end}")
                continue
            span += end - start
            if end > start:
                firsts.setdefault(p, start)
            was = None if prev is None else prev.get(p)
            if was is None or start == was:
                continue
            if start < was:
                problems.append(f"commit {sid} partition {p}: starts at {start}, inside the span before it, "
                                f"which ended at {was}: an overlap")
            elif covered(gaps.get(p, []), was, start):
                jumps += 1
            else:
                problems.append(f"commit {sid} partition {p}: starts at {start}, past the end before it at {was}: "
                                f"a hole no {K.KAFKA_GAPS} records")
        problems += [f"commit {sid} partition {p}: moved from {was} to {ends.get(p)} with no start recorded"
                     for p, was in sorted((prev or {}).items()) if p not in starts and ends.get(p) != was]
        if span != rows(s):
            problems.append(f"commit {sid} added {rows(s)} rows; its spans hold {span} offsets")
        problems += [f"commit {sid} dropped the recorded loss of partition {p}, offsets {lo}..{hi - 1}"
                     for p, v in sorted(prev_gaps.items()) for lo, hi in v if not covered(gaps.get(p, []), lo, hi)]
        prev, prev_gaps = ends, gaps
    said = (f"{commits} connector commit(s) over {len(prev or {})} partition(s), each whole and starting where the "
            f"one before ended, {jumps} jump(s) each over a recorded loss")
    if not whole(line):
        said += "; from the oldest commit main's lineage still holds"
    return problems, prev or {}, firsts if whole(line) else None, said


def bronze_ranges(before: dict | None, through: dict, firsts: dict | None) -> dict | None:
    """`{partition: (start, end)}` a bronze commit staged: from `before` (the commit before's
    through-offsets), a partition it lacks from the landing's first offset in it (kafka.ranges_of,
    none when the landing held no row below `end`). None when a start is not in any summary held."""
    if before is None:
        return None
    out = {}
    for p, end in through.items():
        if p in before:
            out[p] = (before[p], end)
        elif firsts is None:
            return None
        else:
            out[p] = (min(firsts.get(p, end), end), end)
    return out


def bronze_spans(line: list, landing_line: list, topic: str, firsts: dict | None) -> tuple[list[str], dict, str]:
    """(problems, the newest through-offsets, what passed). `firsts` is landing_spans'."""
    problems = row_changes(line)
    at: dict = {}  # landing snapshot -> its end offsets: those of the newest connector commit at or under it
    ends = None
    for s in landing_line:
        if connector(s):
            ends = O.decode(summary_of(s)[O.KAFKA_OFFSETS]).get(topic, {})
        at[s["snapshot-id"]] = ends
    lost = K.accepted_losses([(0, summary_of(s)) for s in landing_line], topic)
    before: dict | None = {} if whole(line) else None
    through: dict = {}
    commits = staged = skipped_all = read_all = 0
    unproved, unread = [], 0
    for s in filter(connector, line):
        sid, commits = s["snapshot-id"], commits + 1
        through = offsets_of(s, O.KAFKA_OFFSETS, topic, problems) or {}
        skipped = offsets_of(s, K.KAFKA_SKIPPED, topic, problems) or {}
        src = summary_of(s).get(O.SRC_SNAPSHOT_ID)
        if src is None:
            problems.append(f"commit {sid} has no {O.SRC_SNAPSHOT_ID}")
        elif at.get(int(src)) is None:
            unread += 1
        elif at[int(src)] != through:
            problems.append(f"commit {sid} is staged through {through}; the landing at snapshot {src}, which it "
                            f"read, ends at {at[int(src)]}")
        ranges = bronze_ranges(before, through, firsts)
        before = through
        if ranges is None:
            unproved.append(sid)
            continue
        read = 0
        for p, (lo, hi) in sorted(ranges.items()):
            if hi < lo:
                problems.append(f"commit {sid} partition {p}: staged through {hi}, behind the {lo} before it")
                continue
            want = K.planned(lo, hi, lost.get(p, []))
            n = want[0] if want else 0
            if skipped.get(p, 0) > n:
                problems.append(f"commit {sid} partition {p}: {skipped[p]} skipped of {n} landing rows read")
            read += n
        if rows(s) + sum(skipped.values()) != read:
            problems.append(f"commit {sid}: {rows(s)} staged + {sum(skipped.values())} skipped, but its range "
                            f"holds {read} landing rows")
        staged, skipped_all, read_all = staged + rows(s), skipped_all + sum(skipped.values()), read_all + read
    said = f"{commits} connector commit(s): {staged} staged + {skipped_all} skipped = {read_all} landing rows read"
    if unproved:
        said += f"; {len(unproved)} commit(s) not counted, their start in an expired snapshot ({unproved[0]})"
    if unread:
        said += f"; {unread} commit(s) read a landing snapshot since expired"
    return problems, through, said


def level(through: dict, ends: dict, waiting: bool) -> tuple[str, str]:
    """(verdict, detail): bronze's through-offsets against the landing's end offsets."""
    ahead = sorted(p for p, o in through.items() if o > ends.get(p, 0))
    if ahead:
        return "FAIL", f"bronze is staged past the landing's end on partition(s) {ahead}"
    behind = sorted(p for p, e in ends.items() if through.get(p) != e)
    if behind:
        return "WAIT", (f"bronze is staged behind the landing's end on partition(s) {behind}"
                        + ("; a batch waits on its audit branch, unpublished" if waiting else ""))
    return "PASS", f"bronze is staged through the landing's end on all {len(ends)} partition(s)"


def tag(meta: dict | None, recorded: int | None) -> tuple[str, str]:
    """(verdict, detail): an output's `audited` tag against its newest audited run record."""
    if meta is None:
        return "WAIT", "not built yet"
    found = ((meta.get("refs") or {}).get(AUDITED) or {}).get("snapshot-id")
    if found is None:
        return "WAIT", "has no audited tag yet"
    if recorded != found:
        return "WAIT", f"audited tag {found}, but " + ("no audited run record names the table" if recorded is None
                                                       else f"its newest audited run record says {recorded}")
    return "PASS", f"audited tag {found}, as its newest audited run record says"


def verify(s: dict, reader: Any) -> list[tuple[str, str, str, str]]:
    """`(verdict, check, relation, detail)` per check: PASS, FAIL, or WAIT (not level yet)."""
    bronze = reader.table(s["bronze"])  # before the landing: the landing only ever moves ahead of bronze
    landing = reader.table(s["landing"])
    for relation, meta in ((s["landing"], landing), (s["bronze"], bronze)):
        if meta is None:
            raise Refused(f"{relation} is not in the catalog")
    tags = {o: reader.table(o) for o in s["outputs"]}
    recorded = reader.newest_audited(s["outputs"])  # after the tags: a publish moves a tag, then records it
    land_line, bronze_line = lineage(landing), lineage(bronze)
    problems, ends, firsts, said = landing_spans(land_line, s["topic"])
    out = [("FAIL" if problems else "PASS", "landing", s["landing"], "; ".join(problems) or said)]
    problems, through, said = bronze_spans(bronze_line, land_line, s["topic"], firsts)
    out.append(("FAIL" if problems else "PASS", "bronze", s["bronze"], "; ".join(problems) or said))
    refs = bronze.get("refs") or {}
    verdict, detail = level(through, ends, AUDIT in refs and refs[AUDIT] != refs.get("main"))
    out.append((verdict, "level", s["bronze"], detail))
    out += [(v, "tag", o, d) for o in s["outputs"] for v, d in [tag(tags[o], recorded.get(o))]]
    return out


def run(s: dict, reader: Any, wait: float, sleep=time.sleep, clock=time.monotonic) -> int:
    """verify() until nothing waits, a check fails, or `wait` s pass; then its lines and the exit."""
    t0 = clock()
    while True:
        lines = verify(s, reader)
        verdicts = {v for v, *_ in lines}
        if "FAIL" in verdicts or "WAIT" not in verdicts or clock() - t0 + POLL > wait:
            break
        sleep(POLL)
    for v, check, relation, detail in lines:
        print(f"{'PASS' if v == 'PASS' else 'FAIL'}  {check:<7} {relation}: {detail}")
    failed = sum(v != "PASS" for v, *_ in lines)
    waited = f" (read again for {clock() - t0:.0f} s)" if clock() - t0 >= POLL else ""
    print(f"product-verify: {s['product']}: " + (f"{failed} check(s) failed" if failed else
                                                 "every offset accounted for, every layer level") + waited)
    return 1 if failed else 0


# --- the release and the readers -----------------------------------------------------------------


def split(relation: str) -> list[str]:
    parts = relation.split(".")
    if len(parts) != 3 or not all(NAME.fullmatch(x) for x in parts):
        raise Refused(f"{relation!r} is not <catalog>.<namespace>.<table> of identifiers")
    return parts


def outputs(pipes: dict, product: str) -> list[str]:
    """The dbt outputs of `product` and of every product downstream of it through input ports."""
    found, todo = {product}, [product]
    while todo:
        up = todo.pop()
        for pid, pipe in sorted(pipes.items()):
            if pid not in found and any(i["contractId"].rsplit(".", 1)[0] == up for i in pipe.get("inputs") or []):
                found.add(pid)
                todo.append(pid)
    return sorted(o for pid in found for o in (pipes[pid].get("build") or {}).get("outputs") or [])


def subject(release: Path, arg: str) -> dict:
    """What to verify of the product `arg` names in the release: its topic, landing, bronze and the
    dbt outputs from it down, and each Trino catalog's warehouse (its compiled catalog file)."""
    try:
        dags = json.loads((release / "compiled/airflow/dags.json").read_text())["dags"]
        product, _ = resolve(arg, dags)
        pipes = {d["product"]: json.loads((release / d["pipeline"]).read_text()) for d in dags}
        files = (release / "compiled/trino/etc/catalog").glob("*.properties")
        warehouses = {f.stem: m[1] for f in files if (m := re.search(r"(?m)^iceberg\.rest-catalog\.warehouse=(\S+)$", f.read_text()))}
    except (OSError, ValueError, KeyError) as e:
        raise Refused(f"cannot read the release at {release}: {type(e).__name__}: {e}") from None
    pipe = pipes[product]
    if (pipe.get("connector") or {}).get("kind") != "kafka":
        raise Refused(f"{product} is no Kafka product: product-verify accounts for a Kafka connector's offsets")
    [bronze] = pipe["bronze"]
    s = {"product": product, "topic": pipe["connector"]["topic"], "landing": pipe["landing"]["table"],
         "bronze": bronze["table"], "outputs": outputs(pipes, product), "warehouses": warehouses}
    for relation in (s["landing"], s["bronze"], *s["outputs"]):
        if split(relation)[0] not in warehouses:
            raise Refused(f"{relation}: the release has no catalog file for {split(relation)[0]}")
    return s


class Live:
    """The catalog's REST API, GET only, and the run records as svc.catalog, one SELECT a read."""

    def __init__(self, warehouses: dict, uri: str, trino: Any):
        self.warehouses, self.uri, self.trino, self.catalogs = warehouses, uri, trino, {}

    def table(self, relation: str) -> dict | None:
        catalog, ns, t = split(relation)
        w = self.warehouses[catalog]
        if w not in self.catalogs:
            self.catalogs[w] = Catalog(self.uri, w)
        return self.catalogs[w].table(ns, t)

    def newest_audited(self, relations: list[str]) -> dict:
        """{table: snapshot_after of its newest audited row}, as k2runs.NEWEST picks it, through the reader catalog."""
        if not relations:
            return {}
        names = ", ".join(f"'{'.'.join(split(r))}'" for r in relations)  # identifiers only: nothing to escape
        a = self.trino.run(USER, f"SELECT table_name, max_by(snapshot_after, recorded_at) FROM {RUNS} WHERE event = "
                                 f"'audited' AND snapshot_after IS NOT NULL AND table_name IN ({names}) GROUP BY table_name")
        if a.error:
            raise Unreachable(f"the run records as {USER}: {a.says()}")
        return {r: int(snap) for r, snap in a.rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", required=True, type=Path, help="a compiled release directory")
    ap.add_argument("--product", required=True, help="domain/name, domain.name or a bare name one product has")
    ap.add_argument("--wait", type=float, default=WAIT, help=f"seconds level and tag are read again for (default {WAIT})")
    a = ap.parse_args(argv)
    try:
        s = subject(a.release.resolve(), a.product)
        url = f"https://{os.environ.get('K2_TRINO_HOST', 'trino')}:{os.environ.get('K2_TRINO_INTERNAL_PORT', '8443')}"
        trino = Trino(url, CA_FILE, lambda _: Path(PASSWORD_FILE).read_text().strip())
        reader = Live(s["warehouses"], os.environ.get("K2_CATALOG_URI", CATALOG_URI), trino)
        print(f"product-verify: {s['product']} of {a.release.name}: {s['landing']}, {s['bronze']} and "
              f"{len(s['outputs'])} dbt output(s)")
        return run(s, reader, a.wait)
    except Refused as e:
        print(f"product-verify: REFUSED: {e}", file=sys.stderr)
        return 2
    except (Unreachable, OSError) as e:
        print(f"product-verify: unreachable: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
