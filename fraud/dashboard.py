from dataclasses import replace
from datetime import datetime

import streamlit as st

from fraud import queries as q
from fraud.feedback import retrain
from fraud.model import active_version

st.set_page_config(page_title="Fraud Detection Dashboard", layout="wide")

WINDOWS = {"5 min": 5, "15 min": 15, "1 hour": 60, "All": None}
DECISIONS = {"All": None, "Approved": "approved", "Flagged": "flagged"}
AMOUNT_CAP = 2000


def confusion(quality):
    def pick(decision, label):
        return int(quality.loc[(quality.decision == decision) & (quality.label == label), "n"].sum())

    return pick("flagged", 1), pick("flagged", 0), pick("approved", 1), pick("approved", 0)


@st.cache_data(ttl=600)
def filter_options():
    return q.options()


@st.cache_data(ttl=600)
def history(f):
    return dict(
        kpis=q.history_kpis(f),
        hour=q.history_by(f, q.Transaction.hour, "hour"),
        category=q.history_by(f, q.Transaction.category, "category"),
        state=q.history_by(f, q.Customer.state, "state"),
        band=q.history_amount_bands(f),
        month=q.history_monthly(f),
        jobs=q.history_top_jobs(f),
    )


categories, states = filter_options()
with st.sidebar:
    st.header("Filters")
    window = st.segmented_control("Time window", list(WINDOWS), default="15 min", required=True)
    decision = st.segmented_control("Decision", list(DECISIONS), default="All", required=True)
    picked_categories = st.pills("Category", categories, selection_mode="multi")
    picked_states = st.multiselect("State", states)
    low, high = st.slider("Amount ($)", 0, AMOUNT_CAP, (0, AMOUNT_CAP), step=10)

filters = q.Filters(
    categories=tuple(picked_categories),
    states=tuple(picked_states),
    amount=None if (low, high) == (0, AMOUNT_CAP) else (low, high if high < AMOUNT_CAP else float("inf")),
    decision=DECISIONS[decision],
    minutes=WINDOWS[window],
)


@st.fragment(run_every="2s")
def live(f):
    info = active_version()
    last = q.last_scored()
    running = last is not None and (datetime.now() - last).total_seconds() < 10
    st.write(f"Stream: {'running' if running else 'stopped'} | Model: v{info.version} | Threshold: {info.threshold:.2f}")

    k = q.live_kpis(f)
    cols = st.columns(5)
    cols[0].metric("Transactions", f"{int(k.transactions):,}")
    cols[1].metric("Flagged", f"{int(k.flagged):,}")
    cols[2].metric("Flag rate", f"{k.flagged / k.transactions:.2%}" if k.transactions else "-")
    cols[3].metric("Volume", f"${k.volume:,.0f}")
    cols[4].metric("Flagged amount", f"${k.flagged_volume:,.0f}")

    timeline = q.live_timeline(f).set_index("bucket")
    left, right = st.columns(2)
    left.subheader("Transactions in")
    left.line_chart(timeline.incoming, height=250)
    right.subheader("Flagged")
    right.bar_chart(timeline.flagged, color="#d62728", height=250)

    st.subheader("Latest transactions")
    feed = q.live_feed(f)
    feed["verdict"] = feed.verdict.map({1: "fraud", 0: "legit"}).fillna("")
    st.dataframe(feed, hide_index=True, height=350, column_config={"fraud_score": st.column_config.NumberColumn(format="%.3f")})

    left, right = st.columns(2)
    left.subheader("Flagged by category")
    by_category = q.live_by(f, q.Transaction.category, "category")
    left.bar_chart(by_category, x="category", y="flagged", horizontal=True, height=400)
    right.subheader("Flagged transaction locations")
    points = q.live_flagged_points(f)
    right.map(points, latitude="merch_lat", longitude="merch_long", color="#d62728", size=8000, height=400)

    st.subheader("Merchants with most flags")
    st.dataframe(q.risky_merchants(f), hide_index=True)


@st.fragment
def review(f):
    st.write(f"{q.review_backlog(f):,} flagged transactions waiting for review")
    if st.button("Refresh"):
        st.rerun(scope="fragment")
    queue = q.review_queue(f)
    if queue.empty:
        st.write("Nothing to review.")
        return
    widths = [3, 3, 1, 1, 1, 1]
    for col, title in zip(st.columns(widths), ["Merchant", "Customer", "Amount", "Score"]):
        col.markdown(f"**{title}**")
    for row in queue.itertuples():
        cols = st.columns(widths)
        cols[0].write(f"{row.merchant} ({row.category})")
        cols[1].write(f"{row.customer}, {row.city} {row.state}")
        cols[2].write(f"${row.amount:,.2f}")
        cols[3].write(f"{row.fraud_score:.2f}")
        if cols[4].button("Fraud", key=f"fraud_{row.trans_num}"):
            q.label(row.trans_num, True)
            st.rerun(scope="fragment")
        if cols[5].button("Legit", key=f"legit_{row.trans_num}"):
            q.label(row.trans_num, False)
            st.rerun(scope="fragment")


def history_page(f):
    h = history(replace(f, decision=None, minutes=None))
    k = h["kpis"]
    cols = st.columns(4)
    cols[0].metric("Transactions", f"{int(k.transactions):,}")
    cols[1].metric("Frauds", f"{int(k.frauds):,}")
    cols[2].metric("Fraud rate", f"{k.frauds / k.transactions:.2%}" if k.transactions else "-")
    cols[3].metric("Fraud amount", f"${k.fraud_volume:,.0f}")
    if not k.transactions:
        return

    left, right = st.columns(2)
    left.subheader("Fraud rate by hour")
    left.bar_chart(h["hour"], x="hour", y="fraud_rate", sort=False)
    right.subheader("Fraud rate by amount")
    right.bar_chart(h["band"], x="band", y="fraud_rate", sort=False)

    left, right = st.columns(2)
    left.subheader("Frauds per month")
    left.line_chart(h["month"], x="month", y="frauds")
    right.subheader("Fraud rate by category")
    right.bar_chart(h["category"].sort_values("fraud_rate", ascending=False), x="category", y="fraud_rate", horizontal=True, sort=False)

    left, right = st.columns(2)
    left.subheader("Fraud rate by state")
    left.dataframe(h["state"].sort_values("fraud_rate", ascending=False), hide_index=True, height=400)
    right.subheader("Top occupations by frauds")
    right.dataframe(h["jobs"], hide_index=True, height=400)


def model_page():
    versions = q.model_versions()
    champion = versions[versions.promoted].iloc[-1]
    tp, fp, fn, tn = confusion(q.live_quality(q.Filters()))
    cols = st.columns(4)
    cols[0].metric("Production model", f"v{champion.version}")
    cols[1].metric("PR-AUC", f"{champion.pr_auc:.4f}")
    cols[2].metric("Live precision", f"{tp / (tp + fp):.1%}" if tp + fp else "-")
    cols[3].metric("Live recall", f"{tp / (tp + fn):.1%}" if tp + fn else "-")
    st.write(f"Caught fraud: {tp:,} | False alarms: {fp:,} | Missed fraud: {fn:,} | Correctly approved: {tn:,}")

    if st.button("Retrain model with feedback"):
        with st.spinner("Training..."):
            st.session_state.retrain_result = retrain()
    if "retrain_result" in st.session_state:
        st.write(st.session_state.retrain_result)

    st.subheader("PR-AUC by version")
    chart = versions.assign(version="v" + versions.version.astype(str)).rename(columns={"pr_auc": "new model", "champion_pr_auc": "production model"})
    st.bar_chart(chart, x="version", y=["new model", "production model"], stack=False, sort=False)
    st.subheader("Model versions")
    st.dataframe(versions, hide_index=True)


st.title("Fraud Detection Dashboard")
live_tab, review_tab, history_tab, model_tab = st.tabs(["Live", "Review", "History", "Model"])
with live_tab:
    live(filters)
with review_tab:
    review(filters)
with history_tab:
    history_page(filters)
with model_tab:
    model_page()
