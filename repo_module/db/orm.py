"""
SQLAlchemy ORM models.
Compatible with both PostgreSQL and SQLite.
"""
from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime,
    ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
    event, text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.types import TypeDecorator, String as SAString, JSON

from repo_module.db.base import Base
from repo_module.config import is_demo


# ─── Portable UUID type ───────────────────────────────────────────────────────

class PortableUUID(TypeDecorator):
    """UUID stored as TEXT in SQLite, native UUID in PostgreSQL."""
    impl = SAString(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(SAString(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return uuid.UUID(str(value)) if not isinstance(value, uuid.UUID) else value


# ─── Portable JSON type ───────────────────────────────────────────────────────

class PortableJSON(TypeDecorator):
    """JSONB in PostgreSQL, JSON in SQLite."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


# ─── ORM Tables ──────────────────────────────────────────────────────────────

class Instrument(Base):
    __tablename__ = "instruments"

    instrument_id = Column(String(32), primary_key=True)
    short_name = Column(String(64), nullable=False)
    instrument_type = Column(String(16), nullable=False)
    currency = Column(String(3), nullable=False)
    repo_eligible = Column(Boolean, nullable=False, default=True)
    settlement_mode = Column(String(4), nullable=False, default="T+0")
    day_count_convention = Column(String(16), nullable=False, default="ACT/365")
    is_active = Column(Boolean, nullable=False, default=True)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)


class Participant(Base):
    __tablename__ = "participants"

    participant_id = Column(String(32), primary_key=True)
    name = Column(String(128), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class RawTrade(Base):
    __tablename__ = "raw_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(128), nullable=False)
    source = Column(String(32), nullable=False, default="BATCH")
    payload = Column(PortableJSON, nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    processing_status = Column(String(16), nullable=False, default="PENDING")
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_raw_trades_ikey", "idempotency_key", "received_at", unique=True),
    )


class Trade(Base):
    __tablename__ = "trades"

    trade_id = Column(PortableUUID, primary_key=True, default=uuid.uuid4)
    external_trade_id = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    raw_trade_id = Column(Integer, ForeignKey("raw_trades.id", ondelete="SET NULL"), nullable=True)
    party_1_id = Column(String(32), nullable=False)
    party_2_id = Column(String(32), nullable=False)
    initiator_role = Column(String(20), nullable=False)
    participant_id = Column(String(32), nullable=False)
    counterparty_id = Column(String(32), nullable=False)
    instrument_id = Column(String(32), nullable=False)
    quantity = Column(Numeric(20, 6), nullable=False)
    leg1_amount = Column(Numeric(20, 2), nullable=False)
    leg2_amount = Column(Numeric(20, 2), nullable=True)
    rate = Column(Numeric(10, 6), nullable=False)
    trade_date = Column(Date, nullable=False)
    leg1_settlement_date = Column(Date, nullable=False)
    leg2_settlement_date = Column(Date, nullable=False)
    days_to_maturity = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="NEW")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_trades_ikey", "idempotency_key", unique=True),
        Index("idx_trades_ext", "external_trade_id", "trade_date", unique=True),
        Index("idx_trades_participant", "participant_id", "status"),
        Index("idx_trades_leg2", "leg2_settlement_date", "status"),
    )


class Position(Base):
    __tablename__ = "positions"

    position_id = Column(Integer, primary_key=True, autoincrement=True)
    participant_id = Column(String(32), nullable=False)
    instrument_id = Column(String(32), nullable=False)
    balance_type = Column(String(12), nullable=False)
    currency = Column(String(3), nullable=False)
    position_date = Column(Date, nullable=False)
    balance = Column(Numeric(20, 6), nullable=False, default=0)
    frozen_balance = Column(Numeric(20, 6), nullable=False, default=0)
    status = Column(String(8), nullable=False)
    inconsistent = Column(Boolean, nullable=False, default=False)
    calculated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("balance >= 0", name="chk_positive_balance"),
        CheckConstraint("balance_type IN ('SECURITIES','CASH')", name="chk_balance_type"),
        CheckConstraint("status IN ('SOD','EOD')", name="chk_position_status"),
        UniqueConstraint("participant_id", "instrument_id", "balance_type", "position_date", "status",
                         name="uq_position"),
        Index("idx_positions_lookup", "participant_id", "position_date", "status"),
    )


class FutureObligation(Base):
    __tablename__ = "future_obligations"

    obligation_id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(PortableUUID, ForeignKey("trades.trade_id"), nullable=False)
    participant_id = Column(String(32), nullable=False)
    instrument_id = Column(String(32), nullable=False)
    balance_type = Column(String(12), nullable=False)
    obligation_type = Column(String(32), nullable=False)
    obligation_date = Column(Date, nullable=False)
    quantity = Column(Numeric(20, 6), nullable=True)
    amount = Column(Numeric(20, 2), nullable=True)
    currency = Column(String(3), nullable=False)
    status = Column(String(12), nullable=False, default="PENDING")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_oblig_date", "obligation_date", "status"),
        Index("idx_oblig_part", "participant_id", "obligation_date"),
    )


class PositionAuditLog(Base):
    __tablename__ = "position_audit_log"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("positions.position_id"), nullable=True)
    participant_id = Column(String(32), nullable=False)
    instrument_id = Column(String(32), nullable=False)
    balance_type = Column(String(12), nullable=False)
    position_date = Column(Date, nullable=False)
    change_type = Column(String(32), nullable=False)
    delta = Column(Numeric(20, 6), nullable=False)
    balance_before = Column(Numeric(20, 6), nullable=False)
    balance_after = Column(Numeric(20, 6), nullable=False)
    trade_id = Column(PortableUUID, nullable=True)
    chunk_id = Column(Integer, nullable=True)
    operator = Column(String(64), nullable=True)
    prev_log_hash = Column(String(64), nullable=True)
    logged_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class RejectedTrade(Base):
    __tablename__ = "rejected_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(128), nullable=True)
    raw_trade_id = Column(Integer, ForeignKey("raw_trades.id", ondelete="SET NULL"), nullable=True)
    rejection_type = Column(String(32), nullable=False)
    rejection_detail = Column(Text, nullable=False)
    payload = Column(PortableJSON, nullable=False)
    chunk_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(64), nullable=True)


class LoadReport(Base):
    __tablename__ = "load_reports"

    report_id = Column(Integer, primary_key=True, autoincrement=True)
    eod_date = Column(Date, nullable=False)
    file_name = Column(String(256), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    total_records = Column(Integer, nullable=False, default=0)
    committed = Column(Integer, nullable=False, default=0)
    rejected = Column(Integer, nullable=False, default=0)
    duplicates = Column(Integer, nullable=False, default=0)
    failed_chunks = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="IN_PROGRESS")

    __table_args__ = (
        UniqueConstraint("eod_date", name="uq_load_report_eod_date"),
    )


# ─── SQLite triggers for CHECK constraints ────────────────────────────────────
# SQLite doesn't enforce CHECK constraints on UPDATE in older versions,
# so we add BEFORE INSERT/UPDATE triggers as a safety net.

def _setup_sqlite_triggers(conn, _):
    """Install SQLite triggers that emulate CHECK (balance >= 0)."""
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS trg_positions_balance_insert
        BEFORE INSERT ON positions
        FOR EACH ROW
        WHEN NEW.balance < 0
        BEGIN
            SELECT RAISE(ABORT, 'CHECK constraint failed: chk_positive_balance');
        END
    """))
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS trg_positions_balance_update
        BEFORE UPDATE ON positions
        FOR EACH ROW
        WHEN NEW.balance < 0
        BEGIN
            SELECT RAISE(ABORT, 'CHECK constraint failed: chk_positive_balance');
        END
    """))


def register_sqlite_events(engine):
    """Register SQLite-specific event listeners."""
    from sqlalchemy import event as sa_event

    @sa_event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
