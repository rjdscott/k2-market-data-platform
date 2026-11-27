-- Synthetic, scripts/verify/dbt-patterns.sh pattern (b): the held table, every trade whose symbol
-- resolves to nothing now, rebuilt whole on each build, so a trade leaves it on the build that
-- resolves it (and silver_trade takes it the same build).
{{ config(materialized='table') }}
select
  cast(t.trade_id as bigint) as trade_id,
  cast(t.symbol as varchar) as symbol,
  cast(t.price as decimal(18, 8)) as price,
  cast(t.quantity as decimal(18, 8)) as quantity
from {{ ref('pattern_lab__trades') }} as t
where not exists (select 1 from {{ ref('pattern_lab__symbols') }} as s where s.symbol = t.symbol)
