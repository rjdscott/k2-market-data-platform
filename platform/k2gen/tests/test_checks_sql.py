"""k2gen.checks.sql (step S07): the good set is clean; each failing fixture of
tests/fixtures/v2/bad/sql/ yields exactly the code its first line names; the evasions are caught;
the documented limits are pinned at what the lint does today, so a change to them is deliberate.

A fixture's first line is `-- expect: <code>` (`# expect:` in YAML), and an optional `at:` line
places it under the daily-load product; it replaces models/gold_daily_load.sql by default."""

import re
from pathlib import Path

import pytest

from k2gen import model
from k2gen.checks import sql

FIXTURES = Path(__file__).parent / "fixtures" / "v2"
GOOD = FIXTURES / "good"
BAD = sorted((FIXTURES / "bad" / "sql").iterdir())
PRODUCT = "contracts/ops-insight/daily-load"
MODEL = f"{PRODUCT}/models/gold_daily_load.sql"
CODES = {"env-var", "definer-view", "materialized-view", "incremental-predicates", "config-key", "config-unreadable",
         "foreign-read", "time-travel", "write-outside-base", "statement-ddl", "hook", "model-not-sql", "adapter",
         "relation-unmanaged", "as-of"}
WARNINGS = {"sql.adapter", "sql.relation-unmanaged"}
INPUT = "{{ k2_input('station_readings', 'silver_readings') }}"
TABLE = "{{ config(materialized='table') }}\n"


def docset(changes: dict[str, str]) -> model.DocumentSet:
    documents = {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in GOOD.rglob("*") if p.is_file()}
    documents |= {path: text.encode() for path, text in changes.items()}
    provenance = model.Provenance("git", "fixture", "0" * 40, "fixture", "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return model.DocumentSet(documents, provenance)


def findings(text: str, path: str = MODEL) -> list[model.Finding]:
    return sql.check(docset({path: text}), None)


def codes(text: str, path: str = MODEL) -> set[str]:
    return {f.code for f in findings(text, path)}


def test_the_good_set_is_clean() -> None:
    assert sql.check(docset({}), None) == []


def _header(path: Path) -> tuple[str, str]:
    text = path.read_text()
    expect = re.match(r"(?:--|#) expect: (\S+)", text)
    at = re.search(r"^(?:--|#) at: (\S+)", text, re.M)
    assert expect, f"{path.name}: its first line names no code"
    return expect.group(1), f"{PRODUCT}/{at.group(1)}" if at else MODEL


@pytest.mark.parametrize("fixture", BAD, ids=[p.name for p in BAD])
def test_each_failing_fixture_yields_exactly_its_code(fixture: Path) -> None:
    code, path = _header(fixture)
    found = findings(fixture.read_text(), path)
    assert {f.code for f in found} == {code}, [f.message for f in found]
    assert all(f.path == path and re.match(r"line \d+: \S", f.message) for f in found)
    assert {f.severity for f in found} == {"warning" if code in WARNINGS else "error"}


def test_every_code_has_a_failing_fixture() -> None:
    assert {_header(p)[0] for p in BAD} == {f"sql.{c}" for c in CODES}


def test_a_finding_names_its_line() -> None:
    text = f"-- line 1\n{TABLE}select station_id\n  , '{{{{ env_var(\"X\") }}}}' as x\nfrom {INPUT}\n"
    (finding,) = findings(text)
    assert finding.message.startswith("line 4: env_var()")


def test_another_products_model_is_linted_against_its_own_fence() -> None:
    # the same source() is the station-readings product's own table, and daily-load's foreign read
    own = f"{TABLE}select * from {{{{ source('station_readings', 'bronze_readings') }}}}\n"
    assert codes(own, "contracts/sensor-net/station-readings/models/silver_readings.sql") == set()
    assert codes(own) == {"sql.foreign-read"}


CAUGHT = {
    "sql.env-var": [
        "{{env_var('X')}}",
        "{{- env_var ( 'X' ) -}}",
        "-- Jinja renders inside a SQL comment: {{ env_var('X') }}",
        "{% set x = env_var('X') %}",
        "{{ context['env_var']('X') }}",
        "{{ context['env_' ~ 'var']('X') }}",
        "{{ config(materialized='table', post_hook=\"select '{{ env_var('X') }}'\") }}",
    ],
    "sql.time-travel": [
        "select * from {{ this }} for  version\n   as of 1",
        "select * from {{ this }} FOR /* a comment */ VERSION -- another\n AS OF 1",
        "select * from {{ this }} {{ 'FOR VER' ~ 'SION AS OF 1' }}",
        "select * from {{ this }} For TimeStamp As Of timestamp '2026-01-01 00:00:00 UTC'",
        "select * from TABLE(ops_insight.system.table_changes('daily_load__base', 'gold_daily_load', 0, 1))",
    ],
    "sql.foreign-read": [
        'select * from "SENSOR_NET"."STATION_READINGS__BASE"."silver_readings"',
        "select * from {{ 'sensor_net.station_readings__base' ~ '.silver_readings' }}",
        "select * from {{ builtins.source('station_readings', 'silver_readings') }}",
        "select * from {{ source(var('p'), 'silver_readings') }}",
        "select * from {{ ref(var('m')) }}",
        "select * from {{ this }} t, sensor_net.station_readings__base.silver_readings s",
        "select * from {{ this }} where station_id in (select station_id from sensor_net.station_readings.silver_readings)",
        "select * from {{ this }} t left join lake.station_readings.bronze_readings b on true",
        "select * from TABLE(exclude_columns(input => TABLE(sensor_net.station_readings__base.silver_readings), columns => DESCRIPTOR(x)))",
        "select * from {{ target.database }}.station_readings__base.silver_readings",
        "select * from station_readings__base.silver_readings",
        "{{ config(materialized='table', post_hook=\"insert into {{ this }} select * from sensor_net.station_readings__base.silver_readings\") }}",
        "{{ config(materialized='table', post_hook=\"insert into {{ this }} select * from {{ source('station_readings', 'silver_readings') }}\") }}",
    ],
    "sql.definer-view": [
        "{{ config(materialized='view', view_security='definer') }}",
        '{{ config(materialized="view") }}',
        "{{- config(materialized='view') -}}",
    ],
    "sql.config-unreadable": [
        "{{ config (materialized='view') }}",
        "{{ config(materialized='table') }}{{ config(materialized='view') }}",
        "{{ config(materialized='ta' ~ 'ble') }}",
        "{# {{ config(materialized='table') }} #}{{ config (materialized='view') }}",
        "{{ config(materialized='table') ~ '' }}",
        "{% set c = config(materialized='view') %}",
        "{{ config(materialized='table', post_hook='x'",
    ],
    "sql.hook": [
        "{{ config(materialized='table', post_hook=['select 1', 'CREATE OR REPLACE VIEW ops_insight_build.daily_load.gold_daily_load AS SELECT 1 x']) }}",
        "{{ config(materialized='table', post_hook='CrEaTe oR rEpLaCe ViEw ops_insight_build.daily_load.gold_daily_load AS SELECT 1 x') }}",
        "{{ config(materialized='table', post_hook='{{ ddl }}') }}",
        "{{ config(materialized='table', pre_hook={'sql': 'drop table {{ this }}', 'transaction': False}) }}",
        "{{ config(materialized='table', post_hook=\"call ops_insight.system.rollback_to_snapshot('daily_load__base', 'gold_daily_load', 1)\") }}",
        "{{ config(materialized='table', post_hook='set session ops_insight.statistics_enabled = true') }}",
        "{{ config(materialized='table', post_hook='alter view ops_insight_build.daily_load.gold_daily_load set authorization user \"dbt_daily_load\"') }}",
        # model.model_config skips quoted strings, so a hook holding a Jinja call is read and linted
        "{{ config(materialized='table', post_hook=\"{{ var('ddl') }}\") }}",
    ],
    "sql.statement-ddl": [
        "{% set q = 'drop table x' %}{% do run_query(q) %}",
        "{{ run_query('DROP TABLE ' ~ this) }}",
        "{% do adapter.execute('call system.x()') %}",
        "{% do adapter.drop_relation(this) %}",
        "{% do adapter.dispatch('anything')() %}",
        "{% call statement('s', fetch_result=True) %}GRANT SELECT ON {{ this }} TO USER \"p.x\"{% endcall %}",
        "{% call statement('s') %}EXPLAIN ANALYZE DROP TABLE x{% endcall %}",
        "{% call statement('s') %}select 1; drop table x{% endcall %}",
    ],
    "sql.write-outside-base": [
        "{{ config(materialized='table', database='sensor_net_build') }}",
        "{{ config(materialized='table', alias='silver_readings') }}",
        "{{ config(materialized='table', post_hook='delete from sensor_net_build.station_readings__base.silver_readings') }}",
        "{{ config(materialized='table', post_hook='insert into daily_load__base.gold_daily_load select 1') }}",
        "{% do run_query('insert into sensor_net_build.station_readings__base.silver_readings select 1') %}",
        # the product's own tables but the hook's model: written after their tests ran (review F3)
        "{{ config(materialized='table', post_hook='insert into ops_insight_build.daily_load__base.gold_daily_load select 1') }}",
        "{{ config(materialized='table', post_hook='delete from ops_insight_build.daily_load__base.dq_test_results') }}",
        "{{ config(materialized='table', pre_hook=\"merge into {{ ref('daily_load__silver_load') }} t using (select 1 x) s on true when matched then delete\") }}",
        "{% call statement('s') %}update {{ ref('daily_load__silver_load') }} set x = 1{% endcall %}select 1",
        "{% do run_query('delete from ' ~ ref('daily_load__silver_load')) %}",
    ],
    "sql.as-of": [
        f"select * from {INPUT} as h where {{{{ k2_as_of('h.read_at', 'h', 'as_at') }}}}",
        f"select * from {INPUT} as h where {{{{ k2_as_of('h.read_at + interval ''1'' hour', 'h', 'valid') }}}}",
        f"select * from {INPUT} as h where {{{{ k2_as_of('\"h\".\"read_at\"', 'h', 'valid') }}}}",
        f"select * from {INPUT} as h where {{{{ k2_as_of('h.read_at', '', 'valid') }}}}",
        f"select * from {INPUT} as h where {{{{ k2_as_of('h.read_at', 'h') }}}}",
        f"select * from {INPUT} as h where {{{{ k2_as_of(var('t'), 'h', 'valid') }}}}",
        f"select * from {INPUT} as h where {{{{ k2_as_of('h.read_at', 'x', 'valid') }}}}",  # x is bound nowhere
        f"select * from {INPUT} h where {{{{ k2_as_of('h.read_at', 'h', 'known') }}}}",  # known_to is no column
        f"select h.*, {{{{ k2_known_to('station_id') }}}} as known_to from {INPUT} as h",
        f"select h.*, {{{{ k2_known_to([]) }}}} as known_to from {INPUT} as h",
        f"select h.*, {{{{ k2_known_to(['station_id, x']) }}}} as known_to from {INPUT} as h",
    ],
    "sql.config-key": [
        "{{ config(materialized='table', properties={'location': \"'s3://elsewhere/'\"}) }}",
        "{{ config(materialized='table', on_table_exists='drop') }}",
        "{{ config(materialized='snapshot') }}",
        "{{ config(materialized='table', tags=['station_readings']) }}",
    ],
}


@pytest.mark.parametrize(("code", "text"), [(c, t) for c, ts in CAUGHT.items() for t in ts])
def test_an_evasion_is_caught(code: str, text: str) -> None:
    assert code in codes(text), text


@pytest.mark.parametrize("text", [
    "-- from sensor_net.station_readings__base.silver_readings, in a comment\nselect * from {{ this }}",
    "select 'for version as of 1' as note from {{ this }}",
    "select extract(hour from load_date) as h, trim(both ' ' from station_id) as s from {{ this }} where a is distinct from b",
    "with recent as (select * from {{ this }}), older (d) as (select 1) select * from recent, older",
    "{# {{ env_var('X') }} #}select 1",
    "{{ config(materialized='view', view_security='invoker') }}select 1",
    "{{ config(materialized='ephemeral') }}select 1",
    "{{ config(materialized='incremental', incremental_strategy='merge', unique_key='id', on_schema_change='fail') }}select 1",
    "{{ config(materialized='table', post_hook=\"delete from {{ this }} where load_date < current_date - interval '400' day\") }}",
    "{% set rows = run_query('select max(load_date) from ' ~ this) %}select 1",
    "{% call statement('m', fetch_result=True) %}select max(load_date) from {{ this }}{% endcall %}select 1",
    "select * from {{ ref('daily_load__gold_daily_load') }}",
    "{{ config(materialized='table') }}select config.get('materialized')",
    # k2_as_of over a CTE, a subquery or the model's own table: the lint does not follow their columns
    f"with k as (select h.*, {{{{ k2_known_to(['station_id']) }}}} as known_to from {INPUT} as h) "
    f"select * from {{{{ this }}}} as f join k on k.station_id = f.station_id and {{{{ k2_as_of('f.load_date', 'k', 'known') }}}}",
    f"select * from {{{{ this }}}} as f join (select * from {INPUT}) as v on {{{{ k2_as_of('f.load_date', 'v', 'valid') }}}}",
    "select * from {{ this }} f, {{ this }} v where {{ k2_as_of('load_date', 'v', 'valid') }}",
])


def test_no_false_positive(text: str) -> None:
    assert codes(text) == set(), [f.message for f in findings(text)]


def test_as_of_over_a_history_input_is_clean_and_renders_no_relation() -> None:
    # station_readings gains a type-2 table in this docset only: valid over it is clean, known is not
    history = "contracts/sensor-net/station-readings/station_history.odcs.yaml"
    contract = (GOOD / "contracts/sensor-net/station-readings/silver_readings.odcs.yaml").read_text()
    contract = contract.replace("silver_readings", "station_history").replace("    properties:\n", (
        "    properties:\n      - {name: valid_from, physicalType: timestamp(6) with time zone}\n"
        "      - {name: valid_to, physicalType: timestamp(6) with time zone}\n"), 1)
    text = (f"{TABLE}select d.station_id, h.station_id as s from {INPUT} as d\n"
            "left join {{ k2_input('station_readings', 'station_history') }} as h\n"
            "  on h.station_id = d.station_id and {{ k2_as_of('d.read_at', 'h', 'valid') }}\n")
    docs = docset({MODEL: text, history: contract})
    assert sql.check(docs, None) == []
    assert {f.code for f in sql.check(docset({MODEL: text.replace("'valid'", "'known'"), history: contract}), None)} == {"sql.as-of"}
    # model_reads sees two inputs and nothing opaque, so checks.access does not widen to every port
    assert sql.model_reads(text) == ([("station_readings", "silver_readings"), ("station_readings", "station_history")], [], [])


@pytest.mark.parametrize(("text", "now"), [
    # a name assembled through a variable: Jinja would have to run
    ("{% set v = 'VERSION' %}select * from {{ this }} FOR {{ v }} AS OF 1", set()),
    ("{% set n = 'env_' %}select {{ context[n ~ 'var']('X') }}", set()),
    # a relation object or a variable in FROM: a warning, not a foreign read
    ("select * from {{ api.Relation.create(database='sensor_net', schema='station_readings__base', identifier='silver_readings') }}",
     {"sql.relation-unmanaged"}),
    ("select * from {{ var('t') }}", {"sql.relation-unmanaged"}),
    # a macro's body is linted where it stands, so a FROM its caller adds is not followed
    ("{% macro m() %}sensor_net.station_readings__base.silver_readings{% endmacro %}select * from {{ m() }}",
     {"sql.relation-unmanaged"}),
    # raw is linted as Jinja: flagged, though dbt would not run it
    ("{% raw %}{{ env_var('X') }}{% endraw %}select 1", {"sql.env-var"}),
    # no materialized: the compiled project's default decides (S17)
    ("select 1", set()),
])
def test_a_documented_limit_is_pinned(text: str, now: set[str]) -> None:
    assert codes(text) == now


def test_hook_keys_in_any_yaml_of_a_product_are_found() -> None:
    for key in ("pre-hook", "+post-hook", "on_run_end", "post_hook"):
        assert codes(f"x:\n  {key}: select 1\n", f"{PRODUCT}/extra.yml") == {"sql.hook"}, key
    assert codes("x: 1\n", f"{PRODUCT}/seeds/notes.yml") == set()  # seeds are data
