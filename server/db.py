"""Storage. One process, many people — so every row is owned by a user.

SQLAlchemy rather than raw SQL for one reason: the URL is the only thing that
changes between a laptop (SQLite) and the hosted deployment (Postgres). Set
DATABASE_URL and nothing else moves.

What is deliberately NOT stored: the PDF. Once parse_api has read the rows out
of it the file has done its job, and holding other people's bank statements on
a disk is a liability that buys nothing. Only the extracted JSON survives.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Integer, String,
                        Text, UniqueConstraint, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Session, relationship

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./aifinance.db")
# Postgres URLs from most hosts start postgres://, which SQLAlchemy 2 rejects.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    future=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    name = Column(String(120), default="")
    # Null for a Google-only account that has not set one yet. The product
    # requires a password, so the sign-up flow never leaves this null.
    password_hash = Column(Text, nullable=True)
    google_sub = Column(String(64), unique=True, nullable=True, index=True)
    # The user's own rules — who they are, who pays them, whose money they hold.
    # Never shared, never defaulted from anyone else's.
    profile = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now)

    statements = relationship("Statement", back_populates="user",
                              cascade="all, delete-orphan")


class Statement(Base):
    __tablename__ = "statements"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # "ziraat-bankasi-bankkart-2026-08" — the same statement can only land once
    # per user, whatever the file was called.
    statement_id = Column(String(160), nullable=False)
    bank = Column(String(120), default="")
    period_end = Column(String(10), default="")
    sha256 = Column(String(64), default="")     # so a re-upload is recognised
    payload = Column(JSON, nullable=False)      # exactly what the dashboard reads
    created_at = Column(DateTime(timezone=True), default=now)

    user = relationship("User", back_populates="statements")
    __table_args__ = (UniqueConstraint("user_id", "statement_id",
                                       name="uq_user_statement"),)


class UserState(Base):
    """Every decision the dashboard stores (categories, matches, budgets...).

    One JSON blob per user, mirroring the aifp.* keys the page already writes,
    so the front end needs no new shape.
    """
    __tablename__ = "user_state"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    data = Column(JSON, default=dict)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


def init() -> None:
    Base.metadata.create_all(engine)


def session() -> Session:
    return Session(engine, future=True)


# ── helpers the API leans on ────────────────────────────────────────────────

def user_by_email(s: Session, email: str) -> User | None:
    return s.scalar(select(User).where(User.email == email.strip().lower()))


def user_statements(s: Session, user_id: int) -> list[Statement]:
    return list(s.scalars(
        select(Statement).where(Statement.user_id == user_id)
        .order_by(Statement.period_end.desc())))


def get_state(s: Session, user_id: int) -> dict:
    row = s.get(UserState, user_id)
    return dict(row.data or {}) if row else {}


def put_state(s: Session, user_id: int, data: dict) -> None:
    row = s.get(UserState, user_id)
    if row is None:
        s.add(UserState(user_id=user_id, data=data))
    else:
        row.data = data
        row.updated_at = now()
    s.commit()
