-- What this does and why: type-2 history of each venue spelling, the symbol a trade carries,
-- and the instrument it names, rebuilt whole from the published claims (venue_symbol_claim, held
-- ones left out) on every run, so a --full-refresh gives the same rows. A version starts at a
-- claim whose instrument or pair differs from the spelling's previous claim; a claim that only
-- changes the listing's attributes, and a gap while the pair is delisted, change nothing, so the
-- spelling keeps resolving (instrument.is_listed records the delisting). Each version is valid
-- from its fetch to the next version's, the first from 1970-01-01 so that a trade older than the
-- first fetch still resolves, the open one to 9999-12-31 23:59:59, never null; recorded_at is the
-- fetch that first saw it. Only unheld claims reach here, so no two overlap for a spelling.
{{ config(materialized='table') }}
with claims as (
  select
    venue,
    venue_symbol,
    instrument_id,
    pair_code,
    claimed_from
  from {{ ref('security_master__venue_symbol_claim') }}
  where held_reason is null
),

compared as (
  select
    c.*,
    row_number() over w as claim_number,
    lag(instrument_id) over w as was_instrument_id,
    lag(pair_code) over w as was_pair_code
  from claims as c
  window w as (partition by venue, venue_symbol order by claimed_from)
),

versions as (
  select
    venue,
    venue_symbol,
    instrument_id,
    pair_code,
    claimed_from as recorded_at
  from compared
  where
    claim_number = 1
    or instrument_id is distinct from was_instrument_id
    or pair_code is distinct from was_pair_code
)

select
  cast(venue as varchar) as venue,
  cast(venue_symbol as varchar) as venue_symbol,
  case when row_number() over w = 1 then timestamp '1970-01-01 00:00:00 UTC' else recorded_at end as valid_from,
  coalesce(lead(recorded_at) over w, timestamp '9999-12-31 23:59:59 UTC') as valid_to,
  cast(instrument_id as varchar) as instrument_id,
  cast(pair_code as varchar) as pair_code,
  recorded_at
from versions
window w as (partition by venue, venue_symbol order by recorded_at)
