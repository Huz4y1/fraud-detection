import pytest
from pyspark.sql import functions as F

from fraud.medallion import gold, session, silver

COLUMNS = [
    "trans_date_trans_time", "cc_num", "merchant", "category", "amt", "first", "last", "gender", "street", "city",
    "state", "zip", "lat", "long", "city_pop", "job", "dob", "trans_num", "unix_time", "merch_lat", "merch_long", "is_fraud",
]


def raw(trans_num, ts, amount, card="111", fraud="0", lat="40.0"):
    return [ts, card, "fraud_Kub-Mann", "grocery_pos", amount, "jane", "doe", "f", "1 Main St", "Albany", "ny",
            "1257", lat, "-73.0", "1000", "Teacher", "1990-01-01", trans_num, "0", "40.5", "-73.0", fraud]


@pytest.fixture(scope="module")
def spark():
    spark = session("tests")
    spark.conf.set("spark.sql.shuffle.partitions", "1")
    yield spark
    spark.stop()


@pytest.fixture(scope="module")
def layers(spark):
    rows = [
        raw("a", "2019-01-01 10:00:00", "10.0"),
        raw("b", "2019-01-01 12:00:00", "30.0"),
        raw("b", "2019-01-01 12:00:00", "30.0"),
        raw("c", "2019-01-03 12:00:00", "200.0", fraud="1"),
        raw("d", "2020-07-01 09:00:00", "5.0", card="222"),
        raw("bad_amount", "2019-01-01 13:00:00", "-4"),
        raw("bad_time", "not a date", "4"),
        raw("bad_geo", "2019-01-01 14:00:00", "4", lat="123"),
    ]
    bronze_df = (
        spark.createDataFrame(rows, COLUMNS)
        .coalesce(1)
        .withColumn("_source_file", F.lit("test.csv"))
        .withColumn("_ingested_at", F.current_timestamp())
    )
    clean, quarantine = silver(bronze_df.cache())
    return clean, quarantine, gold(clean)


def test_silver_quarantines_and_dedupes(layers):
    clean, quarantine, _ = layers
    assert sorted(r.trans_num for r in clean.collect()) == ["a", "b", "c", "d"]
    issues = {r.trans_num: r.dq_issue for r in quarantine.collect()}
    assert issues == {"bad_amount": "bad amount", "bad_time": "bad timestamp", "bad_geo": "bad coordinates"}


def test_silver_cleans_values(layers):
    row = layers[0].where("trans_num = 'a'").first()
    assert (row.merchant, row.first_name, row.state, row.zip, row.gender) == ("Kub-Mann", "Jane", "NY", "01257", "F")


def test_gold_features(layers):
    customers, merchants, fact = layers[2]
    assert customers.count() == 2 and merchants.count() == 1
    rows = {r.trans_num: r for r in fact.collect()}
    assert rows["a"].txn_count_24h == 1 and rows["a"].secs_since_last is None
    assert rows["b"].txn_count_24h == 2 and rows["b"].secs_since_last == 7200
    assert rows["b"].amount_24h == 40.0 and rows["b"].amount_to_avg == 3.0
    assert rows["c"].txn_count_24h == 1 and rows["c"].amount_to_avg == 10.0
    assert rows["a"].age == 29 and rows["a"].hour == 10
    assert 55 < rows["a"].distance_km < 56
    assert rows["c"].split == "history" and rows["d"].split == "stream"
