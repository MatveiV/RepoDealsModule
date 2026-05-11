"""
Demo runner script.
Executes the full demo scenario:
1. Initialize DB
2. Load reference data
3. Process 3 trading days
4. Run API demo queries
5. Generate EOD CSV report

Usage:
    python scripts/run_demo.py
    # or via make:
    make demo
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("MODE", "demo")

from repo_module.config import get_config, get_incoming_dir, get_output_dir
from repo_module.db.base import init_db, get_session
from repo_module.db.orm import Instrument, Participant, register_sqlite_events
from repo_module.db.base import get_engine
from repo_module.batch.processor import run_batch_load
from repo_module.services.position_service import load_sod_balances, copy_sod_to_eod
from repo_module.utils.logging_setup import setup_logging
from sqlalchemy import select

TRADING_DAYS = [
    date(2026, 5, 7),
    date(2026, 5, 8),
    date(2026, 5, 10),
]

INCOMING_DIR = Path(get_incoming_dir())
OUTPUT_DIR = Path(get_output_dir())
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def print_banner(msg: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {msg}")
    print("=" * 60)


def print_step(step: int, msg: str) -> None:
    print(f"\n[Step {step}] {msg}")


async def load_reference_data(session_factory) -> None:
    """Load instruments and participants from JSON files."""
    async with get_session() as session:
        async with session.begin():
            # Load instruments
            with open(INCOMING_DIR / "instruments.json", encoding="utf-8") as f:
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
                        valid_to=date.fromisoformat(instr["valid_to"]) if instr.get("valid_to") else None,
                    )
                    session.add(obj)

            # Load participants
            with open(INCOMING_DIR / "participants.json", encoding="utf-8") as f:
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

    print(f"  Loaded {len(instruments)} instruments, {len(participants)} participants")


async def load_sod_for_day(trade_date: date) -> int:
    """Load SOD balances for a specific trading day."""
    with open(INCOMING_DIR / "sod_balances.json", encoding="utf-8") as f:
        all_balances = json.load(f)

    day_balances = [
        b for b in all_balances
        if b.get("position_date") == trade_date.isoformat()
    ]

    async with get_session() as session:
        async with session.begin():
            count = await load_sod_balances(session, day_balances, trade_date)
            eod_count = await copy_sod_to_eod(session, trade_date)

    return count


async def process_day(trade_date: date) -> dict:
    """Process a single trading day."""
    file_path = INCOMING_DIR / f"trades_{trade_date.isoformat()}.jsonl"
    if not file_path.exists():
        print(f"  WARNING: File not found: {file_path}")
        return {}

    result = await run_batch_load(file_path, trade_date)
    return result


async def demo_api_queries() -> None:
    """Demonstrate API queries."""
    from repo_module.db.orm import Position, FutureObligation, LoadReport
    from decimal import Decimal

    print("\n--- API Demo Queries ---")

    # 1. Positions for BANK_A on 2026-05-10
    async with get_session() as session:
        result = await session.execute(
            select(Position).where(
                Position.participant_id == "BANK_A",
                Position.position_date == date(2026, 5, 10),
                Position.status == "EOD",
            ).limit(5)
        )
        positions = result.scalars().all()
        print(f"\n  GET /positions?participant=BANK_A&date=2026-05-10")
        print(f"  → {len(positions)} position records")
        for p in positions[:3]:
            print(f"    {p.instrument_id} [{p.balance_type}]: {p.balance} {p.currency}")

    # 2. Future Obligations for BANK_B
    async with get_session() as session:
        result = await session.execute(
            select(FutureObligation).where(
                FutureObligation.participant_id == "BANK_B",
                FutureObligation.obligation_date >= date(2026, 5, 8),
                FutureObligation.obligation_date <= date(2026, 5, 20),
            ).limit(5)
        )
        obligations = result.scalars().all()
        print(f"\n  GET /obligations?participant=BANK_B&date_from=2026-05-08&date_to=2026-05-20")
        print(f"  → {len(obligations)} obligation records")
        for o in obligations[:3]:
            print(f"    {o.obligation_type} on {o.obligation_date}: {o.amount or o.quantity} {o.currency}")

    # 3. Load report for 2026-05-10
    async with get_session() as session:
        result = await session.execute(
            select(LoadReport).where(LoadReport.eod_date == date(2026, 5, 10))
        )
        report = result.scalar_one_or_none()
        if report:
            print(f"\n  GET /reports/load/2026-05-10")
            print(f"  → status={report.status}, committed={report.committed}, "
                  f"rejected={report.rejected}, duplicates={report.duplicates}, "
                  f"failed_chunks={report.failed_chunks}")

    # 4. Attempt to cancel an ACTIVE trade (expect 409)
    from repo_module.db.orm import Trade
    async with get_session() as session:
        result = await session.execute(
            select(Trade).where(Trade.status == "ACTIVE").limit(1)
        )
        trade = result.scalar_one_or_none()
        if trade:
            print(f"\n  DELETE /trades/{trade.trade_id} (ACTIVE trade)")
            print(f"  → Expected: 409 Conflict (trade is ACTIVE, not NEW)")


async def generate_eod_csv_report(trade_date: date) -> None:
    """Generate EOD Position Report CSV."""
    from repo_module.db.orm import Position

    output_file = OUTPUT_DIR / f"eod_report_{trade_date.isoformat()}.csv"

    async with get_session() as session:
        result = await session.execute(
            select(Position).where(
                Position.position_date == trade_date,
                Position.status == "EOD",
            ).order_by(Position.participant_id, Position.instrument_id)
        )
        positions = result.scalars().all()

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "participant_id", "instrument_id", "balance_type", "currency",
            "position_date", "balance", "frozen_balance", "status", "calculated_at"
        ])
        for p in positions:
            writer.writerow([
                p.participant_id, p.instrument_id, p.balance_type, p.currency,
                p.position_date, p.balance, p.frozen_balance, p.status, p.calculated_at
            ])

    print(f"\n  EOD CSV report saved: {output_file}")
    print(f"  Total positions: {len(positions)}")


async def main() -> None:
    setup_logging(level="WARNING")  # Suppress verbose logs during demo

    print_banner("REPO Module MVP — Demo Mode")
    print(f"  Mode: DEMO (SQLite)")
    print(f"  Incoming dir: {INCOMING_DIR}")
    print(f"  Output dir: {OUTPUT_DIR}")

    # Step 1: Initialize DB
    print_step(1, "Initializing database schema (SQLite)")
    engine = get_engine()
    register_sqlite_events(engine)
    await init_db()
    print("  ✓ Schema created")

    # Step 2: Load reference data
    print_step(2, "Loading reference data (instruments, participants)")
    await load_reference_data(None)
    print("  ✓ Reference data loaded")

    # Steps 3-5: Process each trading day
    total_stats = {"committed": 0, "rejected": 0, "duplicates": 0, "failed_chunks": 0}

    for step_num, trade_date in enumerate(TRADING_DAYS, start=3):
        print_step(step_num, f"Processing trading day {trade_date}")

        # Load SOD balances
        sod_count = await load_sod_for_day(trade_date)
        print(f"  SOD balances loaded: {sod_count} new records")

        # Process trades file
        result = await process_day(trade_date)
        if result:
            print(f"  Batch result: COMMITTED={result.get('committed', 0)}, "
                  f"REJECTED={result.get('rejected', 0)}, "
                  f"DUPLICATES={result.get('duplicates', 0)}, "
                  f"FAILED_CHUNKS={result.get('failed_chunks', 0)}")
            for k in ["committed", "rejected", "duplicates", "failed_chunks"]:
                total_stats[k] += result.get(k, 0)

    # Step 6: Summary and API demo
    print_step(6, "Final summary and API demo queries")
    print(f"\n  ┌─────────────────────────────────────────┐")
    print(f"  │  TOTAL PROCESSING SUMMARY (3 days)      │")
    print(f"  │  COMMITTED:      {total_stats['committed']:>8}                │")
    print(f"  │  REJECTED:       {total_stats['rejected']:>8}                │")
    print(f"  │  DUPLICATES:     {total_stats['duplicates']:>8}                │")
    print(f"  │  FAILED_CHUNKS:  {total_stats['failed_chunks']:>8}                │")
    print(f"  └─────────────────────────────────────────┘")

    await demo_api_queries()

    # Generate EOD CSV report for last day
    print_step(7, "Generating EOD Position Report CSV for 2026-05-10")
    await generate_eod_csv_report(date(2026, 5, 10))

    print_banner("Demo completed successfully!")
    print("  Run 'make serve' to start the REST API server")
    print("  API docs: http://localhost:8000/docs")


if __name__ == "__main__":
    asyncio.run(main())
