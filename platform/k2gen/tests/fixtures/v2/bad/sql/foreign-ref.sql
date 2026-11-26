-- expect: sql.foreign-read
-- Another product's model by its dbt name.
{{ config(materialized='table') }}
select station_id from {{ ref('station_readings__silver_readings') }}
