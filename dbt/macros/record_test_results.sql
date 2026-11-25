{#
  dbt_project.yml's on-run-end hook (ADR-046): every data test and unit test this dbt
  invocation ran, one row each, into dq.dbt_test_results of the writer's own catalog (the
  profile's database, dbt_<catalog>'s), as that writer. Schema and table are created if
  absent; an invocation that ran no test (dbt run, dbt source freshness, a model with no
  tests) writes nothing.

  dbt runs on-run-end after the nodes whatever their result, so a failed, warned, errored or
  skipped test of the invocation gets its row too (make test-dbt fails a source test on
  purpose and reads the row back). A test outside the invocation gets none: under Airflow each
  node is its own invocation, and a node whose upstream task failed is upstream_failed and
  runs no dbt at all. If the hook itself fails, dbt exits non-zero although every test passed
  (docs/runbooks/dbt-marts.md), and that invocation's rows are lost.

  Columns: invocation_id and run_started_at, dbt's own; writer, the profile's user, so a row
  says who asserted it (rules.json lets only dbt_<catalog> insert here); unique_id and
  test_name, the test's;
  tested, the relation of the model or source it tests (lake.gold.trades,
  market_analytics.candles.ohlcv_1m), NULL for a test that reads neither; status as dbt
  reports it (pass, fail, warn, error, skipped); failures, the failing rows dbt counted
  (1 for a failed unit test); execution_time in seconds; message, dbt's, cut at 1000
  characters (a failed unit test's is the diff). Every domain's table has this shape, and
  the governance view unions them (dbt/models/governance/), so a column changes in every
  domain's table at once or the view stops building.

  Append-only: Airflow's dbt_marts runs one dbt invocation per node, so a domain's table
  gains a commit per tested node per hourly run, and its tests' count of rows 24 times a
  day. expire_mart_snapshots compacts and expires it daily.
#}
{% macro record_test_results(results) %}
    {%- set rows = [] -%}
    {%- for r in results if r.node.resource_type in ('test', 'unit_test') -%}
        {#- A source test has no attached_node (dbt 1.12): its one dependency is the source. -#}
        {%- set tested_id = (r.node.attached_node or r.node.depends_on.nodes | first)
            if r.node.resource_type == 'test' else r.node.tested_node_unique_id -%}
        {%- set tested = graph.nodes.get(tested_id) or graph.sources.get(tested_id) -%}
        {%- set relation = [tested.database, tested.schema, tested.alias or tested.identifier] | join('.') if tested else none -%}
        {%- do rows.append('(' ~ [
            _sql_text(invocation_id),
            "timestamp '" ~ run_started_at.strftime('%Y-%m-%d %H:%M:%S.%f') ~ " UTC'",
            _sql_text(target.user),
            _sql_text(r.node.unique_id),
            _sql_text(r.node.name),
            _sql_text(relation),
            _sql_text(r.status),
            r.failures if r.failures is not none else 'null',
            'double ' ~ _sql_text(r.execution_time),
            _sql_text((r.message or '')[:1000] or none),
        ] | join(', ') ~ ')') -%}
    {%- endfor -%}
    {%- if rows -%}
        {%- set table = create_test_results_table() -%}
        {%- do run_query('insert into ' ~ table ~ ' values ' ~ rows | join(', ')) -%}
        {{ log("record_test_results: " ~ rows | length ~ " test result(s) into " ~ table, info=True) }}
    {%- endif -%}
{% endmacro %}

{#
  The writer's dq.dbt_test_results and its schema, if absent; returns the table's name. The
  hook calls it, and dbt_marts runs it as a task of its own before every other task of the
  domain (docker/airflow/dags/dbt_marts.py): two hooks creating a missing table at once can
  fail, one of them with "Table already exists" (ADR-046 Risks), and dbt's Jinja has no way
  to catch that and go on.
#}
{% macro create_test_results_table() %}
    {%- set table = target.database ~ '.dq.dbt_test_results' -%}
    {%- set create_table -%}
        create table if not exists {{ table }} (
            invocation_id varchar,
            run_started_at timestamp(6) with time zone,
            writer varchar,
            unique_id varchar,
            test_name varchar,
            tested varchar,
            status varchar,
            failures bigint,
            execution_time double,
            message varchar
        )
        comment 'One row per dbt test and unit test run by the catalog writer (dbt/macros/record_test_results.sql, ADR-046)'
    {%- endset -%}
    {%- do run_query('create schema if not exists ' ~ target.database ~ '.dq') -%}
    {%- do run_query(create_table) -%}
    {{- return(table) -}}
{% endmacro %}

{# A SQL string literal, or null. #}
{% macro _sql_text(value) -%}
    {{- 'null' if value is none else "'" ~ (value | string | replace("'", "''")) ~ "'" -}}
{%- endmacro %}
