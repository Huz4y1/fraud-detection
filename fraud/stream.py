import argparse
import time
from collections import deque
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fraud.config import GOLD
from fraud.db import Feedback, Transaction, engine
from fraud.model import active_version, load_active, score


def records(frame):
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def main():
    parser = argparse.ArgumentParser(description="Replay the gold stream split as live card transactions.")
    parser.add_argument("--rate", type=int, default=20, help="transactions per second")
    parser.add_argument("--label-delay", type=float, default=300, help="seconds before chargebacks and settlements reveal the true label")
    args = parser.parse_args()

    events = pd.read_parquet(GOLD / "fact_transactions", filters=[("split", "==", "stream")])
    events = events.drop(columns="split").sort_values(["ts", "trans_num"], ignore_index=True)
    with engine.connect() as conn:
        done = conn.scalar(select(func.count()).where(Transaction.source == "live"))
    events = events.iloc[done:]
    info, model = load_active()
    pending = deque()
    print(f"streaming {len(events):,} transactions at {args.rate}/s, scoring with model v{info.version}", flush=True)

    try:
        for batch_no, start in enumerate(range(0, len(events), args.rate)):
            tick = time.monotonic()
            if batch_no % 10 == 0 and active_version().version != info.version:
                info, model = load_active()
                print(f"feedback loop promoted model v{info.version}, now scoring with it", flush=True)

            batch = events.iloc[start : start + args.rate]
            scores = score(model, batch)
            now = datetime.now()
            live = batch.drop(columns="is_fraud").assign(
                source="live",
                fraud_score=scores.round(4),
                decision=np.where(scores >= info.threshold, "flagged", "approved"),
                model_version=info.version,
                scored_at=now,
            )
            due = []
            while pending and pending[0][0] <= now:
                due.append(pending.popleft()[1])
            with engine.begin() as conn:
                conn.execute(insert(Transaction), records(live))
                if due:
                    conn.execute(pg_insert(Feedback).values(due).on_conflict_do_nothing())

            reveal_at = now + timedelta(seconds=args.label_delay)
            pending.extend(
                (reveal_at, {"trans_num": t, "label": int(y), "source": "chargeback" if y else "settled"})
                for t, y in zip(batch.trans_num, batch.is_fraud)
            )
            if batch_no % 30 == 0:
                print(f"{done + start + len(batch):,} streamed, {len(due)} labels just arrived", flush=True)
            time.sleep(max(0.0, 1 - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print("stream stopped")


if __name__ == "__main__":
    main()
