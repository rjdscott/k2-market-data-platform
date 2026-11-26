-- expect: sql.hook
-- The gate's case: a model hook replacing the product's reader view through the build catalog.
{{ config(materialized='table',
          post_hook="create or replace view ops_insight_build.daily_load.gold_daily_load as select 1 x") }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
