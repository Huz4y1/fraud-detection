import argparse
import time

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from sqlalchemy import select
from sqlalchemy.orm import Session

from fraud.db import Feedback, ModelVersion, Transaction, engine
from fraud.model import FEATURES, evaluate, fit, labeled_history, load_active, register, score

MIN_FRAUDS = 30
RECENT_WEIGHT = 3.0
MISTAKE_WEIGHT = 10.0


def labeled_live():
    columns = [Transaction.trans_num, Transaction.ts, *(getattr(Transaction, f) for f in FEATURES)]
    stmt = (
        select(*columns, Transaction.decision, Feedback.label, Feedback.source.label("label_source"))
        .join(Feedback, Feedback.trans_num == Transaction.trans_num)
        .where(Transaction.source == "live")
        .order_by(Transaction.ts)
    )
    return pd.read_sql(stmt, engine)


def latest_version():
    with Session(engine) as session:
        return session.scalars(select(ModelVersion).order_by(ModelVersion.version.desc()).limit(1)).first()


def retrain():
    live = labeled_live()
    frauds = int(live.label.sum())
    if frauds < MIN_FRAUDS:
        return f"Waiting for feedback: {frauds}/{MIN_FRAUDS} confirmed frauds so far ({len(live):,} labels total)."
    last = latest_version()
    if last.feedback_rows == len(live):
        return f"No new feedback since v{last.version}."

    cut = int(len(live) * 0.7)
    recent, holdout = live.iloc[:cut], live.iloc[cut:]
    if holdout.label.nunique() < 2:
        return "Holdout window has only one class yet, waiting for more feedback."
    mistakes = (recent.decision == "flagged") != (recent.label == 1)
    history = labeled_history()
    train = pd.concat([history, recent], ignore_index=True)
    weights = np.concatenate([np.ones(len(history)), np.where(mistakes, MISTAKE_WEIGHT, RECENT_WEIGHT)])

    candidate = fit(train, weights)
    champion_info, champion = load_active()
    champion_pr_auc = float(average_precision_score(holdout.label, score(champion, holdout)))
    metrics = evaluate(candidate, holdout)
    promoted = metrics["pr_auc"] > champion_pr_auc
    version = register(
        candidate,
        metrics,
        champion_pr_auc=champion_pr_auc,
        train_rows=len(train),
        eval_rows=len(holdout),
        feedback_rows=len(live),
        promoted=promoted,
    )
    verdict = "promoted to production" if promoted else f"rejected, v{champion_info.version} stays in production"
    return (
        f"v{version} trained on {len(history):,} history + {len(recent):,} feedback rows "
        f"({int(mistakes.sum())} past mistakes upweighted). Holdout PR-AUC {metrics['pr_auc']:.4f} "
        f"vs champion v{champion_info.version} {champion_pr_auc:.4f}: {verdict}."
    )


def main():
    parser = argparse.ArgumentParser(description="Retrain on analyst and chargeback feedback, promote if it beats the champion.")
    parser.add_argument("--every", type=float, help="keep retraining every N seconds")
    args = parser.parse_args()
    while True:
        print(retrain(), flush=True)
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
