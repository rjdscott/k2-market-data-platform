"""emit/trino_rules.py (S12): rules.json and groups.txt of the good fixture set and of the repository's
documents, each held to its golden files (golden/trino_rules/ and golden/trino_rules/repository/;
groups.txt as groups.yaml, the parsed map), then attacked by properties over random valid mutations
of the good set: no reader rule reaches a base or a `$` table, every reader rule names one
relation, every policy is a rule, nobody but a publisher holds GRANT_SELECT, and every regex in
every rule matches exactly the names it lists, so a product whose name extends another's, or a user
an unescaped `.` would let in, gets nothing.

K2GEN_UPDATE_GOLDEN=1 rewrites this emitter's golden files and nothing else; review the diff as code.
"""

import dataclasses
import json
import os
import random
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_emit_trino_etc import SETS

from k2gen import model
from k2gen.emit import trino_rules
from k2gen.model import Grant, Model, Principal, Product, RolePolicy, Table

GOLDEN = Path(__file__).parent / "golden" / "trino_rules"
ROOTS = {"good": GOLDEN, "repository": GOLDEN / "repository"}
META = ["snapshots", "files", "history", "partitions", "refs", "manifests", "all_manifests", "properties",
        "metadata_log_entries", "entries", "all_entries"]
NAME_FIELDS = ("user", "group", "catalog", "schema", "table", "property", "procedure", "original_user", "new_user")


def loaded(name: str) -> Model:
    return model.load(SETS[name][0]())


def emitted(m: Model) -> tuple[dict[str, Any], dict[str, list[str]]]:
    out = trino_rules.emit(m)
    assert set(out) == {trino_rules.RULES, trino_rules.GROUPS}
    groups = {}
    for line in out[trino_rules.GROUPS].splitlines():
        g, _, members = line.partition(":")
        assert g not in groups and members, line
        groups[g] = members.split(",")
    return json.loads(out[trino_rules.RULES]), groups


@pytest.mark.parametrize("name", SETS)
def test_each_set_compiles_to_its_golden_files(name: str) -> None:
    out, root = trino_rules.emit(loaded(name)), ROOTS[name]
    _, groups = emitted(loaded(name))
    # The release canary's name is the set's hash, which moves with ANY document edit (the compose
    # file included): the golden holds a fixed name in its place, and the name's shape is asserted.
    canaries = set(re.findall(r"k2_release_[0-9a-f]{12}", out[trino_rules.RULES]))
    assert len(canaries) == 1, canaries
    rules = out[trino_rules.RULES].replace(canaries.pop(), "k2_release_000000000000")
    if os.environ.get("K2GEN_UPDATE_GOLDEN"):
        (root / trino_rules.RULES).parent.mkdir(parents=True, exist_ok=True)
        (root / trino_rules.RULES).write_text(rules)
        (root / "groups.yaml").write_text(yaml.safe_dump(groups, sort_keys=True))
    assert rules == (root / trino_rules.RULES).read_text()
    assert groups == yaml.safe_load((root / "groups.yaml").read_text())
    assert out == trino_rules.emit(loaded(name))  # deterministic
    assert all(model.need_of(p) is None for p in out)  # read through the pointer every 5 s


def test_the_good_sets_readers_and_policies_by_name() -> None:
    rules, groups = emitted(loaded("good"))
    assert groups == {"daily_load__full_reader": ["svc.load-dashboard"],
                      "station_readings__delayed_reader": ["p.analyst-delayed"],
                      "station_readings__full_reader": ["p.analyst-full"]}
    (delayed,) = [r for r in rules["tables"] if r.get("group") == "station_readings__delayed_reader"]
    assert delayed == {
        "group": "station_readings__delayed_reader", "catalog": "sensor_net", "schema": "station_readings",
        "table": "silver_readings", "privileges": ["SELECT"],
        "filter": "(\"region\" IN ('north', 'south')) AND (\"read_at\" < current_timestamp - INTERVAL '20' MINUTE)",
        "columns": [{"name": "operator_note", "mask": "CAST(NULL AS varchar)"}]}
    (full,) = [r for r in rules["tables"] if r.get("group") == "station_readings__full_reader"]
    assert "filter" not in full and "columns" not in full


@pytest.mark.parametrize("name", SETS)
def test_default_deny_no_section_ends_in_an_allow_for_everyone(name: str) -> None:
    rules, _ = emitted(loaded(name))
    assert set(rules) == {"catalogs", "schemas", "tables", "system_session_properties", "catalog_session_properties",
                          "queries", "impersonation", "system_information", "procedures"}
    assert "functions" not in rules and "authorization" not in rules  # only system.builtin; SET AUTHORIZATION refused
    for section, items in rules.items():
        for rule in items:
            who = {"user", "group", "original_user"} & rule.keys()
            # The one rule that names nobody is a refusal: `system`, which Trino otherwise grants to all.
            assert who or rule == {"catalog": "system", "allow": "none"}, (section, rule)
    assert rules["catalogs"][-1] == {"catalog": "system", "allow": "none"}
    assert [r for r in rules["queries"] if "view" in r["allow"] or "kill" in r["allow"]] == [
        {"user": "svc\\.platform", "allow": ["execute", "kill", "view"]}]
    assert rules["system_information"] == [{"user": "svc\\.platform", "allow": ["read"]}]
    assert rules["procedures"] == [{"user": "svc\\.platform", "catalog": "system", "schema": "runtime",
                                    "procedure": "kill_query", "privileges": ["EXECUTE"]}]


def test_system_runtime_and_the_query_history_are_the_operators_alone() -> None:
    rules, _ = emitted(loaded("good"))
    on_system = [r for r in rules["tables"] if r["catalog"] == "system"]
    for r in on_system:
        reaches_runtime = "schema" not in r or re.fullmatch(r["schema"], "runtime")
        assert not reaches_runtime or r == {"user": "svc\\.platform", "catalog": "system", "privileges": ["SELECT"]}, r
    assert {r["schema"] for r in on_system if "schema" in r} == {"jdbc", "metadata"}


@pytest.mark.parametrize("name", SETS)
def test_session_properties_statistics_only_for_pipeline_principals(name: str) -> None:
    m = loaded(name)
    rules, _ = emitted(m)
    session = rules["catalog_session_properties"]
    assert session[0] == {"user": "svc\\.platform", "allow": True}
    for r in session[1:]:
        assert r["property"] == model.STATISTICS_PROPERTY and r["allow"] is True
        for user in unescape(r["user"]):
            assert any(re.fullmatch(p, user) for p in model.PIPELINE_PRINCIPALS), user
    (system,) = rules["system_session_properties"]
    assert set(unescape(system["user"])) == set(trino_rules.pipeline_principals(m))
    for user in ("svc.api", "p.analyst-full", "p.canary-full", model.canary_user(m.provenance.set_sha256)):
        assert not any(re.fullmatch(r["user"], user) for r in session + [system])


@pytest.mark.parametrize("name", SETS)
def test_svc_api_impersonates_the_declared_people_and_nobody_else(name: str) -> None:
    m = loaded(name)
    rules, _ = emitted(m)
    (rule,) = rules["impersonation"]
    assert rule["original_user"] == "svc\\.api" and rule["allow"] is True
    people = {p.id for p in m.principals if p.id.startswith("p.")}
    assert set(unescape(rule["new_user"])) == people
    for u in trino_rules.known_principals(m):
        assert bool(re.fullmatch(rule["new_user"], u)) == (u in people), u


@pytest.mark.parametrize("name", SETS)
def test_the_canary_executes_and_reaches_nothing(name: str) -> None:
    m = loaded(name)
    rules, _ = emitted(m)
    canary = model.canary_user(m.provenance.set_sha256)
    assert any(re.fullmatch(r["user"], canary) and r["allow"] == ["execute"] for r in rules["queries"])
    others = [r for s, rs in rules.items() if s != "queries" for r in rs if "user" in r]
    assert not [r for r in others if re.fullmatch(r["user"], canary)]
    assert not any(re.fullmatch(r["user"], "k2_release_000000000000") for r in rules["queries"])


def test_the_repository_set_has_the_run_record_rules() -> None:
    rules, _ = emitted(loaded("repository"))
    rr = [r for r in rules["tables"] if r.get("table") == "run_record"]
    assert rr == [
        {"user": "svc\\.platform", "catalog": "governance_build", "schema": "run_records", "table": "run_record", "privileges": ["SELECT", "OWNERSHIP"]},
        {"user": "svc\\.runs", "catalog": "governance_build", "schema": "run_records", "table": "run_record", "privileges": ["SELECT", "INSERT"]},
        {"user": "svc\\.catalog", "catalog": "governance", "schema": "run_records", "table": "run_record", "privileges": ["SELECT"]}]


# ---- fail closed ---------------------------------------------------------------------------------


def edit_table(m: Model, product: str, change: Any) -> Model:
    products = tuple(dataclasses.replace(p, tables=tuple(change(t) for t in p.tables)) if p.name == product else p for p in m.products)
    return dataclasses.replace(m, products=products)


def edit_grants(m: Model, product: str, grants: tuple[Grant, ...]) -> Model:
    return dataclasses.replace(m, products=tuple(dataclasses.replace(p, grants=grants) if p.name == product else p for p in m.products))


@pytest.mark.parametrize(("change", "message"), [
    (lambda m: edit_grants(m, "station-readings", (Grant("p.analyst-full", "full_reader"), Grant("p.analyst-full", "delayed_reader"))), "two roles"),
    (lambda m: edit_grants(m, "station-readings", (Grant("svc.api", "full_reader"),)), "platform principal"),
    (lambda m: edit_grants(m, "station-readings", (Grant("dbt_daily_load", "full_reader"),)), "platform principal"),
    (lambda m: edit_grants(m, "station-readings", (Grant("svc.platform", "full_reader"),)), "platform principal"),
    (lambda m: edit_grants(m, "station-readings", (Grant("k2_release_0123456789ab", "full_reader"),)), "platform principal"),
    (lambda m: edit_table(m, "station-readings", lambda t: dataclasses.replace(
        t, policies={**t.policies, "public_reader": RolePolicy(None, ("region",), None)}) if t.name == "silver_readings" else t), "does not offer"),
    (lambda m: edit_table(m, "station-readings", lambda t: dataclasses.replace(
        t, policies={"delayed_reader": RolePolicy(None, ("no_such",), None)}) if t.name == "silver_readings" else t), "mask names"),
    (lambda m: edit_table(m, "station-readings", lambda t: dataclasses.replace(
        t, policies={"delayed_reader": RolePolicy(model.Delay("no_such", 5), (), None)}) if t.name == "silver_readings" else t), "delay column"),
    (lambda m: edit_table(m, "station-readings", lambda t: dataclasses.replace(
        t, policies={"delayed_reader": RolePolicy(None, (), model.RowScope("no_such", ("a",)))}) if t.name == "silver_readings" else t), "rowScope column"),
    (lambda m: dataclasses.replace(m, products=tuple(p for p in m.products if p.name != "station-readings")), "no live product's lake table"),
])
def test_what_the_documents_leave_inconsistent_fails_closed(change: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        trino_rules.emit(change(loaded("good")))


def test_a_policy_value_is_one_literal_and_a_name_one_identifier() -> None:
    attack = "north') OR (1=1"
    m = edit_table(loaded("good"), "station-readings", lambda t: dataclasses.replace(
        t, policies={"delayed_reader": RolePolicy(model.Delay("read_at", 20), (), model.RowScope("region", (attack, 7)))})
        if t.name == "silver_readings" else t)
    rules, _ = emitted(m)
    (rule,) = [r for r in rules["tables"] if r.get("group") == "station_readings__delayed_reader"]
    assert rule["filter"] == ("(\"region\" IN ('north'') OR (1=1', 7)) AND "
                              "(\"read_at\" < current_timestamp - INTERVAL '20' MINUTE)")


# ---- properties over random valid mutations of the good set --------------------------------------

WORDS = ["a", "b", "station", "readings", "x", "y", "net", "load", "daily", "z9", "q"]


def slug(rng: random.Random) -> str:
    return "-".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))


def rename(p: Product, name: str, domain: str) -> Product:
    """The product under another name and domain: ids, contract ids and grants follow; its input
    ports stay, so the copy reads what the original reads."""
    def table(t: Table) -> Table:
        return dataclasses.replace(t, contract_id=f"{domain}.{name}.{t.name}", physical_name=f"{model.fence(name)}.{t.name}",
                                   path=f"contracts/{domain}/{name}/{t.name}.odcs.yaml")
    return dataclasses.replace(p, name=name, id=f"{domain}.{name}", domain=domain, path=f"contracts/{domain}/{name}",
                               tables=tuple(table(t) for t in p.tables))


STATUSES = ("active", "paused", "deprecated", "retired")


def mutations(n: int = 150) -> Iterator[Model]:
    """Valid models from the good set: products copied under random names, among them names that
    extend another product's (`station-readings` and `station-readings-x`), into random domains,
    with random policies (a delay only on a trino_rules.DELAY_TYPE column), random grants to
    random people, and a random status each (never retired while a live product reads it)."""
    base = loaded("good")
    for seed in range(n):
        rng = random.Random(seed)
        domains = sorted({d.name for d in base.domains} | {slug(rng) for _ in range(rng.randint(0, 2))})
        products: dict[str, Product] = {p.name: p for p in base.products}
        for _ in range(rng.randint(1, 4)):
            src = rng.choice(list(products.values()))
            name = rng.choice([slug(rng), src.name + "-" + rng.choice(WORDS), src.name.rsplit("-", 1)[0]])
            if name in products or model.product_name_problem(name) or model.fence(name) in {model.fence(x) for x in products}:
                continue
            products[name] = rename(src, name, rng.choice(domains))
        people = sorted({f"p.{slug(rng)}" for _ in range(rng.randint(1, 5))})
        out = []
        for p in products.values():
            tables = []
            for t in p.tables:
                roles = tuple(r for r in t.roles if rng.random() < 0.8) or t.roles
                cols = [c.name for c in t.columns]
                times = [c.name for c in t.columns if c.physical_type == trino_rules.DELAY_TYPE]
                policies = {}
                for r in roles:
                    if rng.random() < 0.6:
                        policies[r.role] = RolePolicy(
                            model.Delay(rng.choice(times), rng.randint(1, 600)) if times and rng.random() < 0.5 else None,
                            tuple(rng.sample(cols, rng.randint(0, 2))),
                            model.RowScope(rng.choice(cols), tuple(rng.sample(["north", "south", "east", 3], 2))) if rng.random() < 0.5 else None)
                tables.append(dataclasses.replace(t, roles=roles, policies=policies))
            offered = sorted({r.role for t in tables for r in t.roles})
            grants = tuple(Grant(u, rng.choice(offered)) for u in people if rng.random() < 0.5)
            out.append(dataclasses.replace(p, tables=tuple(tables), grants=grants, status=rng.choice(STATUSES)))
        read = {port.contract_id.rsplit(".", 1)[0] for p in out if p.status != "retired" for port in p.input_ports}
        out = [dataclasses.replace(p, status="deprecated") if p.status == "retired" and p.id in read else p for p in out]
        principals = tuple(base.principals) + tuple(Principal(u, "person", "team", None) for u in people
                                                    if u not in {x.id for x in base.principals})
        yield dataclasses.replace(base, products=tuple(out), principals=principals,
                                  domains=tuple(model.Domain(d, "team", "source-aligned", "made up") for d in domains))


def unescape(pattern: str) -> list[str]:
    return [re.sub(r"\\(.)", r"\1", part) for part in pattern.split("|")]


def universe(m: Model) -> set[str]:
    """Every name a rule could be asked about, and near misses of each: an extension, a prefix, a
    `.` swapped for `x`, every `$` metadata table."""
    names = {"lake", "system", "runtime", "metadata", "jdbc", "information_schema", "run_records", "run_record",
             "svc.platform", "svc.api", "svc.runs", "svc.catalog", model.canary_user(m.provenance.set_sha256)}
    names |= set(trino_rules.known_principals(m)) | {p.id for p in m.principals}
    for d in m.domains:
        names |= {model.reader_catalog(d.name), model.build_catalog(d.name)}
    for p in m.products:
        names |= {model.reader_namespace(p.name), model.base_namespace(p.name), model.builder(p.name), model.publisher(p.name)}
        names |= {model.group(p.name, r) for r in ("full_reader", "delayed_reader", "public_reader")}
        for t in p.tables:
            names |= {t.name} | {f"{t.name}${x}" for x in META}
    near = set()
    for n in names:
        near |= {n + "x", n + "_x", n[:-1], n.replace(".", "x"), "x" + n, n + "$files"}
    return names | near


def name_rules(rules: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any], str, str]]:
    for section, items in rules.items():
        for rule in items:
            for field in NAME_FIELDS:
                if field in rule:
                    yield section, rule, field, rule[field]


# The rules that leave `table` (or `schema` and `table`) out on purpose, so match every table there:
# the operator's `system` and its base maintenance, a builder in its own base namespace (dbt's
# temporary tables have names no document gives). Each is checked to be exactly one of these.
def wide(rule: dict[str, Any]) -> bool:
    return "table" not in rule


def allowed_wide(rule: dict[str, Any], m: Model) -> bool:
    if rule.get("user") == "svc\\.platform" and rule.get("catalog") == "system" and set(rule) == {"user", "catalog", "privileges"}:
        return True
    bases = {model.base_namespace(p.name) for p in m.products}
    builders = {model.builder(p.name): model.base_namespace(p.name) for p in m.products}
    if rule.get("user") == "svc\\.platform" and set(unescape(rule.get("schema", ".*"))) <= bases:
        return rule["privileges"] == ["SELECT"]
    user = unescape(rule.get("user", ""))
    return len(user) == 1 and user[0] in builders and rule.get("schema") == re.escape(builders[user[0]]) \
        and "GRANT_SELECT" not in rule["privileges"]


@pytest.mark.parametrize("m", list(mutations()), ids=lambda m: f"{len(m.products)}p")
def test_properties_hold_over_random_valid_sets(m: Model) -> None:
    rules, groups = emitted(m)
    names = universe(m)
    readers = [r for r in rules["tables"] if "group" in r and r["catalog"] != "system"]
    offered = {(model.group(p.name, r.role), model.reader_relation(t)): (p, t, r.role)
               for p in m.products if p.status != "retired" for t in p.tables if t.build.kind in model.LAKE_BUILDS for r in t.roles}

    # Every regex is an alternation of exact, escaped names and matches exactly those names.
    for section, _rule, field, pattern in name_rules(rules):
        parts = unescape(pattern)
        assert "|".join(re.escape(x) for x in parts) == pattern, (section, field, pattern)
        assert {n for n in names if re.fullmatch(pattern, n)} == set(parts) & names, (section, field, pattern)
    for rule in rules["tables"]:
        assert not wide(rule) or allowed_wide(rule, m) or rule["catalog"] == "system", rule

    # No reader rule names a base namespace or a `$` table; each names one relation, one it is offered.
    for r in readers:
        assert "|" not in r["catalog"] + r["schema"] + r["table"] + r["group"], r
        assert not r["schema"].endswith(model.BASE_SUFFIX) and "$" not in unescape(r["table"])[0], r
        assert (r["group"], f"{r['catalog']}.{r['schema']}.{r['table']}") in offered, r
        assert r["privileges"] == ["SELECT"]
    # Every (table, role) offered is a rule, carrying exactly that role's policy.
    for (g, relation), (_p, t, role) in offered.items():
        (rule,) = [r for r in readers if r["group"] == g and f"{r['catalog']}.{r['schema']}.{r['table']}" == relation]
        policy = t.policies.get(role, RolePolicy(None, (), None))
        assert sorted(c["name"] for c in rule.get("columns", ())) == sorted(set(policy.mask))
        types = {c.name: c.physical_type for c in t.columns}
        assert all(c["mask"] == f"CAST(NULL AS {types[c['name']]})" for c in rule.get("columns", ()))
        where = rule.get("filter", "")
        assert (policy.delay is None) == ("current_timestamp - INTERVAL" not in where)
        assert (policy.row_scope is None) == (" IN (" not in where)
        if policy.delay:
            assert f"{model.quote_ident(policy.delay.column)} < current_timestamp - INTERVAL '{policy.delay.minutes}' MINUTE" in where
        if policy.row_scope:
            assert f"{model.quote_ident(policy.row_scope.column)} IN ({', '.join(model.sql_literal(v) for v in policy.row_scope.values)})" in where
    # Group members are exactly the grants.
    for g, members in groups.items():
        assert set(members) == {gr.principal for p in m.products for gr in p.grants if model.group(p.name, gr.role) == g}

    # GRANT_SELECT: publishers alone, on their own base tables, through their reader catalog.
    for r in rules["tables"]:
        if "GRANT_SELECT" in r["privileges"]:
            (pub,) = unescape(r["user"])
            (p,) = [p for p in m.products if model.publisher(p.name) == pub]
            assert "group" not in r and r["catalog"] == model.reader_catalog(p.domain) and r["schema"] == model.base_namespace(p.name)
            names = {t.name for t in p.tables if t.build.kind in ("dbt", "code")}
            assert set(unescape(r["table"])) == names | {f"{n}$refs" for n in names}  # each view's guard reads $refs
    # Catalog access `all` on a reader catalog: that domain's publishers alone.
    for r in rules["catalogs"]:
        if r.get("allow") == "all" and not r["catalog"].endswith("_build") and r["catalog"] not in ("system", "lake"):
            assert set(unescape(r["user"])) == {model.publisher(p.name) for p in m.products if p.status != "retired"
                                                and p.domain in {d.name for d in m.domains if model.reader_catalog(d.name) == r["catalog"]}
                                                and any(t.build.kind in ("dbt", "code") for t in p.tables)}, r
        if r.get("catalog", "").endswith("_build"):
            assert "group" not in r and r["allow"] == "all", r  # never a reader
    # svc.platform holds nothing in a dbt or code product's reader namespace, in any catalog.
    reader_ns = {model.reader_namespace(p.name) for p in m.products if any(t.build.kind in ("dbt", "code") for t in p.tables)}
    for r in rules["tables"] + rules["schemas"]:
        if r.get("user") == "svc\\.platform" and r.get("catalog") != "system":
            assert "schema" in r and not set(unescape(r["schema"])) & reader_ns, r
    # A builder reads others only through read-only catalogs, by exact name, never in a `_build`.
    for p in m.products:
        dbt = re.escape(model.builder(p.name))
        for r in rules["tables"]:
            if r.get("user") == dbt and r["catalog"].endswith("_build"):
                assert r["catalog"] == model.build_catalog(p.domain) and r["schema"] == model.base_namespace(p.name), r

    # Status (ADR-055 part 4): a retired product is named by no rule and no group; a paused one
    # keeps its readers and publisher and has no builder; the others have both.
    every = [(sec, r) for sec, rs in rules.items() for r in rs]
    for p in m.products:
        built = any(t.build.kind in ("dbt", "code") for t in p.tables)
        mine = {model.builder(p.name), model.publisher(p.name), model.base_namespace(p.name), model.reader_namespace(p.name),
                *(model.group(p.name, r) for r in ("full_reader", "delayed_reader", "public_reader"))}
        named = {n for _, r in every for f in NAME_FIELDS if f in r for n in unescape(r[f])}
        if p.status == "retired":
            assert not named & mine and not {g for g in groups if g in mine}, (p.id, named & mine)
            continue
        assert (model.builder(p.name) in trino_rules.known_principals(m)) == (built and p.status != "paused"), p.id
        assert (model.publisher(p.name) in trino_rules.known_principals(m)) == built, p.id
        for t in p.tables:
            if t.build.kind in model.LAKE_BUILDS and t.roles:
                assert any(r.get("group") == re.escape(model.group(p.name, t.roles[0].role)) for _, r in every), (p.id, t.name)


@pytest.mark.parametrize("m", list(mutations(60)), ids=lambda m: f"{len(m.products)}p")
def test_each_view_reads_a_guard_its_owner_alone_may_pass_on(m: Model) -> None:
    """A base swapped for a view (C1), as far as the emitters can say: every view the lake emitter
    lists guards on its own base's `$refs`, which its publisher, and no other principal of a
    product, may pass on; the publisher holds GRANT_SELECT on exactly the base and `$refs`."""
    from k2gen.emit import lake
    rules, _ = emitted(m)
    out = lake.emit(m)
    live = {p.name for p in m.products if p.status != "retired"}
    for v in json.loads(out[model.VIEWS])["views"]:
        sql = out[model.view_sql_path(v["relation"])]
        guard = model.quote_relation(v["base"] + "$refs")
        assert f"FROM {model.quote_relation(v['base'])} FOR VERSION AS OF 'audited' WHERE EXISTS (SELECT 1 FROM {guard} " in sql
        if v["owner"].removeprefix("pub_") not in {model.fence(n) for n in live}:
            continue
        catalog, schema, table = v["base"].split(".")
        on = [r for r in rules["tables"] if r["catalog"] == catalog and re.fullmatch(r.get("schema", ".*"), schema)
              and re.fullmatch(r.get("table", ".*"), table + "$refs")]
        holders = {u for r in on for u in unescape(r.get("user", r.get("group", "")))}
        assert holders <= {v["owner"], *model.REFS_READERS}, holders
        (own,) = [r for r in on if r.get("user") == re.escape(v["owner"])]
        assert own["privileges"] == ["SELECT", "GRANT_SELECT"] and re.fullmatch(own["table"], table)


def bronze_and_delays(n: int = 80) -> Iterator[tuple[Model, str]]:
    """Invalid sets from the valid ones: a connector table offered a role, or a delay put on a
    column of any other type than trino_rules.DELAY_TYPE (timestamp(6), date, varchar...)."""
    for seed, m in enumerate(mutations(n)):
        rng = random.Random(1000 + seed)
        p = rng.choice([p for p in m.products if p.status != "retired"])
        if rng.random() < 0.5 and (bronze := [t for t in p.tables if t.build.kind == "connector"]):
            target, change, message = bronze[0], lambda t: dataclasses.replace(t, roles=(model.TableRole("full_reader", None),)), "connector table"
        else:
            tables = [t for t in p.tables if t.build.kind in model.LAKE_BUILDS and t.roles and t.build.kind != "connector"
                      and [c for c in t.columns if c.physical_type != trino_rules.DELAY_TYPE]]
            if not tables:
                continue
            target = rng.choice(tables)
            column = rng.choice([c for c in target.columns if c.physical_type != trino_rules.DELAY_TYPE])
            role = target.roles[0].role
            change = lambda t, c=column.name, r=role: dataclasses.replace(t, policies={**t.policies, r: RolePolicy(model.Delay(c, 5), (), None)})  # noqa: E731
            message = "delay column"
        products = tuple(dataclasses.replace(x, tables=tuple(change(t) if t is target else t for t in x.tables)) if x is p else x
                         for x in m.products)
        yield dataclasses.replace(m, products=products), message


@pytest.mark.parametrize(("m", "message"), list(bronze_and_delays()), ids=lambda x: x if isinstance(x, str) else f"{len(x.products)}p")
def test_a_bronze_role_or_a_zone_dependent_delay_fails_closed(m: Model, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        trino_rules.emit(m)


def test_a_status_changes_exactly_its_own_products_rules() -> None:
    """station-readings by each status, the rest unchanged: active and deprecated rules are equal;
    paused drops only the builder's rules; retired drops every rule of the product, so daily-load,
    which reads it, fails closed."""
    def at(status: str) -> Model:
        m = loaded("good")
        return dataclasses.replace(m, products=tuple(dataclasses.replace(p, status=status) if p.name == "station-readings" else p
                                                     for p in m.products))
    active, _ = emitted(at("active"))
    assert emitted(at("deprecated"))[0] == active
    paused, groups = emitted(at("paused"))
    gone = [r for s in active for r in active[s] if r not in paused[s]]
    assert gone and all("dbt_station_readings" in json.dumps(r) for r in gone), gone
    assert "dbt_station_readings" not in json.dumps(paused) and "pub_station_readings" in json.dumps(paused)
    assert set(groups) == {"daily_load__full_reader", "station_readings__delayed_reader", "station_readings__full_reader"}
    with pytest.raises(ValueError, match="no live product's lake table"):
        trino_rules.emit(at("retired"))
    m = at("retired")
    m = dataclasses.replace(m, products=tuple(p for p in m.products if p.name != "daily-load") + tuple(
        dataclasses.replace(p, status="retired") for p in m.products if p.name == "daily-load"))
    rules, groups = emitted(m)
    assert not groups and not [r for r in rules["tables"] if "group" in r and r["catalog"] != "system"]
    assert not re.search(r"station_readings|daily_load", json.dumps(rules)), json.dumps(rules)[:400]
