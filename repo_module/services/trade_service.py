"""
TradeService: role mapping, validation, trade registration.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repo_module.db.orm import Instrument, Participant, RawTrade, Trade
from repo_module.models.domain import (
    IncomingTrade, InitiatorRole, RejectionType, TradeCreate, TradeStatus,
)
from repo_module.utils.calc import calculate_leg2_amount

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    def __init__(self, rejection_type: RejectionType, detail: str):
        self.rejection_type = rejection_type
        self.detail = detail
        super().__init__(detail)


class DuplicateError(Exception):
    pass


def map_roles(incoming: IncomingTrade) -> tuple[InitiatorRole, str, str]:
    """
    Map party_1/party_2 to participant/counterparty based on initiator_role.

    Returns:
        (resolved_initiator_role, participant_id, counterparty_id)

    Raises:
        ValidationError if initiator_role has an unrecognized value.
    """
    raw_role = incoming.initiator_role

    if raw_role is None:
        # FALLBACK: party_1 = SECURITY_SELLER by default
        logger.warning(
            "initiator_role missing, applying DEFAULT_SELLER fallback",
            extra={
                "operation": "role_mapping",
                "external_trade_id": incoming.external_trade_id,
                "applied_fallback": "DEFAULT_SELLER",
            },
        )
        return InitiatorRole.DEFAULT_SELLER, incoming.party_1, incoming.party_2

    role_upper = raw_role.strip().upper()

    if role_upper == "SECURITY_SELLER":
        return InitiatorRole.SECURITY_SELLER, incoming.party_1, incoming.party_2
    elif role_upper == "SECURITY_BUYER":
        return InitiatorRole.SECURITY_BUYER, incoming.party_2, incoming.party_1
    else:
        raise ValidationError(
            RejectionType.INITIATOR_ROLE_UNKNOWN,
            f"Unrecognized initiator_role value: '{raw_role}'",
        )


async def validate_trade(
    session: AsyncSession,
    incoming: IncomingTrade,
) -> None:
    """
    Validate trade against reference data.
    Raises ValidationError on failure.
    """
    # Check participant_1 exists
    p1 = await session.get(Participant, incoming.party_1)
    if not p1 or not p1.is_active:
        raise ValidationError(
            RejectionType.PARTICIPANT_NOT_FOUND,
            f"Participant '{incoming.party_1}' not found or inactive",
        )

    # Check participant_2 exists
    p2 = await session.get(Participant, incoming.party_2)
    if not p2 or not p2.is_active:
        raise ValidationError(
            RejectionType.PARTICIPANT_NOT_FOUND,
            f"Participant '{incoming.party_2}' not found or inactive",
        )

    # Check instrument exists and is repo_eligible
    instr = await session.get(Instrument, incoming.asset)
    if not instr or not instr.is_active:
        raise ValidationError(
            RejectionType.INSTRUMENT_NOT_ELIGIBLE,
            f"Instrument '{incoming.asset}' not found or inactive",
        )
    if not instr.repo_eligible:
        raise ValidationError(
            RejectionType.INSTRUMENT_NOT_ELIGIBLE,
            f"Instrument '{incoming.asset}' is not repo_eligible",
        )


async def check_duplicate(
    session: AsyncSession,
    idempotency_key: str,
) -> bool:
    """Return True if idempotency_key already exists in trades."""
    result = await session.execute(
        select(Trade.trade_id).where(Trade.idempotency_key == idempotency_key).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def build_trade_create(
    session: AsyncSession,
    incoming: IncomingTrade,
    raw_trade_id: Optional[int],
    eod_date: date,
) -> TradeCreate:
    """
    Build a TradeCreate object from an incoming trade.
    Performs role mapping and Leg2 calculation.
    """
    initiator_role, participant_id, counterparty_id = map_roles(incoming)

    idempotency_key = f"{incoming.external_trade_id}_{incoming.trade_date.isoformat()}"
    leg1_settlement_date = incoming.trade_date  # T+0

    # Get day_count_convention from instrument
    instr = await session.get(Instrument, incoming.asset)
    day_count_convention = instr.day_count_convention if instr else "ACT/365"

    leg2_amount, _, days = calculate_leg2_amount(
        leg1_amount=incoming.sum,
        rate=incoming.rate,
        leg1_settlement_date=leg1_settlement_date,
        leg2_settlement_date=incoming.maturity_date,
        day_count_convention=day_count_convention,
    )

    # Determine status: batch load → ACTIVE immediately (T+0)
    status = TradeStatus.ACTIVE if incoming.trade_date <= eod_date else TradeStatus.NEW

    return TradeCreate(
        external_trade_id=incoming.external_trade_id,
        idempotency_key=idempotency_key,
        raw_trade_id=raw_trade_id,
        party_1_id=incoming.party_1,
        party_2_id=incoming.party_2,
        initiator_role=initiator_role,
        participant_id=participant_id,
        counterparty_id=counterparty_id,
        instrument_id=incoming.asset,
        quantity=incoming.amount,
        leg1_amount=incoming.sum,
        leg2_amount=leg2_amount,
        rate=incoming.rate,
        trade_date=incoming.trade_date,
        leg1_settlement_date=leg1_settlement_date,
        leg2_settlement_date=incoming.maturity_date,
        days_to_maturity=days,
        status=status,
    )


async def insert_trade(session: AsyncSession, tc: TradeCreate) -> Trade:
    """Insert a trade record into the database."""
    trade = Trade(
        trade_id=uuid.uuid4(),
        external_trade_id=tc.external_trade_id,
        idempotency_key=tc.idempotency_key,
        raw_trade_id=tc.raw_trade_id,
        party_1_id=tc.party_1_id,
        party_2_id=tc.party_2_id,
        initiator_role=tc.initiator_role.value,
        participant_id=tc.participant_id,
        counterparty_id=tc.counterparty_id,
        instrument_id=tc.instrument_id,
        quantity=tc.quantity,
        leg1_amount=tc.leg1_amount,
        leg2_amount=tc.leg2_amount,
        rate=tc.rate,
        trade_date=tc.trade_date,
        leg1_settlement_date=tc.leg1_settlement_date,
        leg2_settlement_date=tc.leg2_settlement_date,
        days_to_maturity=tc.days_to_maturity,
        status=tc.status.value,
    )
    session.add(trade)
    await session.flush()
    return trade


async def cancel_trade(session: AsyncSession, trade_id: str, current_date: date) -> Trade:
    """
    Cancel a trade in NEW status.
    Raises ValueError if trade cannot be cancelled.
    """
    result = await session.execute(
        select(Trade).where(Trade.trade_id == trade_id)
    )
    trade = result.scalar_one_or_none()
    if trade is None:
        raise ValueError(f"Trade {trade_id} not found")
    if trade.status != TradeStatus.NEW.value:
        raise ValueError(f"Trade {trade_id} is in status {trade.status}, only NEW trades can be cancelled")
    if current_date >= trade.leg1_settlement_date:
        raise ValueError(
            f"Cannot cancel trade {trade_id}: current_date {current_date} >= leg1_settlement_date {trade.leg1_settlement_date}"
        )
    trade.status = TradeStatus.CANCELLED.value
    await session.flush()
    return trade
