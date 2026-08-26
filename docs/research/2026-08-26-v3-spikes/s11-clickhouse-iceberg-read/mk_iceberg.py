import datetime, decimal
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

cat = SqlCatalog(
    "spike",
    **{
        "uri": "sqlite:////w/spike-cat.db",
        "warehouse": "s3://lake/wh",
        "s3.endpoint": "http://minio:9000",
        "s3.access-key-id": "spikekey",
        "s3.secret-access-key": "spikesecret",
        "s3.path-style-access": "true",
        "s3.region": "us-east-1",
    },
)
cat.create_namespace_if_not_exists("ns")
schema = pa.schema([
    pa.field("id", pa.int64(), nullable=False),
    pa.field("sym", pa.string()),
    pa.field("px", pa.decimal128(18, 8)),
    pa.field("ts", pa.timestamp("us")),
])
try:
    cat.drop_table("ns.t")
except Exception:
    pass
tbl = cat.create_table("ns.t", schema=schema, properties={"format-version": "2"})

def batch(lo, hi):
    return pa.Table.from_pylist([
        {"id": i, "sym": f"S{i}", "px": decimal.Decimal(f"{100 + i}.12345678"),
         "ts": datetime.datetime(2026, 8, 26, 0, 0, i)}
        for i in range(lo, hi)
    ], schema=schema)

tbl.append(batch(1, 4))   # snapshot 1
tbl.append(batch(4, 7))   # snapshot 2
tbl.refresh()
print("format-version:", tbl.metadata.format_version)
print("snapshots:", len(tbl.metadata.snapshots))
print("metadata_location:", tbl.metadata_location)
print(tbl.scan().to_arrow().to_pydict())
