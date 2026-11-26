-- Version 1 of the contracted table: id, name, qty integer. Synthetic rows.
select cast(id as bigint) as id, cast(name as varchar) as name, cast(qty as integer) as qty
from (values (1, 'alpha', 10), (2, 'bravo', 20), (3, 'charlie', 30)) as t (id, name, qty)
