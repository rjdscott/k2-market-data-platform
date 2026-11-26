-- expect: sql.hook
-- A hook replacing the base table: its audited tag goes with the old table (gate h1.34).
{{ config(materialized='table', post_hook="create or replace table {{ this }} as select * from {{ this }}") }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
