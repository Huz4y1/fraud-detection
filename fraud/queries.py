from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import case, distinct, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fraud.db import Customer, Feedback, Merchant, ModelVersion, Transaction, engine

FLAGGED = Transaction.decision == "flagged"
FRAUD = Transaction.is_fraud == 1
AMOUNT_BAND = case(
    (Transaction.amount < 25, "< $25"),
    (Transaction.amount < 100, "$25-100"),
    (Transaction.amount < 300, "$100-300"),
    (Transaction.amount < 1000, "$300-1k"),
    else_="$1k+",
)


@dataclass(frozen=True)
class Filters:
    categories: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    amount: tuple[float, float] | None = None
    decision: str | None = None
    minutes: int | None = None


def read(stmt):
    return pd.read_sql(stmt, engine)


def scoped(f, source, *columns):
    stmt = (
        select(*columns)
        .select_from(Transaction)
        .join(Customer, Customer.customer_id == Transaction.customer_id)
        .join(Merchant, Merchant.merchant_id == Transaction.merchant_id)
        .where(Transaction.source == source)
    )
    if f.categories:
        stmt = stmt.where(Transaction.category.in_(f.categories))
    if f.states:
        stmt = stmt.where(Customer.state.in_(f.states))
    if f.amount:
        stmt = stmt.where(Transaction.amount.between(*f.amount))
    if source == "live" and f.decision:
        stmt = stmt.where(Transaction.decision == f.decision)
    if source == "live" and f.minutes:
        stmt = stmt.where(Transaction.scored_at >= datetime.now() - timedelta(minutes=f.minutes))
    return stmt


def options():
    with engine.connect() as conn:
        categories = conn.scalars(select(Merchant.category).distinct().order_by(Merchant.category)).all()
        states = conn.scalars(select(Customer.state).distinct().order_by(Customer.state)).all()
    return categories, states


def live_kpis(f):
    stmt = scoped(
        f,
        "live",
        func.count().label("transactions"),
        func.count().filter(FLAGGED).label("flagged"),
        func.coalesce(func.sum(Transaction.amount), 0).label("volume"),
        func.coalesce(func.sum(Transaction.amount).filter(FLAGGED), 0).label("flagged_volume"),
        func.count(distinct(Transaction.customer_id)).label("cards"),
    )
    return read(stmt).iloc[0]


def live_quality(f):
    stmt = (
        scoped(f, "live", Transaction.decision, Feedback.label, Feedback.source, func.count().label("n"))
        .join(Feedback, Feedback.trans_num == Transaction.trans_num)
        .group_by(Transaction.decision, Feedback.label, Feedback.source)
    )
    return read(stmt)


def live_timeline(f):
    seconds = max(5, f.minutes or 60)
    bucket = func.date_bin(
        literal_column(f"interval '{seconds} seconds'"), Transaction.scored_at, literal_column("timestamp '2000-01-01'")
    ).label("bucket")
    stmt = (
        scoped(f, "live", bucket, func.count().label("incoming"), func.count().filter(FLAGGED).label("flagged"))
        .group_by("bucket")
        .order_by("bucket")
    )
    return read(stmt)


def live_feed(f, limit=40):
    stmt = (
        scoped(
            f,
            "live",
            Transaction.scored_at.label("time"),
            (Customer.first_name + " " + Customer.last_name).label("customer"),
            Customer.state,
            Merchant.name.label("merchant"),
            Transaction.category,
            Transaction.amount,
            Transaction.fraud_score,
            Transaction.decision,
            Feedback.label.label("verdict"),
            Transaction.model_version,
        )
        .outerjoin(Feedback, Feedback.trans_num == Transaction.trans_num)
        .order_by(Transaction.scored_at.desc(), Transaction.fraud_score.desc())
        .limit(limit)
    )
    return read(stmt)


def live_scores(f, limit=5000):
    stmt = scoped(f, "live", Transaction.fraud_score, Transaction.decision).order_by(Transaction.scored_at.desc()).limit(limit)
    return read(stmt)


def live_by(f, dimension, name):
    stmt = (
        scoped(
            f,
            "live",
            dimension.label(name),
            func.count().label("transactions"),
            func.count().filter(FLAGGED).label("flagged"),
            func.coalesce(func.sum(Transaction.amount).filter(FLAGGED), 0).label("flagged_volume"),
        )
        .group_by(literal_column("1"))
        .order_by(literal_column("1"))
    )
    return read(stmt).assign(flag_rate=lambda d: d.flagged / d.transactions)


def risky_merchants(f, limit=8):
    flagged = func.count().filter(FLAGGED)
    stmt = (
        scoped(f, "live", Merchant.name.label("merchant"), Merchant.category, func.count().label("transactions"), flagged.label("flagged"))
        .group_by(Merchant.name, Merchant.category)
        .having(flagged > 0)
        .order_by(flagged.desc(), func.count().desc())
        .limit(limit)
    )
    return read(stmt)


def last_scored():
    with engine.connect() as conn:
        return conn.scalar(select(func.max(Transaction.scored_at)))


def live_flagged_points(f, limit=300):
    stmt = (
        scoped(f, "live", Transaction.merch_lat, Transaction.merch_long, Merchant.name.label("merchant"), Transaction.amount, Transaction.fraud_score)
        .where(FLAGGED)
        .order_by(Transaction.scored_at.desc())
        .limit(limit)
    )
    return read(stmt)


def unreviewed(f, *columns):
    return (
        scoped(replace(f, decision=None, minutes=None), "live", *columns)
        .outerjoin(Feedback, Feedback.trans_num == Transaction.trans_num)
        .where(FLAGGED, Feedback.trans_num.is_(None))
    )


def review_backlog(f):
    with engine.connect() as conn:
        return conn.scalar(unreviewed(f, func.count()))


def review_queue(f, limit=12):
    stmt = (
        unreviewed(
            f,
            Transaction.trans_num,
            Transaction.scored_at,
            Transaction.ts,
            (Customer.first_name + " " + Customer.last_name).label("customer"),
            Customer.city,
            Customer.state,
            Customer.job,
            Merchant.name.label("merchant"),
            Transaction.category,
            Transaction.amount,
            Transaction.fraud_score,
            Transaction.hour,
            Transaction.distance_km,
            Transaction.txn_count_24h,
            Transaction.amount_to_avg,
        )
        .order_by(Transaction.scored_at.desc())
        .limit(limit)
    )
    return read(stmt)


def label(trans_num, is_fraud):
    stmt = pg_insert(Feedback).values(trans_num=trans_num, label=int(is_fraud), source="analyst")
    stmt = stmt.on_conflict_do_update(
        index_elements=[Feedback.trans_num],
        set_={"label": stmt.excluded.label, "source": "analyst", "created_at": func.now()},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def history_kpis(f):
    stmt = scoped(
        f,
        "history",
        func.count().label("transactions"),
        func.count().filter(FRAUD).label("frauds"),
        func.coalesce(func.sum(Transaction.amount).filter(FRAUD), 0).label("fraud_volume"),
        func.avg(Transaction.amount).filter(FRAUD).label("avg_fraud"),
        func.avg(Transaction.amount).filter(~FRAUD).label("avg_legit"),
        func.count(distinct(Transaction.customer_id)).filter(FRAUD).label("cards_hit"),
    )
    return read(stmt).iloc[0]


def history_by(f, dimension, name, order=None):
    stmt = (
        scoped(
            f,
            "history",
            dimension.label(name),
            func.count().label("transactions"),
            func.count().filter(FRAUD).label("frauds"),
            func.coalesce(func.sum(Transaction.amount).filter(FRAUD), 0).label("fraud_volume"),
        )
        .group_by(literal_column("1"))
        .order_by(order if order is not None else literal_column("1"))
    )
    return read(stmt).assign(fraud_rate=lambda d: d.frauds / d.transactions)


def history_monthly(f):
    month = func.date_trunc(literal_column("'month'"), Transaction.ts)
    return history_by(f, month, "month")


def history_amount_bands(f):
    return history_by(f, AMOUNT_BAND, "band", order=func.min(Transaction.amount))


def history_top_jobs(f, limit=10):
    frauds = func.count().filter(FRAUD)
    stmt = (
        scoped(f, "history", Customer.job, func.count().label("transactions"), frauds.label("frauds"))
        .group_by(Customer.job)
        .order_by(frauds.desc())
        .limit(limit)
    )
    return read(stmt)


def model_versions():
    return read(select(ModelVersion.__table__).order_by(ModelVersion.version))
