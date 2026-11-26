-- expect: sql.adapter
-- A read-only adapter call: a warning.
{% set existing = adapter.get_relation(database=this.database, schema=this.schema, identifier='gold_daily_load') %}
{{ config(materialized='table') }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
