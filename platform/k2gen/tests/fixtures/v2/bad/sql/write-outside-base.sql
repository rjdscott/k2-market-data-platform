-- expect: sql.write-outside-base
-- A schema override: dbt would write into another product's base.
{{ config(materialized='table', schema='station_readings__base') }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
