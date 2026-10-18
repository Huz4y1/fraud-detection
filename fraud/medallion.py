import os
import sys
import urllib.request

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

from fraud.config import BRONZE, DATA, GOLD, LANDING, SILVER, STREAM_FROM

LOCAL_FS_JAR = "https://repo1.maven.org/maven2/com/globalmentor/hadoop-bare-naked-local-fs/0.1.0/hadoop-bare-naked-local-fs-0.1.0.jar"


def session(app="fraud-medallion"):
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    builder = (
        SparkSession.builder.appName(app)
        .master("local[*]")
        .config("spark.driver.memory", "4g")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.ui.showConsoleProgress", "false")
    )
    if os.name == "nt":
        jar = DATA / "jars" / "bare-local-fs.jar"
        if not jar.exists():
            jar.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(LOCAL_FS_JAR, jar)
        builder = builder.config("spark.driver.extraClassPath", str(jar)).config(
            "spark.hadoop.fs.file.impl", "com.globalmentor.apache.hadoop.fs.BareLocalFileSystem"
        )
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def bronze(spark):
    raw = spark.read.option("header", True).csv(str(LANDING / "*.csv"))
    return (
        raw.drop("_c0")
        .withColumn("_source_file", F.col("_metadata.file_name"))
        .withColumn("_ingested_at", F.current_timestamp())
    )


def silver(bronze_df):
    typed = bronze_df.select(
        F.trim("trans_num").alias("trans_num"),
        F.col("trans_date_trans_time").try_cast("timestamp").alias("ts"),
        F.trim("cc_num").alias("customer_id"),
        F.regexp_replace(F.trim("merchant"), "^fraud_", "").alias("merchant"),
        F.trim("category").alias("category"),
        F.col("amt").try_cast("double").alias("amount"),
        F.initcap(F.trim("first")).alias("first_name"),
        F.initcap(F.trim("last")).alias("last_name"),
        F.upper(F.trim("gender")).alias("gender"),
        F.trim("street").alias("street"),
        F.trim("city").alias("city"),
        F.upper(F.trim("state")).alias("state"),
        F.lpad(F.trim("zip"), 5, "0").alias("zip"),
        F.col("lat").try_cast("double").alias("lat"),
        F.col("long").try_cast("double").alias("long"),
        F.col("city_pop").try_cast("int").alias("city_pop"),
        F.trim("job").alias("job"),
        F.col("dob").try_cast("date").alias("dob"),
        F.col("merch_lat").try_cast("double").alias("merch_lat"),
        F.col("merch_long").try_cast("double").alias("merch_long"),
        F.col("is_fraud").try_cast("int").alias("is_fraud"),
        "_source_file",
        "_ingested_at",
    )
    valid_geo = F.coalesce(
        F.col("lat").between(-90, 90)
        & F.col("long").between(-180, 180)
        & F.col("merch_lat").between(-90, 90)
        & F.col("merch_long").between(-180, 180),
        F.lit(False),
    )
    issue = (
        F.when(F.col("trans_num").isNull(), "missing trans_num")
        .when(F.col("ts").isNull(), "bad timestamp")
        .when(F.col("customer_id").isNull() | F.col("merchant").isNull() | F.col("category").isNull(), "missing key")
        .when(F.col("amount").isNull() | (F.col("amount") <= 0), "bad amount")
        .when(F.col("is_fraud").isNull() | ~F.col("is_fraud").isin(0, 1), "bad label")
        .when(F.col("dob").isNull(), "bad dob")
        .when(~valid_geo, "bad coordinates")
    )
    checked = typed.withColumn("dq_issue", issue)
    clean = checked.where(F.col("dq_issue").isNull()).drop("dq_issue").dropDuplicates(["trans_num"])
    quarantine = checked.where(F.col("dq_issue").isNotNull())
    return clean, quarantine


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (F.radians(c) for c in (lat1, lon1, lat2, lon2))
    a = F.sin((lat2 - lat1) / 2) ** 2 + F.cos(lat1) * F.cos(lat2) * F.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * F.asin(F.sqrt(a))


def gold(silver_df):
    customers = silver_df.select(
        "customer_id", "first_name", "last_name", "gender", "street", "city", "state", "zip", "lat", "long", "city_pop", "job", "dob"
    ).dropDuplicates(["customer_id"])
    merchants = (
        silver_df.select(F.col("merchant").alias("name"), "category")
        .distinct()
        .withColumn("merchant_id", F.row_number().over(Window.orderBy("name", "category")))
        .select("merchant_id", "name", "category")
    )
    card = Window.partitionBy("customer_id").orderBy("epoch")
    last_day = card.rangeBetween(-86400, 0)
    before = card.rowsBetween(Window.unboundedPreceding, -1)
    fact = (
        silver_df.withColumn("epoch", F.col("ts").cast("long"))
        .join(F.broadcast(merchants.withColumnRenamed("name", "merchant")), ["merchant", "category"])
        .select(
            "trans_num",
            F.col("ts").cast("timestamp_ntz").alias("ts"),
            "customer_id",
            "merchant_id",
            "category",
            "amount",
            "merch_lat",
            "merch_long",
            F.hour("ts").alias("hour"),
            F.dayofweek("ts").alias("day_of_week"),
            F.floor(F.months_between("ts", "dob") / 12).cast("int").alias("age"),
            F.round(haversine("lat", "long", "merch_lat", "merch_long"), 2).alias("distance_km"),
            F.count("*").over(last_day).alias("txn_count_24h"),
            F.round(F.sum("amount").over(last_day), 2).alias("amount_24h"),
            (F.col("epoch") - F.lag("epoch").over(card)).cast("double").alias("secs_since_last"),
            F.round(F.col("amount") / F.avg("amount").over(before), 4).alias("amount_to_avg"),
            "is_fraud",
            F.when(F.col("ts") < F.lit(STREAM_FROM).cast("timestamp"), "history").otherwise("stream").alias("split"),
        )
    )
    return customers, merchants, fact


def main():
    spark = session()
    bronze(spark).write.mode("overwrite").parquet(str(BRONZE / "transactions"))
    bronze_df = spark.read.parquet(str(BRONZE / "transactions"))

    clean, quarantine = silver(bronze_df)
    clean.write.mode("overwrite").parquet(str(SILVER / "transactions"))
    quarantine.write.mode("overwrite").parquet(str(SILVER / "quarantine"))
    silver_df = spark.read.parquet(str(SILVER / "transactions"))

    customers, merchants, fact = gold(silver_df)
    customers.write.mode("overwrite").parquet(str(GOLD / "dim_customer"))
    merchants.write.mode("overwrite").parquet(str(GOLD / "dim_merchant"))
    fact.write.mode("overwrite").partitionBy("split").parquet(str(GOLD / "fact_transactions"))

    bronze_rows = bronze_df.count()
    silver_rows = silver_df.count()
    quarantined = spark.read.parquet(str(SILVER / "quarantine")).count()
    splits = {r["split"]: r["count"] for r in spark.read.parquet(str(GOLD / "fact_transactions")).groupBy("split").count().collect()}
    print(f"bronze  {bronze_rows:>9,} raw rows")
    print(f"silver  {silver_rows:>9,} clean rows, {quarantined:,} quarantined, {bronze_rows - silver_rows - quarantined:,} duplicates dropped")
    print(f"gold    {splits.get('history', 0):>9,} history + {splits.get('stream', 0):,} stream facts, "
          f"{spark.read.parquet(str(GOLD / 'dim_customer')).count():,} customers, {spark.read.parquet(str(GOLD / 'dim_merchant')).count():,} merchants")
    spark.stop()


if __name__ == "__main__":
    main()
