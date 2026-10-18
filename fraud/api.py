from functools import lru_cache
from typing import Literal

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from fraud.config import MODELS
from fraud.db import engine
from fraud.model import active_version, score

app = FastAPI(title="Fraud Detection API")

DIMENSIONS = {
    "category": "t.category",
    "state": "c.state",
    "gender": "c.gender",
    "hour": "t.hour",
    "day_of_week": "t.day_of_week",
    "month": "date_trunc('month', t.ts)",
}
ORDER = {"live": "t.scored_at desc", "history": "t.ts desc"}


class Features(BaseModel):
    amount: float = Field(gt=0)
    category: str
    hour: int = Field(ge=0, le=23)
    day_of_week: int = Field(ge=1, le=7)
    age: int = Field(ge=0, le=120)
    distance_km: float = Field(ge=0)
    txn_count_24h: int = Field(default=1, ge=1)
    amount_24h: float | None = None
    secs_since_last: float | None = None
    amount_to_avg: float | None = None


class Label(BaseModel):
    trans_num: str
    is_fraud: bool


def fetch(sql, **params):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params).mappings()]


@lru_cache(maxsize=8)
def load_model(path):
    return joblib.load(MODELS / path)


@app.get("/health")
def health():
    return fetch("select source, count(*) as transactions from transactions group by source order by source")


@app.get("/transactions")
def transactions(
    source: Literal["live", "history"] = "live",
    decision: Literal["approved", "flagged"] | None = None,
    category: str | None = None,
    state: str | None = None,
    min_amount: float = 0,
    max_amount: float = 1e12,
    limit: int = Query(50, ge=1, le=1000),
):
    sql = f"""
        select t.trans_num, t.ts, t.scored_at, t.amount, t.category, m.name as merchant,
               c.first_name || ' ' || c.last_name as customer, c.state,
               t.fraud_score, t.decision, t.is_fraud, f.label as feedback_label, f.source as feedback_source
        from transactions t
        join customers c on c.customer_id = t.customer_id
        join merchants m on m.merchant_id = t.merchant_id
        left join feedback f on f.trans_num = t.trans_num
        where t.source = :source
          and (cast(:decision as text) is null or t.decision = :decision)
          and (cast(:category as text) is null or t.category = :category)
          and (cast(:state as text) is null or c.state = :state)
          and t.amount between :min_amount and :max_amount
        order by {ORDER[source]}
        limit :limit
    """
    return fetch(sql, source=source, decision=decision, category=category, state=state,
                 min_amount=min_amount, max_amount=max_amount, limit=limit)


@app.get("/transactions/{trans_num}")
def transaction(trans_num: str):
    found = fetch(
        """
        select t.*, m.name as merchant, c.first_name, c.last_name, c.city, c.state, c.job, c.dob,
               f.label as feedback_label, f.source as feedback_source, f.created_at as feedback_at
        from transactions t
        join customers c on c.customer_id = t.customer_id
        join merchants m on m.merchant_id = t.merchant_id
        left join feedback f on f.trans_num = t.trans_num
        where t.trans_num = :trans_num
        """,
        trans_num=trans_num,
    )
    if not found:
        raise HTTPException(404, "transaction not found")
    return found[0]


@app.get("/customers/{customer_id}")
def customer(customer_id: str, limit: int = Query(20, ge=1, le=500)):
    profile = fetch(
        """
        select c.*, count(t.trans_num) as transactions, coalesce(sum(t.amount), 0) as total_spent,
               count(*) filter (where t.is_fraud = 1 or f.label = 1) as confirmed_frauds,
               count(*) filter (where t.decision = 'flagged') as flagged, max(t.ts) as last_transaction
        from customers c
        left join transactions t on t.customer_id = c.customer_id
        left join feedback f on f.trans_num = t.trans_num
        where c.customer_id = :customer_id
        group by c.customer_id
        """,
        customer_id=customer_id,
    )
    if not profile:
        raise HTTPException(404, "customer not found")
    recent = fetch(
        """
        select t.trans_num, t.ts, t.amount, t.category, m.name as merchant, t.source, t.fraud_score, t.decision, t.is_fraud
        from transactions t
        join merchants m on m.merchant_id = t.merchant_id
        where t.customer_id = :customer_id
        order by t.ts desc
        limit :limit
        """,
        customer_id=customer_id,
        limit=limit,
    )
    return {**profile[0], "recent": recent}


@app.get("/stats/live")
def live_stats(minutes: int = Query(15, ge=1, le=10080)):
    stats = fetch(
        """
        select count(*) as transactions,
               count(*) filter (where t.decision = 'flagged') as flagged,
               coalesce(sum(t.amount), 0) as volume,
               coalesce(sum(t.amount) filter (where t.decision = 'flagged'), 0) as flagged_volume,
               count(*) filter (where t.decision = 'flagged' and f.label = 1) as caught,
               count(*) filter (where t.decision = 'flagged' and f.label = 0) as false_alarms,
               count(*) filter (where t.decision = 'approved' and f.label = 1) as missed
        from transactions t
        left join feedback f on f.trans_num = t.trans_num
        where t.source = 'live' and t.scored_at >= localtimestamp - make_interval(mins => :minutes)
        """,
        minutes=minutes,
    )[0]
    flagged_labeled = stats["caught"] + stats["false_alarms"]
    frauds_labeled = stats["caught"] + stats["missed"]
    stats["precision"] = stats["caught"] / flagged_labeled if flagged_labeled else None
    stats["recall"] = stats["caught"] / frauds_labeled if frauds_labeled else None
    return stats


@app.get("/stats/fraud-by/{dimension}")
def fraud_by(dimension: Literal["category", "state", "gender", "hour", "day_of_week", "month"]):
    return fetch(
        f"""
        select {DIMENSIONS[dimension]} as {dimension}, count(*) as transactions,
               count(*) filter (where t.is_fraud = 1) as frauds,
               round(avg(t.is_fraud)::numeric, 5) as fraud_rate,
               coalesce(sum(t.amount) filter (where t.is_fraud = 1), 0) as fraud_amount
        from transactions t
        join customers c on c.customer_id = t.customer_id
        where t.source = 'history'
        group by 1
        order by 1
        """
    )


@app.get("/merchants/risky")
def risky_merchants(minutes: int = Query(60, ge=1, le=10080), limit: int = Query(10, ge=1, le=100)):
    return fetch(
        """
        select m.merchant_id, m.name, m.category, count(*) as transactions,
               count(*) filter (where t.decision = 'flagged') as flagged,
               round(avg(t.fraud_score)::numeric, 4) as avg_score
        from transactions t
        join merchants m on m.merchant_id = t.merchant_id
        where t.source = 'live' and t.scored_at >= localtimestamp - make_interval(mins => :minutes)
        group by m.merchant_id, m.name, m.category
        having count(*) filter (where t.decision = 'flagged') > 0
        order by flagged desc, transactions desc
        limit :limit
        """,
        minutes=minutes,
        limit=limit,
    )


@app.get("/models")
def models():
    return fetch("select * from model_versions order by version")


@app.post("/feedback")
def feedback(label: Label):
    if not fetch("select 1 from transactions where trans_num = :trans_num and source = 'live'", trans_num=label.trans_num):
        raise HTTPException(404, "live transaction not found")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                insert into feedback (trans_num, label, source) values (:trans_num, :label, 'analyst')
                on conflict (trans_num) do update set label = excluded.label, source = 'analyst', created_at = now()
                """
            ),
            {"trans_num": label.trans_num, "label": int(label.is_fraud)},
        )
    return {"trans_num": label.trans_num, "label": int(label.is_fraud), "source": "analyst"}


@app.post("/score")
def score_transaction(features: Features):
    info = active_version()
    row = features.model_dump()
    row["amount_24h"] = row["amount_24h"] or row["amount"]
    fraud_score = float(score(load_model(info.path), pd.DataFrame([row]))[0])
    return {
        "fraud_score": round(fraud_score, 4),
        "decision": "flagged" if fraud_score >= info.threshold else "approved",
        "threshold": info.threshold,
        "model_version": info.version,
    }

