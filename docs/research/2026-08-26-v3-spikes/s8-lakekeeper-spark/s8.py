from pyspark.sql import SparkSession
C="spark.sql.catalog.lake"
s=(SparkSession.builder.appName("s8")
 .config("spark.sql.extensions","org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
 .config(C,"org.apache.iceberg.spark.SparkCatalog")
 .config(C+".type","rest")
 .config(C+".uri","http://lakekeeper:8181/catalog")
 .config(C+".warehouse","k2")
 .config(C+".io-impl","org.apache.iceberg.aws.s3.S3FileIO")
 .config(C+".s3.endpoint","http://minio:9000")
 .config(C+".s3.path-style-access","true")
 .config(C+".s3.access-key-id","minioadmin")
 .config(C+".s3.secret-access-key","minioadmin")
 .config(C+".s3.region","local-01")
 .config("spark.sql.defaultCatalog","lake")
 .getOrCreate())
s.sparkContext.setLogLevel("ERROR")
s.sql("CREATE NAMESPACE IF NOT EXISTS lake.bronze")
s.sql("DROP TABLE IF EXISTS lake.bronze.t")
s.sql("""CREATE TABLE lake.bronze.t (id bigint, ts timestamp, px decimal(28,10))
 USING iceberg PARTITIONED BY (days(ts)) TBLPROPERTIES ("format-version"="2")""")
s.sql("""INSERT INTO lake.bronze.t VALUES
 (1, TIMESTAMP "2026-08-26 01:00:00", 12345.1234567890),
 (2, TIMESTAMP "2026-08-26 02:00:00", 22345.1234567890),
 (3, TIMESTAMP "2026-08-27 03:00:00", 32345.1234567890)""")
print("COUNT1:", s.sql("SELECT count(*) c FROM lake.bronze.t").collect()[0].c)
df=s.sql("SELECT 4L AS id, TIMESTAMP \"2026-08-28 04:00:00\" AS ts, CAST(999.5 AS decimal(28,10)) AS px")
df.writeTo("lake.bronze.t").option("snapshot-property.k2.kafka-offsets", "{\"0\":42}").append()
print("COUNT2:", s.sql("SELECT count(*) c FROM lake.bronze.t").collect()[0].c)
for r in s.sql("SELECT snapshot_id, summary FROM lake.bronze.t.snapshots").collect():
    print("SNAP", r.snapshot_id, {k:v for k,v in r.summary.items() if k.startswith("k2.")})
