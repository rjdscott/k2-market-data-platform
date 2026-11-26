-- lake.audit.checks as market_analytics reads it: the columns its marts use, unchanged.
select
    run_ts,
    job,
    check_name,
    scope,
    passed,
    observed
from {{ source('audit', 'checks') }}
