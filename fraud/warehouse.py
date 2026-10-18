import io

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from fraud.config import DATABASE_URL, GOLD
from fraud.db import Base, engine


def ensure_database():
    url = make_url(DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("select 1 from pg_database where datname = :name"), {"name": url.database}):
            conn.execute(text(f'create database "{url.database}"'))
    admin.dispose()


def copy(conn, table, frame):
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, header=False)
    columns = ", ".join(frame.columns)
    with conn.connection.cursor() as cursor, cursor.copy(f"copy {table} ({columns}) from stdin with (format csv)") as stream:
        stream.write(buffer.getvalue())


def main():
    ensure_database()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    history = pd.read_parquet(GOLD / "fact_transactions", filters=[("split", "==", "history")])
    tables = {
        "customers": pd.read_parquet(GOLD / "dim_customer"),
        "merchants": pd.read_parquet(GOLD / "dim_merchant"),
        "transactions": history.drop(columns="split").assign(source="history"),
    }
    with engine.begin() as conn:
        for table, frame in tables.items():
            copy(conn, table, frame)
            print(f"loaded {len(frame):,} rows into {table}")


if __name__ == "__main__":
    main()
