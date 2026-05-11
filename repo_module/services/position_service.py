"""
PositionService: EOD position calculation, pre-check, audit log.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repo_module.db.orm import (
    FutureObligation, Position, PositionAuditLog, Trade,
)
from repo_module.models.domain import BalanceType, ObligationType, TradeStatus
from repo_module.utils.hashing import compute_chain_hash

logger = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    def __init__(self, participant_id: str, instrument_id: str, balance_type: str,
                 current: Decimal, delta: Decimal):
        self.participant_id = participant_id
        self.instrument_id = instrument_id
        self.balance_type = balance_type
        self.current = current
        self.delta = delta
        super().__init__(
            f"INSUFFICIENT_BALANCE: {participant_id}/{instrument_id}/{balance_type} "
            f"current={current} delta={delta} would result in {current + delta}"
        )


async def get_or_create_position(
    session: AsyncSession,
    participant_id: str,
    instrument_id: str,
    balance_type: str,
    currency: str,
    position_date: date,
    status: str = "EOD",
) -> Position:
    """Get existing position or create a new one with zero balance."""
    result = await session.execute(
        select(Position).where(
            Position.participant_id == participant_id,
            Position.instrument_id == instrument_id,
            Position.balance_type == balance_type,
            Position.position_date == position_date,
            Position.status == status,
        ).with_for_update()
    )
    pos = result.scalar_one_or_none()
    if pos is None:
        pos = Position(
            participant_id=participant_id,
            instrument_id=instrument_id,
            balance_type=balance_type,
            currency=currency,
            position_date=position_date,
            balance=Decimal("0"),
            frozen_balance=Decimal("0"),
            status=status,
        )
        session.add(pos)
        await session.flush()
    return pos


async def apply_position_delta(
    session: AsyncSession,
    participant_id: str,
    instrument_id: str,
    balance_type: str,
    currency: str,
    position_date: date,
    delta: Decimal,
    trade_id: Optional[UUID],
    chunk_id: Optional[int],
    change_type: str,
    status: str = "EOD",
) -> Position:
    """
    Apply a delta to a position with pre-check and audit logging.

    Raises InsufficientBalanceError if balance would go negative.
    """
    pos = await get_or_create_position(
        session, participant_id, instrument_id, balance_type, currency, position_date, status
    )

    balance_before = Decimal(str(pos.balance))
    new_balance = balance_before + delta

    # PRE-CHECK: first line of defense
    if new_balance < Decimal("0"):
        raise InsufficientBalanceError(
            participant_id, instrument_id, balance_type, balance_before, delta
        )

    # Get last audit hash for chain
    last_hash_result = await session.execute(
        select(PositionAuditLog.prev_log_hash, PositionAuditLog.log_id)
        .where(
            PositionAuditLog.participant_id == participant_id,
            PositionAuditLog.instrument_id == instrument_id,
            PositionAuditLog.balance_type == balance_type,
        )
        .order_by(PositionAuditLog.log_id.desc())
        .limit(1)
    )
    last_row = last_hash_result.first()
    prev_hash = last_row[0] if last_row else None

    # Update position
    pos.balance = new_balance
    pos.calculated_at = datetime.utcnow()
    await session.flush()

    # Compute hash chain entry
    record_data = {
        "participant_id": participant_id,
        "instrument_id": instrument_id,
        "balance_type": balance_type,
        "position_date": position_date,
        "change_type": change_type,
        "delta": delta,
        "balance_before": balance_before,
        "balance_after": new_balance,
        "trade_id": str(trade_id) if trade_id else None,
    }
    chain_hash = compute_chain_hash(prev_hash, record_data)

    # Write audit log
    audit = PositionAuditLog(
        position_id=pos.position_id,
        participant_id=participant_id,
        instrument_id=instrument_id,
        balance_type=balance_type,
        position_date=position_date,
        change_type=change_type,
        delta=delta,
        balance_before=balance_before,
        balance_after=new_balance,
        trade_id=trade_id,
        chunk_id=chunk_id,
        prev_log_hash=chain_hash,
    )
    session.add(audit)
    await session.flush()

    return pos


async def apply_leg1_settlements(
    session: AsyncSession,
    trade: Trade,
    eod_date: date,
    chunk_id: Optional[int] = None,
) -> None:
    """
    Apply Leg 1 position movements for a trade.

    participant (SECURITY_SELLER):
        - SECURITIES: -quantity
        - CASH: +leg1_amount

    counterparty (SECURITY_BUYER):
        - SECURITIES: +quantity
        - CASH: -leg1_amount
    """
    quantity = Decimal(str(trade.quantity))
    leg1_amount = Decimal(str(trade.leg1_amount))
    trade_id = trade.trade_id

    # Determine currency for cash instrument
    cash_instrument = f"CASH_{_get_currency_for_instrument(session, trade.instrument_id)}"

    # participant: -securities, +cash
    await apply_position_delta(
        session, trade.participant_id, trade.instrument_id,
        BalanceType.SECURITIES.value, _get_instrument_currency(trade.instrument_id),
        eod_date, -quantity, trade_id, chunk_id, "LEG1_SECURITIES_OUT",
    )
    await apply_position_delta(
        session, trade.participant_id, cash_instrument,
        BalanceType.CASH.value, _extract_currency(cash_instrument),
        eod_date, leg1_amount, trade_id, chunk_id, "LEG1_CASH_IN",
    )

    # counterparty: +securities, -cash
    await apply_position_delta(
        session, trade.counterparty_id, trade.instrument_id,
        BalanceType.SECURITIES.value, _get_instrument_currency(trade.instrument_id),
        eod_date, quantity, trade_id, chunk_id, "LEG1_SECURITIES_IN",
    )
    await apply_position_delta(
        session, trade.counterparty_id, cash_instrument,
        BalanceType.CASH.value, _extract_currency(cash_instrument),
        eod_date, -leg1_amount, trade_id, chunk_id, "LEG1_CASH_OUT",
    )


async def create_future_obligations(
    session: AsyncSession,
    trade: Trade,
) -> None:
    """Create future obligation records for Leg 2."""
    leg2_amount = Decimal(str(trade.leg2_amount)) if trade.leg2_amount else Decimal("0")
    quantity = Decimal(str(trade.quantity))
    cash_instrument = f"CASH_{_get_instrument_currency(trade.instrument_id)}"
    currency = _extract_currency(cash_instrument)

    obligations = [
        # participant (SECURITY_SELLER): pays cash, receives securities
        FutureObligation(
            trade_id=trade.trade_id,
            participant_id=trade.participant_id,
            instrument_id=cash_instrument,
            balance_type=BalanceType.CASH.value,
            obligation_type=ObligationType.LEG2_PAY_CASH.value,
            obligation_date=trade.leg2_settlement_date,
            amount=leg2_amount,
            quantity=None,
            currency=currency,
            status="PENDING",
        ),
        FutureObligation(
            trade_id=trade.trade_id,
            participant_id=trade.participant_id,
            instrument_id=trade.instrument_id,
            balance_type=BalanceType.SECURITIES.value,
            obligation_type=ObligationType.LEG2_RECEIVE_SECURITIES.value,
            obligation_date=trade.leg2_settlement_date,
            amount=None,
            quantity=quantity,
            currency=currency,
            status="PENDING",
        ),
        # counterparty (SECURITY_BUYER): receives cash, returns securities
        FutureObligation(
            trade_id=trade.trade_id,
            participant_id=trade.counterparty_id,
            instrument_id=cash_instrument,
            balance_type=BalanceType.CASH.value,
            obligation_type=ObligationType.LEG2_RECEIVE_CASH.value,
            obligation_date=trade.leg2_settlement_date,
            amount=leg2_amount,
            quantity=None,
            currency=currency,
            status="PENDING",
        ),
        FutureObligation(
            trade_id=trade.trade_id,
            participant_id=trade.counterparty_id,
            instrument_id=trade.instrument_id,
            balance_type=BalanceType.SECURITIES.value,
            obligation_type=ObligationType.LEG2_RETURN_SECURITIES.value,
            obligation_date=trade.leg2_settlement_date,
            amount=None,
            quantity=quantity,
            currency=currency,
            status="PENDING",
        ),
    ]
    for ob in obligations:
        session.add(ob)
    await session.flush()


async def load_sod_balances(
    session: AsyncSession,
    balances: list[dict],
    position_date: date,
) -> int:
    """Load Start-of-Day balances into positions table."""
    count = 0
    for b in balances:
        if b.get("position_date"):
            pd = b["position_date"] if isinstance(b["position_date"], date) else date.fromisoformat(b["position_date"])
        else:
            pd = position_date

        # Check if SOD already exists
        result = await session.execute(
            select(Position).where(
                Position.participant_id == b["participant_id"],
                Position.instrument_id == b["instrument_id"],
                Position.balance_type == b["balance_type"],
                Position.position_date == pd,
                Position.status == "SOD",
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            continue  # idempotent

        pos = Position(
            participant_id=b["participant_id"],
            instrument_id=b["instrument_id"],
            balance_type=b["balance_type"],
            currency=b["currency"],
            position_date=pd,
            balance=Decimal(str(b["balance"])),
            frozen_balance=Decimal("0"),
            status="SOD",
        )
        session.add(pos)
        count += 1

    await session.flush()
    return count


async def copy_sod_to_eod(
    session: AsyncSession,
    position_date: date,
) -> int:
    """
    Copy SOD positions to EOD positions as starting point for the day.
    Returns number of positions copied.
    """
    result = await session.execute(
        select(Position).where(
            Position.position_date == position_date,
            Position.status == "SOD",
        )
    )
    sod_positions = result.scalars().all()
    count = 0
    for sod in sod_positions:
        # Check if EOD already exists
        eod_result = await session.execute(
            select(Position).where(
                Position.participant_id == sod.participant_id,
                Position.instrument_id == sod.instrument_id,
                Position.balance_type == sod.balance_type,
                Position.position_date == position_date,
                Position.status == "EOD",
            )
        )
        if eod_result.scalar_one_or_none():
            continue

        eod = Position(
            participant_id=sod.participant_id,
            instrument_id=sod.instrument_id,
            balance_type=sod.balance_type,
            currency=sod.currency,
            position_date=position_date,
            balance=sod.balance,
            frozen_balance=sod.frozen_balance,
            status="EOD",
        )
        session.add(eod)
        count += 1

    await session.flush()
    return count


def _get_instrument_currency(instrument_id: str) -> str:
    """Derive currency from instrument_id (simplified for demo)."""
    # For CASH instruments like CASH_RUB, CASH_USD
    if instrument_id.startswith("CASH_"):
        return instrument_id[5:]
    # Default to RUB for securities
    return "RUB"


def _get_currency_for_instrument(session, instrument_id: str) -> str:
    """Get currency for an instrument (sync fallback)."""
    return "RUB"  # simplified; in production, look up from instruments table


def _extract_currency(cash_instrument_id: str) -> str:
    """Extract currency from CASH_XXX instrument id."""
    if cash_instrument_id.startswith("CASH_"):
        return cash_instrument_id[5:]
    return "RUB"
