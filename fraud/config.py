import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("DATA_DIR", ROOT / "data"))
LANDING = DATA / "landing"
LAKE = DATA / "lake"
BRONZE = LAKE / "bronze"
SILVER = LAKE / "silver"
GOLD = LAKE / "gold"
MODELS = Path(os.getenv("MODEL_DIR", ROOT / "models"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/fraud")

SOURCE_URL = "https://huggingface.co/SquareBracket/fraud_detection/resolve/main"
SOURCE_FILES = ["fraudTrain.csv", "fraudTest.csv"]
STREAM_FROM = "2020-01-01"
