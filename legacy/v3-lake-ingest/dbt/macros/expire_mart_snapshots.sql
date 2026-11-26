{#
  Snapshot maintenance of one domain's marts, by its own writer through Trino:

    K2_TRINO_USER=dbt_<catalog> dbt run-operation expire_mart_snapshots

  Every model in the profile's catalog (the writer's own) that is built as a table, and
  exists: expire_snapshots, then remove_orphan_files, both at 7 days. A rebuild is a CREATE
  OR REPLACE, one snapshot per build, so an hourly mart gains 24 a day; this keeps a week of
  them for time travel and deletes the files only older snapshots reference. 7 days is the
  Iceberg connector's floor, and the session properties that would lower it are denied to
  every user (ADR-044), so a shorter retention fails here rather than running. Views have no
  snapshots. The dbt_marts DAG runs it daily (docker/airflow/dags/dbt_marts.py).

  And the catalog's test results, dq.dbt_test_results (record_test_results.sql, ADR-046),
  if they exist: optimize first, because they are appended to, a small file per commit and
  a commit per tested node per run, where a mart is rewritten whole; then the same expiry.
#}
{% macro expire_mart_snapshots() %}
    {%- set retention = '7d' -%}
    {%- set tables = [] -%}
    {%- for node in graph.nodes.values()
        if node.resource_type == 'model' and node.database == target.database
        and node.config.materialized == 'table' -%}
        {%- do tables.append((node.schema, node.alias, [])) -%}
    {%- endfor -%}
    {%- do tables.append(('dq', 'dbt_test_results', ['optimize'])) -%}
    {%- for schema, identifier, first in tables -%}
        {%- set relation = adapter.get_relation(database=target.database, schema=schema, identifier=identifier) -%}
        {%- if relation is none -%}
            {{ log("expire_mart_snapshots: " ~ target.database ~ "." ~ schema ~ "." ~ identifier ~ " not built yet, skipped", info=True) }}
        {%- else -%}
            {%- for procedure in first -%}
                {%- do run_query("alter table " ~ relation ~ " execute " ~ procedure) -%}
                {{ log("expire_mart_snapshots: " ~ relation ~ " " ~ procedure, info=True) }}
            {%- endfor -%}
            {%- for procedure in ['expire_snapshots', 'remove_orphan_files'] -%}
                {%- do run_query("alter table " ~ relation ~ " execute " ~ procedure ~ "(retention_threshold => '" ~ retention ~ "')") -%}
                {{ log("expire_mart_snapshots: " ~ relation ~ " " ~ procedure ~ " at " ~ retention, info=True) }}
            {%- endfor -%}
        {%- endif -%}
    {%- endfor -%}
{% endmacro %}
