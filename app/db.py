"""Database setup — Postgres on Railway via DATABASE_URL, SQLite fallback for local dev."""
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Integer, MetaData, String, Table, create_engine, text
)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local.db")

# Railway provides postgres:// — SQLAlchemy 2.x requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
metadata = MetaData()


def utcnow():
    return datetime.now(timezone.utc)


subscribers = Table(
    "subscribers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("source", String(40), nullable=False, default="landing"),  # landing | deposit
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

reservations = Table(
    "reservations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(200)),
    Column("email", String(320), nullable=False),
    Column("amount_cents", Integer, nullable=False, default=2500),
    Column("currency", String(10), nullable=False, default="usd"),
    Column("stripe_session_id", String(255), unique=True),
    Column("stripe_payment_intent", String(255)),
    Column("status", String(40), nullable=False, default="paid"),  # paid | refunded
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)


def init_db():
    metadata.create_all(engine)


def healthcheck() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
