-- What this does and why: a venue lists pairs under its own codes, and a trade names its pair by
-- the trades stream's spelling. This model is the one place the matching rule lives. Each listed
-- version of a pair (silver_listing, read through k2_input at its tested snapshot) becomes one
-- claim, a span from the fetch that first stated it to the pair's next version. Its websocket
-- name is split at its one slash, each code upper-cased and mapped through the reviewed
-- seeds/code_exceptions.csv for the venue (Kraken's older XBT is BTC), and recomposed: base/quote
-- is the venue spelling, as Kraken's websocket v2 trades stream writes it, and base-quote the
-- instrument id. Splitting the websocket name, not stripping the X and Z prefixes of the REST
-- base and quote, is deliberate: no prefix rule tells XXBT from an asset whose code starts with X.
-- A spelling two pairs of one venue claim over overlapping spans is held whole (held_reason),
-- since publishing either would pick an answer for the overlap; a name with no single slash is
-- held too. Kraken is the only venue; another venue's listing is another input port, unioned in
-- listing with its own recomposition. A table rebuilt whole on every run.
{{ config(materialized='table') }}
with listing as (
  -- each version of a pair, and the start of the pair's next version: the span it held
  select
    cast('kraken' as varchar) as venue,
    pair_code,
    first_fetched_at,
    coalesce(
      lead(first_fetched_at) over (partition by pair_code order by first_fetched_at),
      timestamp '9999-12-31 23:59:59 UTC'
    ) as next_fetched_at,
    is_listed,
    ws_name,
    tick_size,
    pair_decimals,
    lot_decimals,
    order_min,
    status
  from {{ k2_input('kraken_instruments', 'silver_listing') }}
),

exceptions as (
  select
    cast(venue as varchar) as venue,
    upper(cast(venue_code as varchar)) as venue_code,
    upper(cast(asset_code as varchar)) as asset_code
  from {{ ref('security_master__code_exceptions') }}
),

codes as (
  -- a listed version's websocket name, split at its one slash, each part upper-cased
  select
    l.venue,
    l.pair_code,
    l.first_fetched_at,
    l.next_fetched_at,
    l.ws_name,
    l.tick_size,
    l.pair_decimals,
    l.lot_decimals,
    l.order_min,
    l.status,
    case when regexp_like(l.ws_name, '^[^/]+/[^/]+$') then upper(split_part(l.ws_name, '/', 1)) end as base_code,
    case when regexp_like(l.ws_name, '^[^/]+/[^/]+$') then upper(split_part(l.ws_name, '/', 2)) end as quote_code
  from listing as l
  where l.is_listed
),

claims as (
  select
    c.*,
    coalesce(b.asset_code, c.base_code) as base_asset,
    coalesce(q.asset_code, c.quote_code) as quote_asset,
    coalesce(b.asset_code, c.base_code) || '/' || coalesce(q.asset_code, c.quote_code) as venue_symbol,
    coalesce(b.asset_code, c.base_code) || '-' || coalesce(q.asset_code, c.quote_code) as instrument_id
  from codes as c
  left join exceptions as b on b.venue = c.venue and b.venue_code = c.base_code
  left join exceptions as q on q.venue = c.venue and q.venue_code = c.quote_code
),

contested as (
  -- each spelling of a venue that two pairs claim over spans that overlap
  select distinct
    a.venue,
    a.venue_symbol
  from claims as a
  inner join claims as b
    on
      b.venue = a.venue
      and b.venue_symbol = a.venue_symbol
      and b.pair_code <> a.pair_code
      and b.first_fetched_at < a.next_fetched_at
      and a.first_fetched_at < b.next_fetched_at
)

select
  cast(c.venue as varchar) as venue,
  cast(c.pair_code as varchar) as pair_code,
  c.first_fetched_at as claimed_from,
  c.next_fetched_at as claimed_to,
  cast(c.ws_name as varchar) as ws_name,
  cast(c.base_asset as varchar) as base_asset,
  cast(c.quote_asset as varchar) as quote_asset,
  cast(c.venue_symbol as varchar) as venue_symbol,
  cast(c.instrument_id as varchar) as instrument_id,
  c.tick_size,
  c.pair_decimals,
  c.lot_decimals,
  c.order_min,
  cast(c.status as varchar) as status,
  cast(case
    when c.venue_symbol is null then 'not_base_slash_quote'
    when k.venue_symbol is not null then 'claimed_twice'
  end as varchar) as held_reason
from claims as c
left join contested as k on k.venue = c.venue and k.venue_symbol = c.venue_symbol
