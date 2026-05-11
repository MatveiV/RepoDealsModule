"""
REST API routes for REPO Module.
Read-only GET endpoints + DELETE (cancel trade) + POST (batch load trigger).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from sqlalchemy import select

from repo_module.batch.processor import run_batch_load
from repo_module.config import get_incoming_dir, get_output_dir, is_demo
from repo_module.db.base import get_session
from repo_module.db.orm import (
    FutureObligation, LoadReport, Position, RejectedTrade, Trade,
)
from repo_module.models.domain import (
    LoadReportOut, ObligationOut, PositionOut, TradeOut,
)
from repo_module.services.trade_service import cancel_trade

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Trades ──────────────────────────────────────────────────────────────────

@router.get("/trades", response_model=list[TradeOut])
async def list_trades(
    participant: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
):
    """List trades with optional filters."""
    async with get_session() as session:
        q = select(Trade)
        if participant:
            q = q.where(
                (Trade.participant_id == participant) | (Trade.counterparty_id == participant)
            )
        if status:
            q = q.where(Trade.status == status)
        if date_from:
            q = q.where(Trade.trade_date >= date_from)
        if date_to:
            q = q.where(Trade.trade_date <= date_to)
        q = q.order_by(Trade.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(q)
        trades = result.scalars().all()
        return [TradeOut.model_validate(t) for t in trades]


@router.get("/trades/{trade_id}", response_model=TradeOut)
async def get_trade(trade_id: UUID):
    """Get a single trade by UUID."""
    async with get_session() as session:
        trade = await session.get(Trade, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
        return TradeOut.model_validate(trade)


@router.delete("/trades/{trade_id}", status_code=200)
async def cancel_trade_endpoint(trade_id: UUID):
    """
    Cancel a trade in NEW status.
    Returns 409 if trade is not in NEW status or leg1_settlement_date has passed.
    """
    async with get_session() as session:
        async with session.begin():
            try:
                trade = await cancel_trade(session, str(trade_id), date.today())
                return {"trade_id": str(trade.trade_id), "status": trade.status}
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))


# ─── Positions ────────────────────────────────────────────────────────────────

@router.get("/positions", response_model=list[PositionOut])
async def list_positions(
    participant: Optional[str] = Query(None),
    position_date: Optional[date] = Query(None),
    instrument: Optional[str] = Query(None),
    balance_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="SOD or EOD"),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
):
    """List positions with optional filters."""
    async with get_session() as session:
        q = select(Position)
        if participant:
            q = q.where(Position.participant_id == participant)
        if position_date:
            q = q.where(Position.position_date == position_date)
        if instrument:
            q = q.where(Position.instrument_id == instrument)
        if balance_type:
            q = q.where(Position.balance_type == balance_type)
        if status:
            q = q.where(Position.status == status)
        q = q.order_by(Position.position_date.desc(), Position.participant_id).limit(limit).offset(offset)
        result = await session.execute(q)
        positions = result.scalars().all()
        return [PositionOut.model_validate(p) for p in positions]


# ─── Future Obligations ───────────────────────────────────────────────────────

@router.get("/obligations", response_model=list[ObligationOut])
async def list_obligations(
    participant: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
):
    """List future obligations."""
    async with get_session() as session:
        q = select(FutureObligation)
        if participant:
            q = q.where(FutureObligation.participant_id == participant)
        if date_from:
            q = q.where(FutureObligation.obligation_date >= date_from)
        if date_to:
            q = q.where(FutureObligation.obligation_date <= date_to)
        if status:
            q = q.where(FutureObligation.status == status)
        q = q.order_by(FutureObligation.obligation_date, FutureObligation.participant_id).limit(limit).offset(offset)
        result = await session.execute(q)
        obligations = result.scalars().all()
        return [ObligationOut.model_validate(o) for o in obligations]


# ─── Reports ─────────────────────────────────────────────────────────────────

@router.get("/reports/eod")
async def get_eod_report(
    eod_date: date = Query(...),
    format: str = Query("json", description="json or csv"),
):
    """Get EOD position report for a date."""
    async with get_session() as session:
        result = await session.execute(
            select(Position).where(
                Position.position_date == eod_date,
                Position.status == "EOD",
            ).order_by(Position.participant_id, Position.instrument_id)
        )
        positions = result.scalars().all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "participant_id", "instrument_id", "balance_type", "currency",
            "position_date", "balance", "frozen_balance", "status", "calculated_at"
        ])
        for p in positions:
            writer.writerow([
                p.participant_id, p.instrument_id, p.balance_type, p.currency,
                p.position_date, p.balance, p.frozen_balance, p.status, p.calculated_at
            ])
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=eod_report_{eod_date}.csv"},
        )

    return [PositionOut.model_validate(p) for p in positions]


@router.get("/reports/load/{eod_date}", response_model=LoadReportOut)
async def get_load_report(eod_date: date):
    """Get batch load report for a specific date."""
    async with get_session() as session:
        result = await session.execute(
            select(LoadReport).where(LoadReport.eod_date == eod_date)
        )
        report = result.scalar_one_or_none()
        if not report:
            raise HTTPException(status_code=404, detail=f"No load report for {eod_date}")
        return LoadReportOut.model_validate(report)


# ─── Batch Load ───────────────────────────────────────────────────────────────

@router.post("/batch/load")
async def trigger_batch_load(
    eod_date: date = Query(...),
    file_name: Optional[str] = Query(None, description="File name in incoming directory"),
    background_tasks: BackgroundTasks = None,
):
    """
    Manually trigger batch load for a given EOD date.
    In demo mode, reads from demo_data/incoming/.
    """
    import os
    from pathlib import Path

    incoming_dir = get_incoming_dir()
    if not incoming_dir:
        raise HTTPException(status_code=400, detail="Incoming directory not configured (production mode requires SFTP/S3)")

    if file_name:
        file_path = Path(incoming_dir) / file_name
    else:
        # Auto-detect file for the date
        file_path = Path(incoming_dir) / f"trades_{eod_date}.jsonl"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    # Run synchronously for simplicity (background task in production)
    result = await run_batch_load(file_path, eod_date)
    return result
