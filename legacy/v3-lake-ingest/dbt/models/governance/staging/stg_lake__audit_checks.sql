-- lake.audit.checks as governance reads it: every column, unchanged. Named apart from
-- market_analytics' stg_audit__checks because a dbt project allows one model per name.
select
    run_ts,
    job,
    check_name,
    scope,
    passed,
    observed,
    detail
from {{ source('audit', 'checks') }}
