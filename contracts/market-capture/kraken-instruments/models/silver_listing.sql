-- What this does and why: Kraken's AssetPairs endpoint shows the listing only as it is now, so a
-- pair's history lives in bronze, one row per pair of every landed response. This model walks the
-- landed responses in fetch time order (fetched_at, the connector's lineage, never the order they
-- arrived in the lake) and keeps a pair's state only where it differs from its state at the
-- previous fetch: its first fetch, a changed attribute, and its absence from a response after it
-- was listed (is_listed false, attributes null). A pair that disappears and returns therefore has
-- three rows, and a tick size changed and changed back has three versions, not two. Every landed
-- response lists every pair the venue lists, which is what makes a missing pair an absence; a
-- refetch that changes nothing lands nothing (fetch exits 10), and one that changes only other
-- pairs or attributes not kept here adds no row for this pair. A table rebuilt whole on every run,
-- so a rebuild gives the same rows and the history is bronze's alone.
-- ponytail: every pair at every landed response since its first, rebuilt each run, so the work
-- grows as pairs times landed responses. Make it incremental from each pair's newest version when
-- the hourly rebuild's time, measured by the soak, says so.
{{ config(materialized='table') }}
with bronze as (
  select
    pair_code,
    ws_name,
    base,
    quote,
    cast(tick_size as decimal(38, 18)) as tick_size,
    pair_decimals,
    lot_decimals,
    cast(order_min as decimal(38, 18)) as order_min,
    status,
    fetched_at,
    source_file_sha256
  from {{ source('kraken_instruments', 'bronze_pairs') }}
),

fetches as (
  -- each landed response once; the same bytes fetched again at another time are another fetch
  select distinct fetched_at, source_file_sha256
  from bronze
),

pairs as (
  select pair_code, min(fetched_at) as first_seen
  from bronze
  group by pair_code
),

states as (
  -- every pair at every fetch from its first: its attributes, or all null where the response lacks it
  select
    p.pair_code,
    f.fetched_at,
    f.source_file_sha256,
    b.pair_code is not null as is_listed,
    b.ws_name,
    b.base,
    b.quote,
    b.tick_size,
    b.pair_decimals,
    b.lot_decimals,
    b.order_min,
    b.status
  from pairs as p
  join fetches as f on f.fetched_at >= p.first_seen
  left join bronze as b
    on b.pair_code = p.pair_code and b.fetched_at = f.fetched_at and b.source_file_sha256 = f.source_file_sha256
),

compared as (
  select
    s.*,
    row_number() over w as fetch_number,
    lag(is_listed) over w as was_listed,
    lag(ws_name) over w as was_ws_name,
    lag(base) over w as was_base,
    lag(quote) over w as was_quote,
    lag(tick_size) over w as was_tick_size,
    lag(pair_decimals) over w as was_pair_decimals,
    lag(lot_decimals) over w as was_lot_decimals,
    lag(order_min) over w as was_order_min,
    lag(status) over w as was_status
  from states as s
  window w as (partition by pair_code order by fetched_at, source_file_sha256)
)

select
  pair_code,
  fetched_at as first_fetched_at,
  is_listed,
  ws_name,
  base,
  quote,
  tick_size,
  pair_decimals,
  lot_decimals,
  order_min,
  status,
  source_file_sha256
from compared
where fetch_number = 1
  or is_listed is distinct from was_listed
  or ws_name is distinct from was_ws_name
  or base is distinct from was_base
  or quote is distinct from was_quote
  or tick_size is distinct from was_tick_size
  or pair_decimals is distinct from was_pair_decimals
  or lot_decimals is distinct from was_lot_decimals
  or order_min is distinct from was_order_min
  or status is distinct from was_status
