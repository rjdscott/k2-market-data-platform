-- What this does and why: every canonical code a published instrument buys or is priced in, once,
-- with its kind and the first fetch that showed it. The kind is fiat when the currency master
-- (read through k2_input at its tested snapshot) lists the code as a circulating currency: not a
-- fund, and with minor units, which leaves out the metals, the SDR and the testing codes the ISO
-- list also holds; stablecoin when the reviewed seeds/stablecoins.csv lists it; else crypto. No
-- history: a code and its kind do not change. A table rebuilt whole on every run.
{{ config(materialized='table') }}
with legs as (
  select base_asset as asset_code, recorded_at
  from {{ ref('security_master__instrument') }}
  union all
  select quote_asset, recorded_at
  from {{ ref('security_master__instrument') }}
),

assets as (
  select asset_code, min(recorded_at) as first_seen_at
  from legs
  group by asset_code
),

fiat as (
  select code
  from {{ k2_input('currency_master', 'currency') }}
  where not is_fund and minor_units is not null
),

stablecoins as (
  select upper(cast(asset_code as varchar)) as asset_code
  from {{ ref('security_master__stablecoins') }}
)

select
  cast(a.asset_code as varchar) as asset_code,
  cast(case
    when f.code is not null then 'fiat'
    when s.asset_code is not null then 'stablecoin'
    else 'crypto'
  end as varchar) as kind,
  a.first_seen_at
from assets as a
left join fiat as f on f.code = a.asset_code
left join stablecoins as s on s.asset_code = a.asset_code
