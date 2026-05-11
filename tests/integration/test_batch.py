"""
Integration tests for batch processing with error isolation.
"""
import json
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio

from repo_module.batch.processor import BatchProcessor, run_batch_load
from repo_module.db.base import get_session
from repo_module.db.orm import Position, RejectedTrade, Trade
from repo_module.services.position_service import load_sod_balances, copy_sod_to_eod
from sqlalchemy import select


def make_trade_line(
    external_id: str,
    party_1: str = "BANK_A",
    party_2: str = "BANK_B",
    asset: str = "RU000A0ZYJT2",
    amount: int = 100,
    sum_val: float = 1_000_000.0,
    rate: float = 0.15,
    trade_date: str = "2026-05-07",
    maturity_date: str = "2026-05-14",
    initiator_role: str = "SECURITY_SELLER",
) -> str:
    data = {
        "external_trade_id": external_id,
        "party_1": party_1,
        "party_2": party_2,
        "initiator_role": initiator_role,
        "asset": asset,
        "amount": amount,
        "sum": sum_val,
        "rate": rate,
        "trade_date": trade_date,
        "maturity_date": maturity_date,
    }
    return json.dumps(data)


async def setup_sod(session, trade_date: date, participants: list[str], instruments: list[str]):
    """Set up SOD balances for test participants."""
    balances = []
    for pid in participants:
        # Cash balance
        balances.append({
            "participant_id": pid,
            "instrument_id": "CASH_RUB",
            "balance_type": "CASH",
            "currency": "RUB",
            "position_date": trade_date.isoformat(),
            "balance": 1_000_000_000.0,
        })
        # Securities balance
        for iid in instruments:
            balances.append({
                "participant_id": pid,
                "instrument_id": iid,
                "balance_type": "SECURITIES",
                "currency": "RUB",
                "position_date": trade_date.isoformat(),
                "balance": 100_000.0,
            })
    await load_sod_balances(session, balances, trade_date)
    await copy_sod_to_eod(session, trade_date)


@pytest.mark.asyncio
async def test_valid_trade_committed(populated_db):
    """A valid trade should be committed and positions updated."""
    trade_date = date(2026, 5, 7)

    async with get_session() as session:
        async with session.begin():
            await setup_sod(session, trade_date, ["BANK_A", "BANK_B"], ["RU000A0ZYJT2"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(make_trade_line("TEST-VALID-001") + "\n")
        fname = f.name

    try:
        result = await run_batch_load(fname, trade_date)
        assert result["committed"] == 1
        assert result["rejected"] == 0
        assert result["failed_chunks"] == 0

        # Verify trade exists in DB
        async with get_session() as session:
            result_q = await session.execute(
                select(Trade).where(Trade.external_trade_id == "TEST-VALID-001")
            )
            trade = result_q.scalar_one_or_none()
            assert trade is not None
            assert trade.status == "ACTIVE"
            assert trade.leg2_amount is not None
            assert Decimal(str(trade.leg2_amount)) > Decimal(str(trade.leg1_amount))
    finally:
        os.unlink(fname)


@pytest.mark.asyncio
async def test_rejected_trade_does_not_affect_valid_trades(populated_db):
    """
    A rejected trade (invalid instrument) should not prevent valid trades
    in the same chunk from being committed.
    """
    trade_date = date(2026, 5, 7)

    async with get_session() as session:
        async with session.begin():
            await setup_sod(session, trade_date, ["BANK_A", "BANK_B", "BANK_C"], ["RU000A0ZYJT2"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        # Valid trade
        f.write(make_trade_line("TEST-VALID-002", party_1="BANK_A", party_2="BANK_B") + "\n")
        # Invalid: unknown instrument
        f.write(make_trade_line("TEST-INVALID-001", asset="UNKNOWN_INSTRUMENT") + "\n")
        # Another valid trade
        f.write(make_trade_line("TEST-VALID-003", party_1="BANK_B", party_2="BANK_C") + "\n")
        fname = f.name

    try:
        result = await run_batch_load(fname, trade_date)
        assert result["committed"] == 2, f"Expected 2 committed, got {result['committed']}"
        assert result["rejected"] == 1, f"Expected 1 rejected, got {result['rejected']}"

        # Verify rejected trade is in rejected_trades
        async with get_session() as session:
            result_q = await session.execute(
                select(RejectedTrade).where(
                    RejectedTrade.idempotency_key.like("%TEST-INVALID-001%")
                )
            )
            rejected = result_q.scalar_one_or_none()
            assert rejected is not None
            assert "INSTRUMENT" in rejected.rejection_type
    finally:
        os.unlink(fname)


@pytest.mark.asyncio
async def test_duplicate_trade_skipped(populated_db):
    """Duplicate trade (same idempotency_key) should be skipped, not rejected."""
    trade_date = date(2026, 5, 7)

    async with get_session() as session:
        async with session.begin():
            await setup_sod(session, trade_date, ["BANK_A", "BANK_B"], ["RU000A0ZYJT2"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(make_trade_line("TEST-DUP-001") + "\n")
        fname = f.name

    try:
        # First load
        result1 = await run_batch_load(fname, trade_date)
        assert result1["committed"] == 1

        # Second load (same file = same idempotency_key)
        result2 = await run_batch_load(fname, trade_date)
        assert result2["duplicates"] == 1
        assert result2["committed"] == 0
    finally:
        os.unlink(fname)


@pytest.mark.asyncio
async def test_check_constraint_negative_balance(populated_db):
    """
    Direct insertion of negative balance should trigger CHECK constraint.
    """
    from sqlalchemy.exc import IntegrityError, DBAPIError

    async with get_session() as session:
        pos = Position(
            participant_id="BANK_A",
            instrument_id="RU000A0ZYJT2",
            balance_type="SECURITIES",
            currency="RUB",
            position_date=date(2026, 5, 7),
            balance=Decimal("-1.0"),  # NEGATIVE — should fail
            frozen_balance=Decimal("0"),
            status="EOD",
        )
        session.add(pos)
        with pytest.raises((IntegrityError, DBAPIError, Exception)) as exc_info:
            await session.flush()
        # Verify it's a constraint violation
        error_msg = str(exc_info.value).lower()
        assert "check" in error_msg or "constraint" in error_msg or "positive" in error_msg


@pytest.mark.asyncio
async def test_insufficient_balance_rejected(populated_db):
    """Trade with insufficient balance should be rejected with INSUFFICIENT_BALANCE."""
    trade_date = date(2026, 5, 7)

    # Set up tiny balance for BANK_A (as counterparty/buyer who pays cash)
    async with get_session() as session:
        async with session.begin():
            balances = [
                {
                    "participant_id": "BANK_A",
                    "instrument_id": "CASH_RUB",
                    "balance_type": "CASH",
                    "currency": "RUB",
                    "position_date": trade_date.isoformat(),
                    "balance": 100.0,  # Only 100 RUB
                },
                {
                    "participant_id": "BANK_B",
                    "instrument_id": "CASH_RUB",
                    "balance_type": "CASH",
                    "currency": "RUB",
                    "position_date": trade_date.isoformat(),
                    "balance": 1_000_000_000.0,
                },
                {
                    "participant_id": "BANK_B",
                    "instrument_id": "RU000A0ZYJT2",
                    "balance_type": "SECURITIES",
                    "currency": "RUB",
                    "position_date": trade_date.isoformat(),
                    "balance": 100_000.0,
                },
                {
                    "participant_id": "BANK_A",
                    "instrument_id": "RU000A0ZYJT2",
                    "balance_type": "SECURITIES",
                    "currency": "RUB",
                    "position_date": trade_date.isoformat(),
                    "balance": 100_000.0,
                },
            ]
            await load_sod_balances(session, balances, trade_date)
            await copy_sod_to_eod(session, trade_date)

    # BANK_B sells securities to BANK_A (BANK_A pays 95M RUB but has only 100)
    # SECURITY_SELLER=BANK_B (participant), BANK_A=counterparty (pays cash)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        trade = {
            "external_trade_id": "TEST-INSUF-001",
            "party_1": "BANK_B",
            "party_2": "BANK_A",
            "initiator_role": "SECURITY_SELLER",  # BANK_B=participant, BANK_A=counterparty (pays cash)
            "asset": "RU000A0ZYJT2",
            "amount": 1000,
            "sum": 95_000_000.0,  # BANK_A needs to pay 95M but has only 100
            "rate": 0.165,
            "trade_date": trade_date.isoformat(),
            "maturity_date": (trade_date + timedelta(days=7)).isoformat(),
        }
        f.write(json.dumps(trade) + "\n")
        fname = f.name

    try:
        result = await run_batch_load(fname, trade_date)
        assert result["rejected"] == 1, f"Expected 1 rejected, got {result}"

        async with get_session() as session:
            result_q = await session.execute(
                select(RejectedTrade).where(
                    RejectedTrade.rejection_type == "INSUFFICIENT_BALANCE"
                )
            )
            rejected = result_q.scalar_one_or_none()
            assert rejected is not None
    finally:
        os.unlink(fname)


@pytest.mark.asyncio
async def test_idempotency_repeated_load(populated_db):
    """Repeated load of the same file should produce same results (idempotent)."""
    trade_date = date(2026, 5, 8)

    async with get_session() as session:
        async with session.begin():
            await setup_sod(session, trade_date, ["BANK_A", "BANK_B"], ["RU000A0ZYJT2"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(make_trade_line("TEST-IDEM-001", trade_date="2026-05-08", maturity_date="2026-05-15") + "\n")
        f.write(make_trade_line("TEST-IDEM-002", party_1="BANK_B", party_2="BANK_A",
                                trade_date="2026-05-08", maturity_date="2026-05-15") + "\n")
        fname = f.name

    try:
        result1 = await run_batch_load(fname, trade_date)
        assert result1["committed"] == 2

        result2 = await run_batch_load(fname, trade_date)
        assert result2["duplicates"] == 2
        assert result2["committed"] == 0

        # Verify only 2 trades in DB (not 4)
        async with get_session() as session:
            result_q = await session.execute(
                select(Trade).where(Trade.trade_date == trade_date)
            )
            trades = result_q.scalars().all()
            assert len(trades) == 2
    finally:
        os.unlink(fname)
