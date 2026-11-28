-- What this does and why: type-2 history of each instrument a venue lists (ADR-030, with its
-- Outcome), rebuilt whole from the published claims (venue_symbol_claim, held ones left out) on
-- every run, so a --full-refresh gives the same rows and the history is the listing's. An
-- instrument's state can change only at a fetch where one of its claims starts or ends; at each
-- such fetch its state is the claim in force (half-open, claimed_from <= t < claimed_to), or none,
-- which is the instrument not listed (attributes null). A fetch whose state equals the previous
-- one's adds nothing; the rest are versions, each valid from its fetch to the next version's,
-- the first from 1970-01-01 so that anything earlier still matches, the open one to
-- 9999-12-31 23:59:59, never null. recorded_at is the fetch that first saw the version. Only
-- unheld claims reach here, so at most one claim is in force for an instrument at any time.
{{ config(materialized='table') }}
with claims as (
  select
    venue,
    instrument_id,
    base_asset,
    quote_asset,
    pair_code,
    claimed_from,
    claimed_to,
    tick_size,
    pair_decimals,
    lot_decimals,
    order_min,
    status
  from {{ ref('security_master__venue_symbol_claim') }}
  where held_reason is null
),

changes as (
  -- every fetch at which an instrument's state may change: one of its claims starts or ends
  select venue, instrument_id, base_asset, quote_asset, claimed_from as changed_at
  from claims
  union
  select venue, instrument_id, base_asset, quote_asset, claimed_to
  from claims
  where claimed_to < timestamp '9999-12-31 23:59:59 UTC'
),

states as (
  select
    ch.venue,
    ch.instrument_id,
    ch.base_asset,
    ch.quote_asset,
    ch.changed_at,
    c.pair_code,
    c.tick_size,
    c.pair_decimals,
    c.lot_decimals,
    c.order_min,
    c.status,
    c.pair_code is not null as is_listed
  from changes as ch
  left join claims as c
    on
      c.venue = ch.venue
      and c.instrument_id = ch.instrument_id
      and c.claimed_from <= ch.changed_at
      and ch.changed_at < c.claimed_to
),

compared as (
  select
    s.*,
    row_number() over w as change_number,
    lag(is_listed) over w as was_listed,
    lag(pair_code) over w as was_pair_code,
    lag(tick_size) over w as was_tick_size,
    lag(pair_decimals) over w as was_pair_decimals,
    lag(lot_decimals) over w as was_lot_decimals,
    lag(order_min) over w as was_order_min,
    lag(status) over w as was_status
  from states as s
  window w as (partition by venue, instrument_id order by changed_at)
),

versions as (
  select *
  from compared
  where
    change_number = 1
    or is_listed is distinct from was_listed
    or pair_code is distinct from was_pair_code
    or tick_size is distinct from was_tick_size
    or pair_decimals is distinct from was_pair_decimals
    or lot_decimals is distinct from was_lot_decimals
    or order_min is distinct from was_order_min
    or status is distinct from was_status
)

select
  cast(venue as varchar) as venue,
  cast(instrument_id as varchar) as instrument_id,
  case when row_number() over w = 1 then timestamp '1970-01-01 00:00:00 UTC' else changed_at end as valid_from,
  coalesce(lead(changed_at) over w, timestamp '9999-12-31 23:59:59 UTC') as valid_to,
  changed_at as recorded_at,
  cast(base_asset as varchar) as base_asset,
  cast(quote_asset as varchar) as quote_asset,
  is_listed,
  cast(pair_code as varchar) as pair_code,
  tick_size,
  pair_decimals,
  lot_decimals,
  order_min,
  cast(status as varchar) as status
from versions
window w as (partition by venue, instrument_id order by changed_at)
