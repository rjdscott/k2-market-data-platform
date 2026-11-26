select id, amount, amount * 2 as doubled
from {{ k2_input('up', 'events') }}
