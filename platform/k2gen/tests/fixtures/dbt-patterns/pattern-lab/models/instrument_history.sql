-- Synthetic, scripts/verify/dbt-patterns.sh pattern (a): type-2 history from a listing, rebuilt
-- whole from every listing row on each build, so a rebuild gives the same rows and the history
-- lives in the listing, never only in this table (dbt snapshots would hold it nowhere else).
-- A version is a fetch whose tick size differs from the key's previous fetch; it is valid from
-- that fetch to the next version's, half-open, the first from 1970-01-01 so that anything
-- earlier still matches, the open one to 9999-12-31 23:59:59, never null.
-- ponytail: a key missing from a later fetch keeps its open row; close it when a listing says delisted.
{{ config(materialized='table') }}
with listing as (
  select
    cast(symbol as varchar) as symbol,
    cast(tick_size as decimal(18, 8)) as tick_size,
    with_timezone(cast(fetched_at as timestamp(6)), 'UTC') as fetched_at
  from {{ ref('pattern_lab__listing') }}
),

changes as (
  select
    symbol,
    tick_size,
    fetched_at,
    lag(tick_size) over (partition by symbol order by fetched_at) as previous_tick_size
  from listing
),

versions as (
  select symbol, tick_size, fetched_at as recorded_at
  from changes
  where previous_tick_size is null or previous_tick_size <> tick_size
)

select
  symbol,
  case when row_number() over w = 1 then timestamp '1970-01-01 00:00:00 UTC' else recorded_at end as valid_from,
  coalesce(lead(recorded_at) over w, timestamp '9999-12-31 23:59:59 UTC') as valid_to,
  tick_size,
  recorded_at
from versions
window w as (partition by symbol order by recorded_at)
