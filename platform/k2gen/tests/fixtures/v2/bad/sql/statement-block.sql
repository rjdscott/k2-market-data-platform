-- expect: sql.statement-ddl
-- A statement block that drops the base and its audited tag with it.
{% call statement('drop_base') %}
drop table ops_insight_build.daily_load__base.gold_daily_load
{% endcall %}
{{ config(materialized='table') }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
