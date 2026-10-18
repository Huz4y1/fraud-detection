import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, f1_score, precision_recall_curve, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OrdinalEncoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fraud.config import MODELS
from fraud.db import ModelVersion, Transaction, engine

FEATURES = [
    "amount",
    "category",
    "hour",
    "day_of_week",
    "age",
    "distance_km",
    "txn_count_24h",
    "amount_24h",
    "secs_since_last",
    "amount_to_avg",
]


def build():
    encode = ColumnTransformer(
        [("category", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan), ["category"])],
        remainder="passthrough",
        verbose_feature_names_out=False,
    ).set_output(transform="pandas")
    classifier = HistGradientBoostingClassifier(
        max_iter=400,
        learning_rate=0.06,
        max_leaf_nodes=48,
        l2_regularization=1.0,
        categorical_features=["category"],
        early_stopping=False,
        random_state=7,
    )
    return make_pipeline(encode, classifier)


def fit(frame, weights=None):
    return build().fit(frame[FEATURES], frame.label, histgradientboostingclassifier__sample_weight=weights)


def score(model, frame):
    return model.predict_proba(frame[FEATURES])[:, 1]


def best_threshold(labels, scores):
    precision, recall, thresholds = precision_recall_curve(labels, scores)
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-9)
    return float(thresholds[np.argmax(f1[:-1])])


def evaluate(model, frame, threshold=None):
    scores = score(model, frame)
    threshold = best_threshold(frame.label, scores) if threshold is None else threshold
    flagged = scores >= threshold
    return {
        "threshold": threshold,
        "pr_auc": float(average_precision_score(frame.label, scores)),
        "roc_auc": float(roc_auc_score(frame.label, scores)),
        "precision": float(precision_score(frame.label, flagged, zero_division=0)),
        "recall": float(recall_score(frame.label, flagged)),
        "f1": float(f1_score(frame.label, flagged)),
    }


def labeled_history():
    columns = [Transaction.trans_num, Transaction.ts, *(getattr(Transaction, f) for f in FEATURES)]
    stmt = select(*columns, Transaction.is_fraud.label("label")).where(Transaction.source == "history").order_by(Transaction.ts)
    return pd.read_sql(stmt, engine)


def register(model, metrics, **info):
    MODELS.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        version = (session.scalar(select(func.max(ModelVersion.version))) or 0) + 1
        path = f"model_v{version}.joblib"
        joblib.dump(model, MODELS / path)
        session.add(ModelVersion(version=version, path=path, **metrics, **info))
        session.commit()
    return version


def active_version():
    with Session(engine) as session:
        return session.scalars(
            select(ModelVersion).where(ModelVersion.promoted).order_by(ModelVersion.version.desc()).limit(1)
        ).first()


def load_active():
    info = active_version()
    if info is None:
        raise SystemExit("No trained model yet. Run: python -m fraud.model")
    return info, joblib.load(MODELS / info.path)


def main():
    data = labeled_history()
    cut = int(len(data) * 0.85)
    train, holdout = data.iloc[:cut], data.iloc[cut:]
    model = fit(train)
    metrics = evaluate(model, holdout)
    version = register(model, metrics, train_rows=len(train), eval_rows=len(holdout), feedback_rows=0, promoted=True)
    print(
        f"model v{version}: PR-AUC {metrics['pr_auc']:.4f}  ROC-AUC {metrics['roc_auc']:.4f}  "
        f"precision {metrics['precision']:.3f}  recall {metrics['recall']:.3f}  threshold {metrics['threshold']:.3f}"
    )


if __name__ == "__main__":
    main()
