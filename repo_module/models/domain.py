"""
Domain models (Pydantic) for REPO Module.
"""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class InitiatorRole(str, Enum):
    SECURITY_SELLER = "SECURITY_SELLER"
    SECURITY_BUYER = "SECURITY_BUYER"
    DEFAULT_SELLER = "DEFAULT_SELLER"  # fallback


class TradeStatus(str, Enum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class BalanceType(str, Enum):
    SECURITIES = "SECURITIES"
    CASH = "CASH"


class PositionStatus(str, Enum):
    SOD = "SOD"
    EOD = "EOD"


class ObligationType(str, Enum):
    LEG2_PAY_CASH = "LEG2_PAY_CASH"
    LEG2_RECEIVE_CASH = "LEG2_RECEIVE_CASH"
    LEG2_RECEIVE_SECURITIES = "LEG2_RECEIVE_SECURITIES"
    LEG2_RETURN_SECURITIES = "LEG2_RETURN_SECURITIES"


class ObligationStatus(str, Enum):
    PENDING = "PENDING"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"


class RejectionType(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INITIATOR_ROLE_UNKNOWN = "INITIATOR_ROLE_UNKNOWN"
    DUPLICATE = "DUPLICATE"
    PARTICIPANT_NOT_FOUND = "PARTICIPANT_NOT_FOUND"
    INSTRUMENT_NOT_ELIGIBLE = "INSTRUMENT_NOT_ELIGIBLE"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    CHECK_VIOLATION = "CHECK_VIOLATION"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# ─── Input schema from Trading System ────────────────────────────────────────

class IncomingTrade(BaseModel):
    """JSON Lines record from Trading System."""
    external_trade_id: str = Field(..., max_length=64)
    party_1: str = Field(..., max_length=32)
    party_2: str = Field(..., max_length=32)
    initiator_role: Optional[str] = None  # SECURITY_SELLER | SECURITY_BUYER | absent
    asset: str = Field(..., max_length=32)
    amount: Decimal = Field(..., gt=0)
    sum: Decimal = Field(..., gt=0)
    rate: Decimal = Field(..., ge=0, le=5)
    trade_date: date
    maturity_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> "IncomingTrade":
        if self.maturity_date <= self.trade_date:
            raise ValueError("maturity_date must be > trade_date")
        if self.party_1 == self.party_2:
            raise ValueError("party_1 and party_2 must be different")
        return self


# ─── Internal trade model ─────────────────────────────────────────────────────

class TradeCreate(BaseModel):
    """Internal trade after role mapping."""
    external_trade_id: str
    idempotency_key: str
    raw_trade_id: Optional[int] = None
    party_1_id: str
    party_2_id: str
    initiator_role: InitiatorRole
    participant_id: str
    counterparty_id: str
    instrument_id: str
    quantity: Decimal
    leg1_amount: Decimal
    leg2_amount: Optional[Decimal] = None
    rate: Decimal
    trade_date: date
    leg1_settlement_date: date
    leg2_settlement_date: date
    days_to_maturity: int
    status: TradeStatus = TradeStatus.NEW


class TradeOut(BaseModel):
    model_config = {"from_attributes": True}

    trade_id: UUID
    external_trade_id: str
    idempotency_key: str
    party_1_id: str
    party_2_id: str
    initiator_role: str
    participant_id: str
    counterparty_id: str
    instrument_id: str
    quantity: Decimal
    leg1_amount: Decimal
    leg2_amount: Optional[Decimal]
    rate: Decimal
    trade_date: date
    leg1_settlement_date: date
    leg2_settlement_date: date
    days_to_maturity: int
    status: str
    created_at: datetime
    updated_at: datetime


# ─── Position models ──────────────────────────────────────────────────────────

class PositionOut(BaseModel):
    model_config = {"from_attributes": True}

    position_id: int
    participant_id: str
    instrument_id: str
    balance_type: str
    currency: str
    position_date: date
    balance: Decimal
    frozen_balance: Decimal
    status: str
    calculated_at: datetime


# ─── Future Obligation models ─────────────────────────────────────────────────

class ObligationOut(BaseModel):
    model_config = {"from_attributes": True}

    obligation_id: int
    trade_id: UUID
    participant_id: str
    instrument_id: str
    balance_type: str
    obligation_type: str
    obligation_date: date
    quantity: Optional[Decimal]
    amount: Optional[Decimal]
    currency: str
    status: str
    created_at: datetime


# ─── Load Report ──────────────────────────────────────────────────────────────

class LoadReportOut(BaseModel):
    model_config = {"from_attributes": True}

    report_id: int
    eod_date: date
    file_name: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]
    total_records: int
    committed: int
    rejected: int
    duplicates: int
    failed_chunks: int
    status: str


# ─── SOD Balance input ────────────────────────────────────────────────────────

class SodBalance(BaseModel):
    participant_id: str
    instrument_id: str
    balance_type: BalanceType
    currency: str
    position_date: date
    balance: Decimal


# ─── Instrument ───────────────────────────────────────────────────────────────

class InstrumentIn(BaseModel):
    instrument_id: str
    short_name: str
    instrument_type: str
    currency: str
    repo_eligible: bool = True
    settlement_mode: str = "T+0"
    day_count_convention: str = "ACT/365"
    is_active: bool = True
    valid_from: date
    valid_to: Optional[date] = None


# ─── Participant ──────────────────────────────────────────────────────────────

class ParticipantIn(BaseModel):
    participant_id: str
    name: str
    is_active: bool = True
