-- What this does and why: the claims venue_symbol_claim holds from publication, for the owner to
-- read in one place: a spelling claimed twice by one venue, or a websocket name the rule cannot
-- split. Its warn test reports any row; the build still publishes every other spelling. A table
-- rebuilt whole on every run, so a claim leaves it on the build whose rule or listing resolves it.
{{ config(materialized='table') }}
select
  venue,
  pair_code,
  claimed_from,
  claimed_to,
  ws_name,
  venue_symbol,
  held_reason
from {{ ref('security_master__venue_symbol_claim') }}
where held_reason is not null
