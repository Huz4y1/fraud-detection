from datetime import date, datetime

from sqlalchemy import ForeignKey, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fraud.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    first_name: Mapped[str]
    last_name: Mapped[str]
    gender: Mapped[str] = mapped_column(String(1))
    street: Mapped[str]
    city: Mapped[str]
    state: Mapped[str] = mapped_column(String(2), index=True)
    zip: Mapped[str] = mapped_column(String(5))
    lat: Mapped[float]
    long: Mapped[float]
    city_pop: Mapped[int]
    job: Mapped[str]
    dob: Mapped[date]


class Merchant(Base):
    __tablename__ = "merchants"
    merchant_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str]
    category: Mapped[str] = mapped_column(index=True)


class Transaction(Base):
    __tablename__ = "transactions"
    trans_num: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.merchant_id"), index=True)
    category: Mapped[str] = mapped_column(index=True)
    amount: Mapped[float]
    merch_lat: Mapped[float]
    merch_long: Mapped[float]
    hour: Mapped[int]
    day_of_week: Mapped[int]
    age: Mapped[int]
    distance_km: Mapped[float]
    txn_count_24h: Mapped[int]
    amount_24h: Mapped[float]
    secs_since_last: Mapped[float | None]
    amount_to_avg: Mapped[float | None]
    is_fraud: Mapped[int | None]
    source: Mapped[str] = mapped_column(String(8), index=True)
    fraud_score: Mapped[float | None]
    decision: Mapped[str | None] = mapped_column(String(8))
    model_version: Mapped[int | None]
    scored_at: Mapped[datetime | None] = mapped_column(index=True)


class Feedback(Base):
    __tablename__ = "feedback"
    trans_num: Mapped[str] = mapped_column(ForeignKey("transactions.trans_num"), primary_key=True)
    label: Mapped[int]
    source: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ModelVersion(Base):
    __tablename__ = "model_versions"
    version: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    path: Mapped[str]
    threshold: Mapped[float]
    pr_auc: Mapped[float]
    roc_auc: Mapped[float]
    precision: Mapped[float]
    recall: Mapped[float]
    f1: Mapped[float]
    champion_pr_auc: Mapped[float | None]
    train_rows: Mapped[int]
    feedback_rows: Mapped[int]
    eval_rows: Mapped[int]
    promoted: Mapped[bool]
