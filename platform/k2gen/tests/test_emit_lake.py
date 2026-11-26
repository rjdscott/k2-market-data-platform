"""The lake emitter (S15): goldens, the one view shape, quoting under hostile names, purity.

Goldens: `golden/lake/<release path>` for the good set; `golden/lake/repository/<release path>`
for the repository's own set (the access canary and the paused hot tier), compared while the
canary exists. Regenerate both, then review the diff as code:

    cd platform/k2gen && uv run --locked python tests/test_emit_lake.py

Every other test builds its case from the good set, through model.load where a
document can carry it, and through the model itself where only a set past the schema could.
"""

import dataclasses
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import model
from k2gen.emit import lake
from k2gen.model import DocumentSet, Model

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
GOLDEN = Path(__file__).parent / "golden" / "lake"
REPOSITORY = GOLDEN / "repository"
SILVER = "contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
BRONZE = "contracts/sensor-net/station-readings/bronze_readings.odcs.yaml"
CANARY = "contracts/governance/access-canary/product.odps.yaml"

IDENT = r'"(?:[^"]|"")+"'
# The base twice: read at the tag, and its `$refs` named in the guard (ADR-061's Outcome of 2026-11-26).
VIEW = re.compile(rf"SELECT ({IDENT}(?:, {IDENT})*) FROM ({IDENT})\.({IDENT})\.({IDENT}) FOR VERSION AS OF 'audited' "
                  rf"WHERE EXISTS \(SELECT 1 FROM \2\.\3\.({IDENT}) WHERE \"name\" = 'audited' AND \"type\" = 'TAG'\)")
CREATE = re.compile(rf"CREATE OR REPLACE VIEW ({IDENT})\.({IDENT})\.({IDENT}) SECURITY DEFINER AS (.+)")
TOKEN = re.compile(r"""\s+|"(?:[^"]|"")*"|'(?:[^']|'')*'|[0-9]+|[A-Za-z_][A-Za-z0-9_]*|[(),.<>;=]""")
# The only bare words each kind of emitted SQL may hold: keywords, types, transforms. A name is
# never bare, so a name the quoting missed shows up as a word outside these.
VIEW_WORDS = {"SELECT", "FROM", "FOR", "VERSION", "AS", "OF", "WHERE", "EXISTS", "AND"}
SPARK_WORDS = {"CREATE", "TABLE", "NOT", "NULL", "USING", "iceberg", "PARTITIONED", "BY", "TBLPROPERTIES",
               "BOOLEAN", "INT", "BIGINT", "FLOAT", "DOUBLE", "DECIMAL", "STRING", "BINARY", "DATE", "TIMESTAMP_LTZ",
               "TIMESTAMP_NTZ", "ARRAY", "MAP", "years", "months", "days", "hours", "bucket", "truncate"}


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}


def load(documents: dict[str, bytes], tag: str = "v0.1.0") -> Model:
    provenance = model.Provenance("git", tag, "0" * 40, "Example Owner <owner@example.org>", "2026-01-01T00:00:00Z",
                                  model.set_sha256(documents), ())
    return model.load(DocumentSet(documents, provenance))


def edited(documents: dict[str, bytes], path: str, change: Any) -> dict[str, bytes]:
    doc = yaml.safe_load(documents[path])
    change(doc)
    return {**documents, path: yaml.safe_dump(doc, sort_keys=False).encode()}


def columns_of(doc: Any) -> list[dict[str, Any]]:
    return list(doc["schema"][0]["properties"])


def prop(doc: Any, name: str) -> Any:
    """The contract's customProperties entry `name`, by name, never by position."""
    (found,) = [p for p in doc["customProperties"] if p["property"] == name]
    return found


def repository_set(root: Path = model.REPO) -> dict[str, bytes]:
    """The working tree's files under model.DOCUMENT_ROOTS, as a tag's document set holds them."""
    documents = {}
    for prefix in model.DOCUMENT_ROOTS:
        base = root / prefix
        paths = sorted(base.rglob("*")) if prefix.endswith("/") else [base]
        documents |= {p.relative_to(root).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    return documents


def unquote(token: str) -> str:
    assert token[0] == token[-1] == '"', token
    return token[1:-1].replace('""', '"')


def tokens(sql: str) -> list[str]:
    out, pos = [], 0
    while pos < len(sql):
        m = TOKEN.match(sql, pos)
        assert m, f"unexpected text at {pos}: {sql[pos:pos + 40]!r}"
        out.append(m.group())
        pos = m.end()
    return [t for t in out if not t.isspace()]


def view_parts(sql: str) -> tuple[list[str], str]:
    """The view SQL's columns and base, unquoted; AssertionError unless it is exactly the one shape."""
    m = VIEW.fullmatch(sql)
    assert m, sql
    cols = re.findall(IDENT, m[1])
    assert ", ".join(cols) == m[1], sql  # the column list splits back into exactly those names
    assert unquote(m[5]) == unquote(m[4]) + "$refs", sql  # the guard reads the base's own refs
    return [unquote(c) for c in cols], ".".join(unquote(p) for p in m.groups()[1:4])


def held_to_their_shape(out: dict[str, str], m: Model) -> None:
    """Every SQL file the emitter wrote is its one shape, every name in it quoted and known, and
    every JSON file parses."""
    names = {model.LAKE, *model.PLATFORM_TABLES, *(c.name for pt in model.PLATFORM_TABLES.values() for c in pt.columns)}
    for p in m.products:
        names |= {model.reader_catalog(p.domain), model.fence(p.name), model.base_namespace(p.name)}
        names |= {n for t in p.tables for n in (t.name, t.name + "$refs", *(c.name for c in t.columns))}
    names |= {"name", "type"}  # the columns of `$refs` the guard reads
    tables = {model.reader_relation(t): t for p in m.products for t in p.tables if t.build.kind in model.LAKE_BUILDS}
    for path, text in out.items():
        if path.startswith("compiled/trino/views/"):
            t = tables[path.removeprefix("compiled/trino/views/").removesuffix(".sql")]
            assert text.endswith("\n") and text.count("\n") == 1, path
            cols, base = view_parts(text.removesuffix("\n"))
            domain, product, table = t.contract_id.split(".")  # spelt out here, not through the emitter's helpers
            assert cols == [c.name for c in t.columns] and base == f"{domain.replace('-', '_')}.{product.replace('-', '_')}__base.{table}", path
            sql, words, literals = text, VIEW_WORDS, {"'audited'", "'TAG'"}
        elif path.startswith("compiled/lake/ddl/spark/"):
            head = "\n".join(lake.SPARK_SESSION) + "\n"
            assert text.startswith(head + "CREATE TABLE "), path
            sql, words = text.removeprefix(head), SPARK_WORDS
            assert sql.count(";") == 1 and sql.endswith(");\n"), path
            literals = {model.sql_literal(v) for kv in lake.TBLPROPERTIES for v in kv}
        elif path.startswith("compiled/lake/ddl/trino/"):
            assert text == model.RUN_RECORD_SQL.read_text(), path  # the frozen file, copied
            continue
        else:
            assert path.endswith(".json"), path
            json.loads(text)
            continue
        found = tokens(sql)
        assert {w for w in found if w[0].isalpha() or w[0] == "_"} <= words, (path, found)
        assert {unquote(q) for q in found if q[0] == '"'} <= names, path
        assert {lit for lit in found if lit[0] == "'"} == literals, path


# --- goldens ------------------------------------------------------------------------------


def compare_with_golden(out: dict[str, str], root: Path) -> None:
    on_disk = {p.relative_to(root).as_posix() for p in (root / "compiled").rglob("*") if p.is_file()}
    assert sorted(out) == sorted(on_disk)
    for path, text in out.items():
        assert (root / path).read_text() == text, path


def test_the_good_set_compiles_to_its_golden_files() -> None:
    m = load(good())
    out = lake.emit(m)
    held_to_their_shape(out, m)
    assert [(v["relation"], v["base"], v["owner"]) for v in json.loads(out[model.VIEWS])["views"]] == [
        ("ops_insight.daily_load.gold_daily_load", "ops_insight.daily_load__base.gold_daily_load", "pub_daily_load"),
        ("sensor_net.station_readings.silver_readings", "sensor_net.station_readings__base.silver_readings", "pub_station_readings")]
    compare_with_golden(out, GOLDEN)


def test_the_repository_set_with_the_canary_compiles_to_its_golden_files() -> None:
    if not (model.REPO / CANARY).is_file():
        pytest.skip(f"{CANARY} is not in this tree")
    m = load(repository_set())
    out = lake.emit(m)
    held_to_their_shape(out, m)
    assert out[model.view_sql_path("governance.access_canary.events")] == (
        'SELECT "id", "region", "note", "event_ts" FROM "governance"."access_canary__base"."events" FOR VERSION AS OF \'audited\' '
        'WHERE EXISTS (SELECT 1 FROM "governance"."access_canary__base"."events$refs" WHERE "name" = \'audited\' AND "type" = \'TAG\')\n')
    compare_with_golden(out, REPOSITORY)


# --- the one view shape, and the publisher's statements ------------------------------------


def test_a_policy_bearing_column_is_projected_and_no_policy_reaches_any_lake_file() -> None:
    m = load(good())
    (silver,) = [t for p in m.products for t in p.tables if t.name == "silver_readings"]
    policy = silver.policies["delayed_reader"]
    assert policy.delay and policy.mask and policy.row_scope  # the fixture carries all three kinds
    cols, _ = view_parts(lake.emit(m)[model.view_sql_path(model.reader_relation(silver))].removesuffix("\n"))
    assert {policy.delay.column, *policy.mask, policy.row_scope.column} <= set(cols)
    # a policy is a rule on the view's name (S12): dropping every policy changes no lake file
    unpolicied = edited(good(), SILVER, lambda d: d["customProperties"].remove(prop(d, "policies")))
    assert lake.emit(load(unpolicied)) == lake.emit(m)


def test_a_column_removed_from_the_contract_leaves_the_view_and_never_leaves_a_table_silently() -> None:
    def drop_note(doc: Any) -> None:
        doc["schema"][0]["properties"] = [c for c in columns_of(doc) if c["name"] != "operator_note"]

    before = lake.emit(load(good()))
    after_docs = edited(edited(good(), SILVER, drop_note), BRONZE, drop_note)
    after_docs = edited(after_docs, SILVER, lambda d: prop(d, "policies")["value"]["delayed_reader"].pop("mask"))
    after = lake.emit(load(after_docs))
    view = model.view_sql_path("sensor_net.station_readings.silver_readings")
    assert '"operator_note"' in before[view] and '"operator_note"' not in after[view]
    assert "operator_note" not in after[model.VIEWS] and "operator_note" not in after[model.CONTRACT_SCHEMAS]
    # The connector table's DDL changes, so deploy's needs.ddl names it; it is still a bare
    # CREATE TABLE, which fails aloud on the existing table instead of dropping a column.
    ddl = model.ddl_path("spark", "k2", "station_readings", "bronze_readings")
    assert before[ddl] != after[ddl] and model.need_of(ddl) == "ddl"
    assert "CREATE TABLE " in after[ddl] and not re.search(r"\b(IF NOT EXISTS|OR REPLACE|ALTER|DROP)\b", after[ddl])
    # dbt owns its base: no emitted file writes one, so only the view's FROM names it in SQL
    for path, text in after.items():
        if path.endswith(".sql"):
            assert "__base" not in text or path.startswith("compiled/trino/views/"), path
    assert not [p for p in after if "__base" in p]


V = model.View("sensor_net.station_readings.silver_readings", "sensor_net.station_readings__base.silver_readings",
               ("station_id", "read_at", "value"), "pub_station_readings")
HEAD = 'CREATE OR REPLACE VIEW "sensor_net_build"."station_readings"."silver_readings" SECURITY DEFINER AS '


def cols(statement: str | None, v: model.View = V) -> list[str]:
    """The columns of one of the publisher's statements; AssertionError unless it is the one shape."""
    assert statement
    m = CREATE.fullmatch(statement)
    catalog, namespace, table = v.relation.split(".")
    assert m and [unquote(p) for p in m.groups()[:3]] == [f"{catalog}_build", namespace, table]  # spelt out, not build_catalog
    names, base = view_parts(m[4])
    assert base == v.base
    return names


def test_view_update_follows_the_rule_around_the_tag_commit() -> None:
    assert lake.create_view(V).startswith(HEAD)
    i, ts = "integer", "timestamp(6) with time zone"
    # a column added: the view gains it only after the tag moves
    before, after = lake.view_update(V, old={"station_id": i, "read_at": ts}, new={"station_id": i, "read_at": ts, "value": i})
    assert cols(before) == ["station_id", "read_at"] and cols(after) == ["station_id", "read_at", "value"]
    # a column dropped: the view loses it before the tag moves; C's order, never the table's
    before, after = lake.view_update(V, old={"station_id": i, "read_at": ts, "value": i, "gone": i},
                                     new={"read_at": ts, "value": i, "station_id": i})
    assert cols(before) == cols(after) == ["station_id", "read_at", "value"]
    # a column the table has and the contract does not is never served
    _, after = lake.view_update(V, old=None, new={"station_id": i, "extra": i, "read_at": ts, "value": i})
    assert cols(after) == ["station_id", "read_at", "value"]
    # a type change is a drop and an add: out before the commit, back after it
    for wider in ("bigint", "varchar"):
        before, after = lake.view_update(V, old={"station_id": i, "read_at": ts, "value": i}, new={"station_id": i, "read_at": ts, "value": wider})
        assert cols(before) == ["station_id", "read_at"] and cols(after) == ["station_id", "read_at", "value"]
    assert cols(lake.create_view(V)) == list(V.columns) and lake.create_view(V).endswith(" AS " + lake.view_sql(V))
    with pytest.raises(ValueError, match="at least one column"):
        lake.view_update(V, old=None, new={"extra": i})


def test_a_published_view_with_nothing_in_common_is_its_own_outcome_never_a_first_publish() -> None:
    i = "integer"
    assert lake.view_update(V, old=None, new={c: i for c in V.columns})[0] is None  # the first publish
    for old, new in (({"other": i}, {c: i for c in V.columns}),  # every column renamed
                     ({c: i for c in V.columns}, {c: "bigint" for c in V.columns})):  # every column retyped
        with pytest.raises(lake.NoCommonColumns, match="no column of the contract"):
            lake.view_update(V, old=old, new=new)


class ToyTrino:
    """A definer view over the `audited` tag as Trino 484 keeps it (the review's demonstrations):
    CREATE resolves every column at the tagged snapshot and stores its type, and a read fails
    unless every stored column is still there with that type."""

    def __init__(self, snapshots: dict[str, dict[str, str]], tag: str | None) -> None:
        self.snapshots, self.tag = snapshots, tag
        self.view: dict[str, str] | None = None if tag is None else dict(snapshots[tag])  # release N's view, every column

    def tagged(self) -> dict[str, str] | None:
        return None if self.tag is None else self.snapshots[self.tag]

    def run(self, statement: str, v: model.View) -> None:
        tagged = self.tagged()
        assert tagged is not None and set(cols(statement, v)) <= set(tagged), "CREATE fails: a column cannot be resolved"
        self.view = {c: tagged[c] for c in cols(statement, v)}

    def readable(self) -> bool:
        tagged = self.tagged()
        return self.view is None or (tagged is not None and all(tagged.get(c) == t for c, t in self.view.items()))


def publish(trino: ToyTrino, v: model.View, crash_after: int | None = None) -> None:
    """The wrapper's publish (ADR-061 part 5), stopping after step `crash_after` as a crash would."""
    before, after = lake.view_update(v, trino.tagged(), trino.snapshots["new"])
    steps = [lambda: trino.run(before, v) if before else None, lambda: setattr(trino, "tag", "new"), lambda: trino.run(after, v)]
    for n, step in enumerate(steps):
        step()
        assert trino.readable(), f"readers fail after step {n}"
        assert trino.view is None or set(trino.view) <= set(v.columns)
        if n == crash_after:
            return


INT, BIG, STR = "integer", "bigint", "varchar"
CHANGES = {
    "widen": (("a", "b"), {"a": INT, "b": INT}, {"a": INT, "b": BIG}),
    "retype": (("a", "b"), {"a": INT, "b": INT}, {"a": INT, "b": STR}),
    "rename": (("a", "c"), {"a": INT, "b": INT}, {"a": INT, "c": INT}),
    "add": (("a", "b"), {"a": INT}, {"a": INT, "b": INT}),
    "drop": (("a",), {"a": INT, "b": INT}, {"a": INT}),
    "no change": (("a", "b"), {"a": INT, "b": INT}, {"a": INT, "b": INT}),
    "first creation": (("a", "b"), None, {"a": INT, "b": INT}),
}


@pytest.mark.parametrize("crash_after", [None, 0, 1, 2])
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_readers_never_fail_across_the_commit_and_a_rerun_after_any_crash_converges(change: str, crash_after: int | None) -> None:
    contract, old, new = CHANGES[change]
    v = model.View("d.p.t", "d.p__base.t", contract, "pub_p")
    trino = ToyTrino({"old": old or {}, "new": new}, None if old is None else "old")
    publish(trino, v, crash_after)
    publish(trino, v)  # the reconcile reruns the whole publish from what the tag holds now
    assert trino.tag == "new" and trino.view == {c: new[c] for c in contract if c in new}


@pytest.mark.parametrize("crash_after", [None, 0, 1, 2])
def test_a_column_dropped_then_added_back_with_another_type_never_breaks_a_read(crash_after: int | None) -> None:
    trino = ToyTrino({"old": {"a": INT, "b": INT}, "new": {"a": INT}}, "old")
    dropped = model.View("d.p.t", "d.p__base.t", ("a",), "pub_p")  # release N drops b
    publish(trino, dropped, crash_after)
    publish(trino, dropped)
    trino.snapshots, trino.tag = {"old": trino.snapshots["new"], "new": {"a": INT, "b": STR}}, "old"
    back = model.View("d.p.t", "d.p__base.t", ("a", "b"), "pub_p")  # release N+1 adds b back, a varchar
    publish(trino, back, crash_after)
    publish(trino, back)
    assert trino.view == {"a": INT, "b": STR}


# --- hostile names --------------------------------------------------------------------------

# Names the schema takes that are keywords, functions or the view's own words, unquoted.
HOSTILE_SLUGS = ["select", "from", "for-version-as-of", "current-user", "audited", "union-all", "drop-table",
                 "a" * 40, "x9", "a1-b2-c3", "lakehouse", "security-definer"]
HOSTILE_TABLES = ["version", "of", "audited", "timestamp", "user", "view", "security", "definer", "a" * 63, "x1_y2"]
HOSTILE_COLUMNS = ["version", "of", "audited", "user", "timestamp", "zone", "at", "a__base", "x__", "security", "definer",
                   "invoker", "replace", "view", "nulls", "first", "row", "map", "array", "date", "interval", "tag",
                   "a" * 63, "b" * 62 + "_"]


def renamed(documents: dict[str, bytes], words: dict[str, str]) -> dict[str, bytes]:
    """The set with each word replaced, in paths and texts alike."""
    def swap(text: str) -> str:
        for old, new in words.items():
            text = text.replace(old, new)
        return text

    return {swap(p): swap(d.decode()).encode() for p, d in documents.items()}


def name_taken(name: str, problem: Any) -> bool:
    return bool(problem(name)) or model.fence(name) in model.TRINO_RESERVED


def hostile_set(seed: int) -> tuple[dict[str, bytes], str, list[str]]:
    rng = random.Random(seed)
    # the model's own name rules refuse some of these (select as a domain); the next tests have those
    domain = rng.choice([n for n in HOSTILE_SLUGS if not name_taken(n, model.domain_name_problem)])
    product = rng.choice([n for n in HOSTILE_SLUGS if not name_taken(n, model.product_name_problem) and n != domain])
    table = rng.choice(HOSTILE_TABLES)
    extra = rng.sample(HOSTILE_COLUMNS, 4)
    docs = renamed(good(), {"sensor-net": domain, "sensor_net": model.fence(domain),
                            "station-readings": product, "station_readings": model.fence(product), "silver_readings": table})
    silver = SILVER.replace("sensor-net", domain).replace("station-readings", product).replace("silver_readings", table)
    bronze = BRONZE.replace("sensor-net", domain).replace("station-readings", product)
    add = [{"name": c, "physicalType": "varchar", "description": "A hostile name."} for c in extra]
    docs = edited(edited(docs, silver, lambda d: d["schema"][0]["properties"].extend(add)), bronze,
                  lambda d: d["schema"][0]["properties"].extend(add))
    return docs, model.view_sql_path(f"{model.fence(domain)}.{model.fence(product)}.{table}"), extra


@pytest.mark.parametrize("seed", range(40))
def test_hostile_names_that_pass_the_schema_stay_one_quoted_token(seed: int) -> None:
    docs, view, extra = hostile_set(seed)
    m = load(docs)  # every document still passes its schemas: these are names the schema takes
    out = lake.emit(m)
    held_to_their_shape(out, m)
    assert len([p for p in out if p.startswith("compiled/trino/views/")]) == 2
    assert view_parts(out[view].removesuffix("\n"))[0][-4:] == extra


# Names past the schema, put straight into the model, one generator per class. Every class but
# the reserved words is refused wherever it stands; a reserved word as a table or column is
# emitted as one quoted token, and as a domain or product (a catalog or namespace) refused.
CONFUSABLE = {"a": "а", "e": "е", "o": "о", "i": "і", "c": "с"}  # Cyrillic lookalikes


def hostile(kind: str, rng: random.Random, base: str) -> list[str]:
    at = rng.randrange(1, len(base))
    if kind == "quote":
        return [rng.choice([base[:at] + '"' + base[at:], base + '" FROM "x', base + '""'])]
    if kind == "backslash":
        return [rng.choice([base + "\\", base[:at] + "\\" + base[at:], base + '\\"'])]
    if kind == "case":
        return [base[:at] + base[at:].upper() if rng.random() < 0.5 else base.upper()]
    if kind == "confusable":
        spots = [n for n, ch in enumerate(base) if ch in CONFUSABLE]
        n = rng.choice(spots)
        return [base[:n] + CONFUSABLE[base[n]] + base[n + 1:]]
    if kind == "folded twin":
        return [base, rng.choice([base.upper(), base.capitalize(), base.replace("s", "ſ")])]  # U+017F folds to s
    assert kind == "reserved"
    return [rng.choice(sorted(model.TRINO_RESERVED))]


def with_names(m: Model, where: str, names: list[str]) -> Model:
    """The good model with `names` at one place, past the schema and the checks: the first column
    of a dbt (view) or connector (Spark) table, that table's name (a twin is a copy beside it),
    or the product's or domain's name, every contract id following."""
    (station,) = [p for p in m.products if p.name == "station-readings"]
    kind, part = where.split(" ")
    table_name = {"view": "silver_readings", "spark": "bronze_readings"}.get(kind)

    def renamed_table(t: model.Table, name: str) -> model.Table:
        return dataclasses.replace(t, name=name, contract_id=model.contract_id(station.domain, station.name, name))

    def tables(t: model.Table) -> list[model.Table]:
        if t.name != table_name:
            return [t]
        if part == "column":
            return [dataclasses.replace(t, columns=(*(dataclasses.replace(c, name=n) for c, n in zip(t.columns, names, strict=False)), *t.columns[len(names):]))]
        return [renamed_table(t, n) for n in names]

    if part in ("column", "table"):
        p = dataclasses.replace(station, tables=tuple(x for t in station.tables for x in tables(t)))
        return dataclasses.replace(m, products=tuple(p if q is station else q for q in m.products))
    (name,) = names
    domain, product = (name, station.name) if kind == "domain" else (station.domain, name)
    p = dataclasses.replace(station, name=product, domain=domain, tables=tuple(
        dataclasses.replace(t, contract_id=model.contract_id(domain, product, t.name)) for t in station.tables))
    domains = tuple(dataclasses.replace(d, name=domain) if d.name == station.domain else d for d in m.domains)
    return dataclasses.replace(m, domains=domains, products=tuple(p if q is station else q for q in m.products))


PLACES = ["view column", "view table", "spark column", "spark table", "product name", "domain name"]


KINDS = ["quote", "backslash", "case", "confusable", "folded twin", "reserved"]


# a folded twin of a product or domain is two products or domains of one name: the identity tests
@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize(("kind", "where"), [(k, w) for k in KINDS for w in PLACES if not (k == "folded twin" and w.endswith("name"))])
def test_names_past_the_schema_are_refused_or_one_quoted_token(kind: str, where: str, seed: int) -> None:
    m = load(good())
    base = {"view column": "station_id", "view table": "silver_readings", "spark column": "station_id",
            "spark table": "bronze_readings", "product name": "station-readings", "domain name": "sensor-net"}[where]
    names = hostile(kind, random.Random(f"{kind}/{where}/{seed}"), base)
    m = with_names(m, where, names)
    if kind != "reserved" or where.endswith("name"):
        refusal = {"view": "a view can carry|appears twice", "spark": "Spark DDL can quote safely"}.get(where.split(" ")[0], "must be lower-case|reserved word")
        with pytest.raises(ValueError, match=refusal):
            lake.emit(m)
        return
    out = lake.emit(m)
    held_to_their_shape(out, m)  # every name one quoted token, and the view's columns read back
    assert any(model.quote_ident(names[0]) in text for path, text in out.items() if path.endswith(".sql"))


def test_two_columns_that_differ_only_in_case_are_refused_in_the_view_sql() -> None:
    # Trino 484 keeps `SELECT "a", "A"` as a view of two columns both named a
    for twins in (("station_id", "STATION_ID"), ("station_id", "station_id")):
        with pytest.raises(ValueError, match="appears twice"):
            lake.view_sql(dataclasses.replace(V, columns=twins))
    with pytest.raises(ValueError, match="model.IDENTIFIER"):
        lake.view_sql(dataclasses.replace(V, columns=("MixedCase",)))


def test_a_spark_name_with_a_backslash_is_refused_even_past_the_schema() -> None:
    # Spark reads `\\"` inside a double-quoted name as an escaped quote (checked on 3.5.9), so
    # quote_ident alone would let such a name run on into the statement.
    m = load(good())
    m = dataclasses.replace(m, products=tuple(dataclasses.replace(p, tables=tuple(
        dataclasses.replace(t, columns=(dataclasses.replace(t.columns[0], name="a\\"), *t.columns[1:]))
        if t.name == "bronze_readings" else t for t in p.tables)) for p in m.products))
    with pytest.raises(ValueError, match="Spark DDL can quote safely"):
        lake.emit(m)


def test_an_undeclared_domain_named_like_the_lake_is_refused() -> None:
    m = load(good())
    m = dataclasses.replace(m, domains=tuple(d for d in m.domains if d.name != "sensor-net"), products=tuple(
        dataclasses.replace(p, domain="lake", tables=tuple(dataclasses.replace(t, contract_id="lake." + t.contract_id.split(".", 1)[1])
                                                           for t in p.tables)) if p.name == "station-readings" else p for p in m.products))
    with pytest.raises(ValueError, match="read-only catalog"):
        lake.emit(m)


@pytest.mark.parametrize(("where", "name", "why"), [
    ("domain", "lake", "read-only catalog"), ("domain", "k2", "warehouse"), ("domain", "system", "Trino's own catalog"), ("domain", "select", "reserved word"),
    ("product", "runs", "reserved"), ("product", "bronze", "reserved"),
])
def test_names_the_model_refuses_are_refused_even_past_the_checks(where: str, name: str, why: str) -> None:
    old = {"domain": "sensor-net", "product": "station-readings"}[where]
    docs = renamed(good(), {old: name, model.fence(old): model.fence(name)})
    with pytest.raises(ValueError, match=why):
        lake.emit(load(docs))


def run_records_product() -> dict[str, bytes]:
    """The review's f1: product run-records in domain governance, whose view would sit on the
    platform's run-record table and whose publisher would own the platform's namespace."""
    return renamed(good(), {"gold_daily_load": "run_record", "ops-insight": "governance", "ops_insight": "governance",
                            "daily-load": "run-records", "daily_load": "run_records"})


@pytest.mark.parametrize(("guards_off", "why"), [
    ((), "run_records is reserved"),  # model.RESERVED_PRODUCT_NAMES
    (("reserved name",), "governance.run_records: one namespace, two owners: platform and governance.run-records"),
    (("reserved name", "owner"), "governance.run_records.run_record: a view and a table at one name"),
])
def test_no_product_takes_the_run_record_namespace_each_guard_alone(monkeypatch: pytest.MonkeyPatch, guards_off: tuple[str, ...], why: str) -> None:
    if "reserved name" in guards_off:
        monkeypatch.setattr(model, "RESERVED_PRODUCT_NAMES", model.RESERVED_PRODUCT_NAMES - {"run_records"})
    if "owner" in guards_off:  # the platform's namespace made the product's, so only the view rule is left
        monkeypatch.setattr(lake, "PLATFORM_OWNER", "governance.run-records")
    with pytest.raises(ValueError, match=re.escape(why)):
        lake.emit(load(run_records_product()))


def test_the_run_record_namespace_is_the_platforms_before_its_domain_is_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model, "RESERVED_PRODUCT_NAMES", model.RESERVED_PRODUCT_NAMES - {"run_records"})
    m = load(run_records_product())
    m = dataclasses.replace(m, domains=tuple(d for d in m.domains if d.name != "governance"))
    with pytest.raises(ValueError, match="two owners: platform"):
        lake.emit(m)


def test_a_contract_whose_id_names_another_product_is_refused() -> None:
    # the review's f2 (a): S05 reports only docs.contract-misplaced, which k2gen release does not run
    docs = edited(good(), SILVER, lambda d: d.update(id="ops-insight.daily-load.silver_readings"))
    with pytest.raises(ValueError, match="names another table than its own, sensor-net.station-readings.silver_readings"):
        lake.emit(load(docs))


def test_two_products_of_one_name_in_two_domains_are_refused() -> None:
    # the review's f2 (b): both would be pub_daily_load and dbt_daily_load
    g = good()
    twin = {p.replace("contracts/ops-insight/", "contracts/sensor-net/"): d.decode().replace("ops-insight", "sensor-net").replace("ops_insight", "sensor_net").encode()
            for p, d in g.items() if p.startswith("contracts/ops-insight/daily-load/")}
    with pytest.raises(ValueError, match="daily-load: two products share the name, and so its principals pub_daily_load and dbt_daily_load"):
        lake.emit(load({**g, **twin}))


def with_retention(m: Model, days: Any) -> Model:
    return dataclasses.replace(m, products=tuple(dataclasses.replace(p, tables=tuple(
        dataclasses.replace(t, retention=days) if t.name == "silver_readings" else t for t in p.tables)) for p in m.products))


@pytest.mark.parametrize("days", [0, -5, True, False, 1.5, 7.0, "7"])
def test_retention_is_a_whole_number_of_days_of_at_least_one_even_past_the_schema(days: Any) -> None:
    with pytest.raises(ValueError, match="not a whole number of days, at least 1"):
        lake.emit(with_retention(load(good()), days))


def test_one_day_of_retention_is_emitted() -> None:
    assert yaml.safe_load(lake.emit(with_retention(load(good()), 1))[model.RETENTION])["sensor_net.station_readings__base.silver_readings"] == 1


# --- Spark DDL ------------------------------------------------------------------------------


@pytest.mark.parametrize(("trino", "spark"), [
    ("boolean", "BOOLEAN"), ("integer", "INT"), ("bigint", "BIGINT"), ("real", "FLOAT"), ("double", "DOUBLE"),
    ("decimal(38,0)", "DECIMAL(38,0)"), ("varchar", "STRING"), ("varbinary", "BINARY"), ("date", "DATE"),
    ("timestamp(6)", "TIMESTAMP_NTZ"), ("timestamp(6) with time zone", "TIMESTAMP_LTZ"),
    ("array(decimal(12,3))", "ARRAY<DECIMAL(12,3)>"), ("map(varchar, timestamp(6) with time zone)", "MAP<STRING, TIMESTAMP_LTZ>"),
])
def test_spark_types_follow_the_type_grammar(trino: str, spark: str) -> None:
    assert lake._spark_type(trino, "t") == spark


@pytest.mark.parametrize(("trino", "why"), [
    ("time(6)", "cannot create"), ("uuid", "cannot create"), ("array(uuid)", "cannot create"),
    ("varchar, injected bigint", "not a Trino type"), ("decimal(3,12)", "scale exceeds"), ("varchar(10)", "not a Trino type"),
])
def test_spark_types_outside_the_grammar_are_refused(trino: str, why: str) -> None:
    with pytest.raises(ValueError, match=re.escape(why)):
        lake._spark_type(trino, "t")


@pytest.mark.parametrize(("field", "spark"), [
    ("read_at", '"read_at"'), ("year(read_at)", 'years("read_at")'), ("day(read_at)", 'days("read_at")'),
    ("hour(read_at)", 'hours("read_at")'), ("bucket(station_id, 16)", 'bucket(16, "station_id")'),
    ("truncate(region,4)", 'truncate(4, "region")'),
])
def test_partition_fields_in_sparks_spelling(field: str, spark: str) -> None:
    assert lake._spark_partition(field, "t") == spark


@pytest.mark.parametrize("field", ["days(read_at)", "bucket(16, station_id)", 'day("read_at")', "read_at) --", "Read_at"])
def test_partition_fields_outside_the_schema_are_refused(field: str) -> None:
    with pytest.raises(ValueError, match="partition field"):
        lake._spark_partition(field, "t")


def test_a_sorted_connector_table_is_refused_not_dropped() -> None:
    docs = edited(good(), BRONZE, lambda d: d["customProperties"].append({"property": "sort", "value": ["station_id"]}))
    with pytest.raises(ValueError, match="write order"):
        lake.emit(load(docs))


def test_a_table_named_like_a_platform_table_is_refused_not_overwritten() -> None:
    # the schema takes the name; the documents check refuses it (docs.table-name-reserved)
    docs = renamed(good(), {"bronze_readings": model.LANDING_FILES})
    with pytest.raises(ValueError, match="emitted twice|two tables in one place"):
        lake.emit(load(docs))


def test_a_kafka_connector_lands_in_a_table_and_a_fetch_kind_in_objects() -> None:
    m = load(good())
    kafka = dataclasses.replace(m, products=tuple(
        dataclasses.replace(p, connector=model.Connector("kafka", {})) if p.connector else p for p in m.products))
    fetched, streamed = lake.emit(m), lake.emit(kafka)
    landing_files = model.ddl_path("spark", "k2", "station_readings", model.LANDING_FILES)
    landing = model.ddl_path("spark", "k2", "station_readings", model.KAFKA_LANDING)
    assert landing_files in fetched and landing not in fetched
    assert landing in streamed and landing_files not in streamed
    assert '"record_value" BINARY' in streamed[landing]
    assert '"prefixes": []' in streamed[model.STORAGE_PLAN] and '"kind": "landing"' not in streamed[model.BACKUP_SET]


def test_a_contract_platform_table_is_refused_until_its_ddl_is_decided() -> None:
    m = load(good())
    m = dataclasses.replace(m, products=tuple(dataclasses.replace(p, tables=tuple(
        dataclasses.replace(t, build=model.Build("platform", None)) if t.name == "gold_daily_load" else t for t in p.tables))
        for p in m.products))
    with pytest.raises(ValueError, match="platform table"):
        lake.emit(m)


# --- the run record, the plans, purity -------------------------------------------------------


def with_governance(documents: dict[str, bytes]) -> dict[str, bytes]:
    return edited(documents, model.DOMAINS, lambda d: d["domains"].update(
        governance={"owner": "platform-team", "kind": "consumer-aligned", "description": "Made up for the test."}))


def test_the_run_record_is_copied_byte_for_byte_when_its_domain_is_declared() -> None:
    path = model.ddl_path("trino", "governance", "run_records", "run_record")
    assert path not in lake.emit(load(good()))
    out = lake.emit(load(with_governance(good())))
    assert out[path].encode() == model.RUN_RECORD_SQL.read_bytes()
    assert '"governance.run_records.run_record": 7' in out[model.RETENTION]
    assert '"table": "governance.run_records.run_record"' in out[model.BACKUP_SET]
    assert model.need_of(path) == "ddl"


def test_the_storage_plan_lists_every_domain_and_both_namespaces_of_each_built_product() -> None:
    plan = yaml.safe_load(lake.emit(load(with_governance(good())))[model.STORAGE_PLAN])
    assert plan == {
        "warehouses": [
            {"name": "governance", "remoteSigningEnabled": False, "namespaces": ["run_records"]},
            {"name": "k2", "remoteSigningEnabled": False, "namespaces": ["station_readings"]},
            {"name": "ops_insight", "remoteSigningEnabled": False, "namespaces": ["daily_load", "daily_load__base"]},
            {"name": "sensor_net", "remoteSigningEnabled": False, "namespaces": ["station_readings", "station_readings__base"]},
        ],
        "landing": {"bucket": "k2-landing", "prefixes": ["sensor-net/station-readings/"]},
    }


def test_retention_is_the_contracts_else_the_floor() -> None:
    docs = edited(good(), SILVER, lambda d: prop(d, "retention").update(value=30))
    retention = yaml.safe_load(lake.emit(load(docs))[model.RETENTION])
    assert retention["sensor_net.station_readings__base.silver_readings"] == 30
    assert retention["ops_insight.daily_load__base.gold_daily_load"] == 7  # its contract states none


def test_output_is_deterministic_and_reads_no_clock_environment_or_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = load(good())
    out = lake.emit(m)
    # products and tables in another order, and another commit and approval time: the same files
    shuffled = dataclasses.replace(m, products=tuple(dataclasses.replace(p, tables=p.tables[::-1]) for p in m.products[::-1]),
                                   provenance=dataclasses.replace(m.provenance, commit="f" * 40, tagged_at="2031-06-01T12:00:00Z"))
    assert lake.emit(shuffled) == out
    canary = "zz-s15-canary-secret-0123456789"
    (tmp_path / "principals").mkdir()
    (tmp_path / "principals" / "pub_station_readings.password").write_text(canary)
    for name in ("K2_SECRETS", "MINIO_ROOT_PASSWORD", "K2_TRINO_S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, str(tmp_path) if name == "K2_SECRETS" else canary)
    assert lake.emit(m) == out
    for path, text in out.items():
        assert canary not in text and "${" not in text and "ENV:" not in text, path
        assert not re.search(r"(?i)password|secret.?key|access.?key|minio", text), path
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}", text), path


def regenerate() -> None:
    for root, documents in ((GOLDEN, good()), (REPOSITORY, repository_set())):
        shutil.rmtree(root / "compiled", ignore_errors=True)
        for path, text in lake.emit(load(documents)).items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)


if __name__ == "__main__":
    regenerate()
