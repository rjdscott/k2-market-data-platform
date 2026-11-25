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
#}
{% macro expire_mart_snapshots() %}
    {%- set retention = '7d' -%}
    {%- for node in graph.nodes.values()
        if node.resource_type == 'model' and node.database == target.database
        and node.config.materialized == 'table' -%}
        {%- set relation = adapter.get_relation(database=node.database, schema=node.schema, identifier=node.alias) -%}
        {%- if relation is none -%}
            {{ log("expire_mart_snapshots: " ~ node.database ~ "." ~ node.schema ~ "." ~ node.alias ~ " not built yet, skipped", info=True) }}
        {%- else -%}
            {%- for procedure in ['expire_snapshots', 'remove_orphan_files'] -%}
                {%- do run_query("alter table " ~ relation ~ " execute " ~ procedure ~ "(retention_threshold => '" ~ retention ~ "')") -%}
                {{ log("expire_mart_snapshots: " ~ relation ~ " " ~ procedure ~ " at " ~ retention, info=True) }}
            {%- endfor -%}
        {%- endif -%}
    {%- endfor -%}
{% endmacro %}
