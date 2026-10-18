# Fraud Detection Pipeline

End-to-end credit card fraud detection: a PySpark medallion pipeline (bronze, silver, gold), a PostgreSQL warehouse, a gradient boosting model, a live transaction stream that gets scored in real time, a FastAPI service with a plain HTML/CSS dashboard, and a feedback loop that retrains the model from analyst labels and chargebacks.

## Screenshots

**Live**: transactions streaming in, scored and flagged in real time, with filters for time window, decision, category, state and amount

![Live tab](docs/screenshots/live.png)

**Review**: flagged transactions waiting for an analyst to mark them as fraud or legit

![Review tab](docs/screenshots/review.png)

**History**: fraud patterns in the historical training data

![History tab](docs/screenshots/history.png)

**Model**: the production model, live precision and recall from feedback, and every retrain with whether it was promoted

![Model tab](docs/screenshots/model.png)

## Dataset

[Sparkov credit card transactions](https://www.kaggle.com/datasets/kartik2112/fraud-detection), a simulated dataset of card transactions from 952 customers across 700 merchants, downloaded from a [Hugging Face mirror](https://huggingface.co/SquareBracket/fraud_detection) so no Kaggle login is needed.

| File | Rows | Period | Used as |
|---|---|---|---|
| fraudTrain.csv | 377,116 | Jan 2019 - Jun 2019 | history (training data) |
| fraudTest.csv | 555,719 | Jun 2020 - Dec 2020 | live stream |

## Architecture

```mermaid
flowchart LR
    HF[Hugging Face CSVs] --> L[landing]
    L --> B[bronze: raw parquet]
    B --> S[silver: typed, validated, deduped]
    S --> Q[quarantine]
    S --> G[gold: star schema + features]
    G --> PG[(PostgreSQL)]
    G --> ST[stream simulator]
    PG --> TR[train model v1]
    TR --> REG[model registry]
    REG --> ST
    ST -->|scored transactions| PG
    ST -->|delayed chargebacks| PG
    PG --> API[FastAPI]
    API --> DASH[HTML/CSS dashboard]
    DASH -->|analyst labels| API
    PG --> FB[feedback retrain]
    FB -->|promote if better| REG
```

### Medallion layers (`fraud/medallion.py`)

| Layer | Path | Contents |
|---|---|---|
| landing | `data/landing/` | the raw CSV files |
| bronze | `data/lake/bronze/transactions` | raw rows as strings plus source file and ingestion time |
| silver | `data/lake/silver/transactions` | typed and cleaned rows, `fraud_` prefix stripped from merchants, zip codes padded, duplicates dropped |
| silver | `data/lake/silver/quarantine` | rows that failed a data quality rule, with the reason |
| gold | `data/lake/gold/dim_customer`, `dim_merchant` | dimension tables |
| gold | `data/lake/gold/fact_transactions` | fact table partitioned by `split` (history / stream) with engineered features |

Gold features are built with Spark window functions per card: transaction count and spend over the last 24 hours, seconds since the previous transaction, amount relative to the card's running average, plus hour, day of week, cardholder age and haversine distance between the cardholder and the merchant.

### Warehouse (`fraud/db.py`, `fraud/warehouse.py`)

SQLAlchemy models for `customers`, `merchants`, `transactions`, `feedback` and `model_versions`. Gold tables are bulk loaded with Postgres `COPY`. Live transactions are written to the same `transactions` table with `source = 'live'`, the fraud score, the decision and the model version that made it.

### Model (`fraud/model.py`)

`HistGradientBoostingClassifier` with an ordinal encoded category, trained on the first 85% of history by time and evaluated on the last 15%. The decision threshold is picked to maximise F1.

| Metric | Holdout |
|---|---|
| PR-AUC | 0.963 |
| ROC-AUC | 0.9998 |
| Precision | 0.929 |
| Recall | 0.864 |

### Live stream (`fraud/stream.py`)

Replays the stream split in time order at a fixed rate, scores every transaction with the current production model and writes it to Postgres. The true label is held back and only revealed after a delay, like a chargeback (fraud) or a settlement (legit) arriving later. The stream checks the registry every 10 seconds and switches to a newly promoted model on the fly.

### Feedback loop (`fraud/feedback.py`)

1. Analysts label flagged transactions in the dashboard (or `POST /feedback`), and chargebacks and settlements arrive from the stream.
2. A challenger is trained on history plus labeled live traffic. Live rows get 3x weight and rows the production model got wrong get 10x.
3. Challenger and champion are both scored on the most recent 30% of labeled live traffic.
4. The challenger is promoted only if its PR-AUC is higher. Every attempt is recorded in `model_versions`.

### Dashboard (`fraud/static/`, `fraud/web.py`)

Plain HTML, CSS and JavaScript with no frontend framework or chart library, served by the FastAPI app at `/`. Charts are drawn as SVG and CSS bars. The page fetches JSON from the `/dashboard/*` endpoints in `fraud/web.py`, which run the SQLAlchemy queries in `fraud/queries.py` (filters and joins across transactions, customers, merchants and feedback).

- **Live**: KPIs, transactions in vs flagged over time, latest transactions, flags by category, riskiest merchants. Refreshes every 2 seconds.
- **Review**: queue of flagged transactions with Fraud / Legit buttons that call `POST /feedback`
- **History**: fraud rate by hour, amount, category, month, state and occupation
- **Model**: production model metrics, live precision and recall from feedback, version history and a retrain button

Sidebar filters (time window, decision, category, state, amount) are sent as query parameters on every request.

### API (`fraud/api.py`)

The REST endpoints below run raw SQL through `sqlalchemy.text()` with bound parameters. Docs at `/docs`.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | row counts per source |
| GET | `/transactions` | filter by source, decision, category, state, amount |
| GET | `/transactions/{trans_num}` | one transaction with customer, merchant and feedback |
| GET | `/customers/{customer_id}` | customer profile, totals and recent transactions |
| GET | `/stats/live` | live KPIs, precision and recall for the last N minutes |
| GET | `/stats/fraud-by/{dimension}` | historical fraud rate by category, state, gender, hour, day_of_week or month |
| GET | `/merchants/risky` | merchants with the most flags in the last N minutes |
| GET | `/models` | model registry |
| POST | `/feedback` | label a live transaction as fraud or legit |
| POST | `/score` | score a transaction with the production model |

The dashboard uses its own endpoints: `GET /dashboard/options`, `/dashboard/live`, `/dashboard/review`, `/dashboard/history`, `/dashboard/model` and `POST /dashboard/retrain`.

```bash
curl -X POST localhost:8000/score -H "Content-Type: application/json" \
  -d '{"amount": 950.5, "category": "shopping_net", "hour": 23, "day_of_week": 6, "age": 61, "distance_km": 80, "txn_count_24h": 7, "amount_24h": 2100, "secs_since_last": 600, "amount_to_avg": 12.4}'
```

## Running it

Needs Python 3.11+, Java 17+ (for Spark) and PostgreSQL.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/fraud

python -m fraud.pipeline             # download, bronze/silver/gold, load Postgres, train model v1
```

Then in separate terminals:

```bash
python -m fraud.stream                 # live transactions
uvicorn fraud.api:app                  # dashboard on localhost:8000, API docs on localhost:8000/docs
python -m fraud.feedback --every 600   # optional: retrain every 10 minutes
```

Add `--port 8001` to the uvicorn command if port 8000 is taken.

Each pipeline step can also be run on its own: `fraud.download`, `fraud.medallion`, `fraud.warehouse`, `fraud.model`.

| Setting | Default |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/fraud` |
| `DATA_DIR` | `./data` |
| `MODEL_DIR` | `./models` |
| `stream --rate` | 20 transactions per second |
| `stream --label-delay` | 300 seconds until true labels arrive |

On Windows, Spark normally needs `winutils.exe` to write files. The Spark session downloads a small pure Java local filesystem jar from Maven Central instead, so nothing extra has to be installed.

## Tests

```bash
python -m pytest
```

Runs the silver and gold transformations on a small in-memory dataset and checks data quality rules, deduplication and the window features.
