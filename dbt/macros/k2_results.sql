{#- k2_results(results): the per-product results hook (phase 001 interface A7,
    docs/development/phase-001-interfaces.md), the compiled project's on-run-end
    (platform/k2gen/k2gen/emit/dbt.py). Every data test and unit test of the invocation, one row
    each, into <domain>_build.<fence>__base.dq_test_results, as the builder dbt_<fence>, which owns
    only <fence>__base (ADR-061): one INSERT of all rows, the schema and table created if absent.
    An invocation that ran no test writes nothing and needs no var.

    dbt 1.12 runs on-run-end after the nodes whatever their result, so a failed, errored or skipped
    test gets its row (scripts/verify/dbt-wrapper.sh plants a failing one and reads it back). If the hook fails,
    dbt exits non-zero although every test passed, and the rows are lost: nothing here is caught.

    Columns: those of record_test_results.sql (ADR-046), its invocation_id named
    dbt_invocation_id, plus run_id (<dag_id>/<run_id>, the var k2_run_id the wrapper sets) and
    release_id (the var k2_release, the release's id, since the compiled tree reads no environment), the keys that join a publish's run record to its test rows.
    status is dbt's (pass, fail, warn, error, skipped); failures, the failing rows dbt counted;
    execution_time in seconds; message cut at 1000 characters (a failed unit test's is the diff);
    tested, the relation of the model or seed it tests. -#}
{% macro k2_results(results) -%}
  {%- if execute -%}
    {%- set tests = [] -%}
    {%- for r in results if r.node.resource_type in ('test', 'unit_test') -%}{%- do tests.append(r) -%}{%- endfor -%}
    {%- if tests -%}
      {%- if not target.user.startswith('dbt_') -%}
        {{ exceptions.raise_compiler_error("k2_results: the writer " ~ target.user ~ " is not a builder dbt_<fence>") }}
      {%- endif -%}
      {%- set schema = target.user[4:] ~ '__base' -%}
      {%- set dbs = graph.nodes.values() | selectattr('schema', 'equalto', schema) | map(attribute='database') | unique | list -%}
      {%- if dbs | length != 1 -%}
        {{ exceptions.raise_compiler_error("k2_results: no single database builds " ~ schema ~ ": " ~ dbs) }}
      {%- endif -%}
      {%- set table = dbs[0] ~ '.' ~ schema ~ '.dq_test_results' -%}
      {%- set keys = [k2_results_text(var('k2_run_id')), k2_results_text(invocation_id), k2_results_text(var('k2_release')),
          "timestamp '" ~ run_started_at.strftime('%Y-%m-%d %H:%M:%S.%f') ~ " UTC'", k2_results_text(target.user)] -%}
      {%- set rows = [] -%}
      {%- for r in tests -%}
        {#- A unit test that ran is the UnitTestNode dbt built, with tested_node_unique_id; one
            skipped or errored before that is the UnitTestDefinition, which depends on its model alone. -#}
        {%- set tested_id = (r.node.attached_node if r.node.resource_type == 'test' else r.node.tested_node_unique_id)
            or r.node.depends_on.nodes | first -%}
        {%- set tested = graph.nodes.get(tested_id) or graph.sources.get(tested_id) -%}
        {%- do rows.append('(' ~ (keys + [
            k2_results_text(r.node.unique_id),
            k2_results_text(r.node.name),
            k2_results_text([tested.database, tested.schema, tested.alias or tested.identifier] | join('.') if tested else none),
            k2_results_text(r.status),
            r.failures if r.failures is not none else 'null',
            'double ' ~ k2_results_text(r.execution_time),
            k2_results_text((r.message or '')[:1000] or none),
        ]) | join(', ') ~ ')') -%}
      {%- endfor -%}
      {%- do run_query('create schema if not exists ' ~ dbs[0] ~ '.' ~ schema) -%}
      {%- do run_query('create table if not exists ' ~ table ~ ' (run_id varchar, dbt_invocation_id varchar, release_id varchar, '
          ~ 'run_started_at timestamp(6) with time zone, writer varchar, unique_id varchar, test_name varchar, tested varchar, '
          ~ 'status varchar, failures bigint, execution_time double, message varchar) '
          ~ "comment 'One row per dbt test and unit test the builder ran (dbt/macros/k2_results.sql)'") -%}
      {%- do run_query('insert into ' ~ table ~ ' (run_id, dbt_invocation_id, release_id, run_started_at, writer, unique_id, '
          ~ 'test_name, tested, status, failures, execution_time, message) values ' ~ rows | join(', ')) -%}
      {{ log("k2_results: " ~ rows | length ~ " test result(s) into " ~ table, info=True) }}
    {%- endif -%}
  {%- endif -%}
{%- endmacro %}

{#- A Trino string literal, or null: every quote doubled, so no value can end the literal. -#}
{% macro k2_results_text(value) -%}
  {{- 'null' if value is none else "'" ~ (value | string | replace("'", "''")) ~ "'" -}}
{%- endmacro %}
