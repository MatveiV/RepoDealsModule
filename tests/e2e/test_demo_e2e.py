"""
End-to-end test: full demo scenario over 3 trading days.
Verifies final positions, reports, and edge cases.
"""
import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio

from repo_module.batch.processor import run_batch_load
from repo_module.db.base import get_session
from repo_module.db.orm import (
    Instrument, LoadReport, Participant, Position, RejectedTrade, Trade,
)
from repo_module.services.position_service import copy_sod_to_eod, load_sod_balances
from sqlalchemy import func, select

TRADING_DAYS = [date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 10)]
DEMO_INCOMING = Path(__file__).parent.parent.parent / "demo_data" / "incoming"


def demo_data_available() -> bool:
    return (DEMO_INCOMING / "instruments.json").exists() and \
           (DEMO_INCOMING / "trades_2026-05-07.jsonl").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not demo_data_available(), reason="Demo data not generated. Run: python scripts/generate_demo_data.py")
async def test_full_demo_3_days(populated_db):
    """
    Full end-to-end test: process 3 trading days and verify results.
    """
    # Load reference data from demo files
    async with get_session() as session:
        async with session.begin():
            with open(DEMO_INCOMING / "instruments.json", encoding="utf-8") as f:
                instruments = json.load(f)
            for instr in instruments:
                existing = await session.get(Instrument, instr["instrument_id"])
                if not existing:
                    obj = Instrument(
                        instrument_id=instr["instrument_id"],
                        short_name=instr["short_name"],
                        instrument_type=instr["instrument_type"],
                        currency=instr["currency"],
                        repo_eligible=instr.get("repo_eligible", True),
                        settlement_mode=instr.get("settlement_mode", "T+0"),
                        day_count_convention=instr.get("day_count_convention", "ACT/365"),
                        is_active=instr.get("is_active", True),
                        valid_from=date.fromisoformat(instr["valid_from"]),
                    )
                    session.add(obj)

            with open(DEMO_INCOMING / "participants.json", encoding="utf-8") as f:
                participants = json.load(f)
            for p in participants:
                existing = await session.get(Participant, p["participant_id"])
                if not existing:
                    obj = Participant(
                        participant_id=p["participant_id"],
                        name=p["name"],
                        is_active=p.get("is_active", True),
                    )
                    session.add(obj)

    # Load SOD balances and process each day
    with open(DEMO_INCOMING / "sod_balances.json", encoding="utf-8") as f:
        all_sod = json.load(f)

    total_committed = 0
    total_rejected = 0
    total_duplicates = 0

    for td in TRADING_DAYS:
        day_sod = [b for b in all_sod if b.get("position_date") == td.isoformat()]

        async with get_session() as session:
            async with session.begin():
                await load_sod_balances(session, day_sod, td)
                await copy_sod_to_eod(session, td)

        file_path = DEMO_INCOMING / f"trades_{td.isoformat()}.jsonl"
        if file_path.exists():
            result = await run_batch_load(file_path, td)
            total_committed += result.get("committed", 0)
            total_rejected += result.get("rejected", 0)
            total_duplicates += result.get("duplicates", 0)

    # Assertions
    assert total_committed > 0, "Should have committed some trades"

    # Day 3 should have rejections (edge cases: unknown instrument, insufficient balance, bad role)
    async with get_session() as session:
        result = await session.execute(select(func.count()).select_from(RejectedTrade))
        rejected_count = result.scalar()
        assert rejected_count >= 2, f"Expected at least 2 rejected trades, got {rejected_count}"

    # Verify load reports exist for all 3 days
    async with get_session() as session:
        for td in TRADING_DAYS:
            result = await session.execute(
                select(LoadReport).where(LoadReport.eod_date == td)
            )
            report = result.scalar_one_or_none()
            assert report is not None, f"No load report for {td}"
            assert report.status in ("COMPLETED", "COMPLETED_WITH_ERRORS")

    # Verify positions exist for last day
    async with get_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Position).where(
                Position.position_date == TRADING_DAYS[-1],
                Position.status == "EOD",
            )
        )
        pos_count = result.scalar()
        assert pos_count > 0, "Should have EOD positions for last trading day"

    # Verify no negative balances
    async with get_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Position).where(Position.balance < 0)
        )
        neg_count = result.scalar()
        assert neg_count == 0, f"Found {neg_count} positions with negative balance!"

    print(f"\nE2E Summary: committed={total_committed}, rejected={total_rejected}, duplicates={total_duplicates}")


@pytest.mark.asyncio
async def test_positions_never_go_negative(populated_db):
    """Verify that the pre-check prevents negative balances in all scenarios."""
    from repo_module.services.position_service import apply_position_delta, InsufficientBalanceError

    trade_date = date(2026, 5, 7)

    async with get_session() as session:
        async with session.begin():
            # Set up position with 1000 units
            await load_sod_balances(session, [{
                "participant_id": "BANK_A",
                "instrument_id": "RU000A0ZYJT2",
                "balance_type": "SECURITIES",
                "currency": "RUB",
                "position_date": trade_date.isoformat(),
                "balance": 1000.0,
            }], trade_date)
            await copy_sod_to_eod(session, trade_date)

    # Try to deduct more than available
    async with get_session() as session:
        async with session.begin():
            with pytest.raises(InsufficientBalanceError):
                await apply_position_delta(
                    session,
                    participant_id="BANK_A",
                    instrument_id="RU000A0ZYJT2",
                    balance_type="SECURITIES",
                    currency="RUB",
                    position_date=trade_date,
                    delta=Decimal("-2000"),  # More than available
                    trade_id=None,
                    chunk_id=None,
                    change_type="TEST",
                )
