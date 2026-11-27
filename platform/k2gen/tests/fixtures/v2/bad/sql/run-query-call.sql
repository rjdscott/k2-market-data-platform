-- expect: sql.statement-ddl
-- A procedure call that moves the base's main.
{% do run_query("CALL ops_insight.system.rollback_to_snapshot('daily_load__base', 'gold_daily_load', 1)") %}
{{ config(materialized='table') }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
