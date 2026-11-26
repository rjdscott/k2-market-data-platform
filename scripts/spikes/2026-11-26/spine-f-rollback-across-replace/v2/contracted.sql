-- Version 2: name renamed label, qty widened to bigint, amount added. Synthetic rows.
select cast(id as bigint) as id, cast(label as varchar) as label, cast(qty as bigint) as qty,
       cast(amount as decimal(18, 8)) as amount
from (values (1, 'ALPHA', 11, 1.5), (2, 'BRAVO', 21, 2.5)) as t (id, label, qty, amount)
