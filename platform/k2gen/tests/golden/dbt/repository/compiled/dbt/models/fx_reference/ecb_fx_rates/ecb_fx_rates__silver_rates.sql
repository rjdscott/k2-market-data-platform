-- What this does and why: the ECB's 90-day file repeats the last 90 days, so every landed file
-- adds about 90 days of rows to bronze and one fixing appears once per file that holds it. This
-- model keeps each (rate_date, currency) once, from the newest file that holds it, and casts the
-- published string to an exact DECIMAL(20,10) (never a float, which would round it).
-- Which file is newest: the one whose own newest rate_date is latest (its as-of date), since each
-- file ends at its newest fixing; on a tie the larger sha256, only so the choice is repeatable.
-- Incremental: after the first run, dbt MERGEs the rows below into the table on its unique key,
-- the contract's primaryKey (rate_date, currency), which k2gen sets; the key holds rate_date, the
-- partition source, so the MERGE touches only the years a run brings (spike (a)).
{{ config(materialized='incremental', incremental_strategy='merge') }}
with bronze as (
  select rate_date, currency, rate, source_file_sha256
  from {{ source('ecb_fx_rates', 'bronze_daily') }}
),

files as (
  -- ponytail: reads every bronze row to date each file; about 2,700 rows a file, so a few
  -- million after years of daily files. Keep a file's as-of date in silver if it grows past that.
  select source_file_sha256, max(rate_date) as file_as_of
  from bronze
  group by source_file_sha256
),

candidates as (
  select b.rate_date, b.currency, b.rate, b.source_file_sha256, f.file_as_of
  from bronze as b
  join files as f on f.source_file_sha256 = b.source_file_sha256
  {% if is_incremental() %}
  -- Only files at least as new as the newest fixing already merged: the newest file repeats the
  -- last 90 days, so it brings every change an older file could, and older files add nothing.
  where f.file_as_of >= (select coalesce(max(rate_date), date '1970-01-01') from {{ this }})
  {% endif %}
),

ranked as (
  select
    rate_date,
    currency,
    rate,
    source_file_sha256,
    row_number() over (
      partition by rate_date, currency
      order by file_as_of desc, source_file_sha256 desc
    ) as file_rank
  from candidates
)

select
  rate_date,
  currency,
  cast(rate as decimal(20, 10)) as rate,
  source_file_sha256
from ranked
where file_rank = 1
