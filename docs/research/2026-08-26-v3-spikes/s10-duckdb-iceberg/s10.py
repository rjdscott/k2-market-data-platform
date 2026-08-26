import duckdb, sys
c=duckdb.connect()
c.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
c.execute("""CREATE SECRET s3sec (TYPE S3, KEY_ID 'minioadmin', SECRET 'minioadmin',
 ENDPOINT 'minio:9000', URL_STYLE 'path', USE_SSL false, REGION 'local-01')""")
variants = [
 ("A", None, "ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT 'http://lakekeeper:8181/catalog', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"),
 ("B", "CREATE SECRET lk (TYPE ICEBERG, ENDPOINT 'http://lakekeeper:8181/catalog', AUTHORIZATION_TYPE 'none')",
      "ATTACH 'k2' AS lake (TYPE ICEBERG, SECRET lk)"),
 ("C", None, "ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT 'http://lakekeeper:8181/catalog', AUTHORIZATION_TYPE 'none')"),
]
for name, pre, sql in variants:
    try:
        if pre: c.execute(pre)
        c.execute(sql)
        print(name, "ATTACH OK", flush=True)
        print(name, "COUNT", c.execute("SELECT count(*) FROM lake.bronze.t").fetchone(), flush=True)
        c.execute("DETACH lake")
    except Exception as e:
        print(name, "FAIL:", str(e).split("\n")[0][:250], flush=True)
