import json
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from fraud import queries as q
from fraud.feedback import retrain
from fraud.model import active_version

STATIC = Path(__file__).parent / "static"
router = APIRouter()


def rows(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def confusion(quality):
    def pick(decision, label):
        return int(quality.loc[(quality.decision == decision) & (quality.label == label), "n"].sum())

    return {"tp": pick("flagged", 1), "fp": pick("flagged", 0), "fn": pick("approved", 1), "tn": pick("approved", 0)}


def filters(
    category: list[str] = Query([]),
    state: list[str] = Query([]),
    min_amount: float | None = Query(None, ge=0),
    max_amount: float | None = Query(None, ge=0),
    decision: Literal["approved", "flagged"] | None = None,
    minutes: int | None = Query(None, ge=1),
):
    amount = None
    if min_amount is not None or max_amount is not None:
        amount = (min_amount or 0, float("inf") if max_amount is None else max_amount)
    return q.Filters(categories=tuple(category), states=tuple(state), amount=amount, decision=decision, minutes=minutes)


@router.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@router.get("/dashboard/options")
def options():
    categories, states = q.options()
    return {"categories": categories, "states": states}


@router.get("/dashboard/live")
def live(f: q.Filters = Depends(filters)):
    info = active_version()
    last = q.last_scored()
    return {
        "running": last is not None and (datetime.now() - last).total_seconds() < 10,
        "model_version": info.version,
        "threshold": info.threshold,
        "bucket_seconds": max(5, f.minutes or 60),
        "kpis": json.loads(q.live_kpis(f).to_json()),
        "quality": confusion(q.live_quality(f)),
        "timeline": rows(q.live_timeline(f)),
        "feed": rows(q.live_feed(f)),
        "categories": rows(q.live_by(f, q.Transaction.category, "category")),
        "merchants": rows(q.risky_merchants(f)),
    }


@router.get("/dashboard/review")
def review(f: q.Filters = Depends(filters)):
    return {"backlog": q.review_backlog(f), "queue": rows(q.review_queue(f, limit=25))}


@lru_cache(maxsize=64)
def history_data(f):
    return {
        "kpis": json.loads(q.history_kpis(f).to_json()),
        "hour": rows(q.history_by(f, q.Transaction.hour, "hour")),
        "band": rows(q.history_amount_bands(f)),
        "month": rows(q.history_monthly(f)),
        "category": rows(q.history_by(f, q.Transaction.category, "category")),
        "state": rows(q.history_by(f, q.Customer.state, "state")),
        "jobs": rows(q.history_top_jobs(f)),
    }


@router.get("/dashboard/history")
def history(f: q.Filters = Depends(filters)):
    return history_data(replace(f, decision=None, minutes=None))


@router.get("/dashboard/model")
def model():
    quality = q.live_quality(q.Filters())
    sources = {source: int(n) for source, n in quality.groupby("source").n.sum().items()}
    return {"versions": rows(q.model_versions()), "quality": confusion(quality), "sources": sources}


@router.post("/dashboard/retrain")
def retrain_now():
    return {"message": retrain()}
