"""
BatchService: chunked processing of REPO trade files.
Implements SAVEPOINT-based isolation for business errors,
chunk-level rollback for system errors.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator, Optional

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from repo_module.config import get_chunk_size, get_max_retries
from repo_module.db.base import get_session
from repo_module.db.orm import LoadReport, RawTrade, RejectedTrade, Trade
from repo_module.models.domain import IncomingTrade, RejectionType, TradeStatus
from repo_module.services.position_service import (
    InsufficientBalanceError,
    apply_leg1_settlements,
    copy_sod_to_eod,
    create_future_obligations,
)
from repo_module.services.trade_service import (
    DuplicateError,
    ValidationError,
    build_trade_create,
    check_duplicate,
    insert_trade,
    validate_trade,
)

logger = logging.getLogger(__name__)


class BatchProcessor:
    """
    Processes a REPO trade file in chunks.

    Error isolation strategy:
    - Business errors (validation, duplicate, insufficient balance):
        SAVEPOINT per record → rollback to savepoint → record in rejected_trades → continue chunk
    - System errors (IO, DB constraint violation):
        ROLLBACK entire chunk → retry up to max_retries → if still failing: failed_chunks++
    """

    def __init__(self, chunk_size: int = None, max_retries: int = None):
        self.chunk_size = chunk_size or get_chunk_size()
        self.max_retries = max_retries or get_max_retries()

    async def process_file(
        self,
        file_path: str | Path,
        eod_date: date,
        report_id: Optional[int] = None,
    ) -> dict:
        """
        Process a JSON Lines file of trades.

        Returns a summary dict with committed, rejected, duplicates, failed_chunks.
        """
        file_path = Path(file_path)
        logger.info(f"Starting batch processing: {file_path}, eod_date={eod_date}")

        stats = {
            "total_records": 0,
            "committed": 0,
            "rejected": 0,
            "duplicates": 0,
            "failed_chunks": 0,
        }

        # Read all lines
        lines = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)

        stats["total_records"] = len(lines)

        # Process in chunks
        chunk_num = 0
        for i in range(0, len(lines), self.chunk_size):
            chunk = lines[i: i + self.chunk_size]
            chunk_num += 1
            chunk_stats = await self._process_chunk_with_retry(
                chunk, chunk_num, eod_date
            )
            stats["committed"] += chunk_stats["committed"]
            stats["rejected"] += chunk_stats["rejected"]
            stats["duplicates"] += chunk_stats["duplicates"]
            stats["failed_chunks"] += chunk_stats["failed_chunks"]

        logger.info(
            f"Batch processing complete: {stats}",
            extra={"eod_date": eod_date, "operation": "batch_complete"},
        )
        return stats

    async def _process_chunk_with_retry(
        self,
        chunk: list[str],
        chunk_id: int,
        eod_date: date,
    ) -> dict:
        """Process a chunk with retry logic for system errors."""
        for attempt in range(1, self.max_retries + 1):
            try:
                result = await self._process_chunk(chunk, chunk_id, eod_date)
                return result
            except Exception as exc:
                logger.error(
                    f"Chunk {chunk_id} attempt {attempt} failed: {exc}",
                    extra={"chunk_id": chunk_id, "attempt": attempt},
                    exc_info=True,
                )
                if attempt == self.max_retries:
                    logger.critical(
                        f"Chunk {chunk_id} failed after {self.max_retries} attempts. Skipping.",
                        extra={"chunk_id": chunk_id},
                    )
                    return {
                        "committed": 0,
                        "rejected": 0,
                        "duplicates": 0,
                        "failed_chunks": 1,
                    }
                await asyncio.sleep(1)  # brief delay before retry

    async def _process_chunk(
        self,
        chunk: list[str],
        chunk_id: int,
        eod_date: date,
    ) -> dict:
        """
        Process a single chunk within one DB transaction.
        Uses SAVEPOINT per record for business error isolation.
        """
        stats = {"committed": 0, "rejected": 0, "duplicates": 0, "failed_chunks": 0}

        async with get_session() as session:
            async with session.begin():
                for idx, line in enumerate(chunk):
                    record_num = idx + 1
                    sp_name = f"sp_record_{chunk_id}_{record_num}"

                    # Create SAVEPOINT
                    await session.execute(text(f"SAVEPOINT {sp_name}"))

                    try:
                        result = await self._process_record(
                            session, line, chunk_id, eod_date
                        )
                        if result == "committed":
                            stats["committed"] += 1
                        elif result == "duplicate":
                            stats["duplicates"] += 1
                            await session.execute(text(f"ROLLBACK TO SAVEPOINT {sp_name}"))
                        await session.execute(text(f"RELEASE SAVEPOINT {sp_name}"))

                    except (ValidationError, InsufficientBalanceError) as biz_err:
                        # Business error: rollback to savepoint, record rejection, continue
                        await session.execute(text(f"ROLLBACK TO SAVEPOINT {sp_name}"))
                        await self._record_rejection(
                            session, line, biz_err, chunk_id
                        )
                        await session.execute(text(f"RELEASE SAVEPOINT {sp_name}"))
                        stats["rejected"] += 1
                        logger.warning(
                            f"Record rejected: {biz_err}",
                            extra={"chunk_id": chunk_id, "record_num": record_num},
                        )

                    except (IntegrityError, DBAPIError) as sys_err:
                        # System/DB error: rollback entire chunk
                        await session.execute(text(f"ROLLBACK TO SAVEPOINT {sp_name}"))
                        await session.execute(text(f"RELEASE SAVEPOINT {sp_name}"))
                        logger.error(
                            f"System error in chunk {chunk_id}: {sys_err}",
                            extra={"chunk_id": chunk_id},
                            exc_info=True,
                        )
                        raise  # triggers chunk-level retry

        return stats

    async def _process_record(
        self,
        session: AsyncSession,
        line: str,
        chunk_id: int,
        eod_date: date,
    ) -> str:
        """
        Process a single trade record.
        Returns 'committed', 'duplicate', or raises an error.
        """
        # Parse JSON
        try:
            raw_data = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValidationError(RejectionType.VALIDATION_ERROR, f"Invalid JSON: {e}")

        # Validate schema
        try:
            incoming = IncomingTrade(**raw_data)
        except PydanticValidationError as e:
            raise ValidationError(RejectionType.VALIDATION_ERROR, str(e))

        idempotency_key = f"{incoming.external_trade_id}_{incoming.trade_date.isoformat()}"

        # Dedup check
        if await check_duplicate(session, idempotency_key):
            logger.info(f"Duplicate trade: {idempotency_key}")
            return "duplicate"

        # Store raw trade
        raw_trade = RawTrade(
            idempotency_key=idempotency_key,
            source="BATCH",
            payload=raw_data,
            processing_status="PROCESSING",
        )
        session.add(raw_trade)
        await session.flush()

        # Validate against reference data
        await validate_trade(session, incoming)

        # Build trade with role mapping and Leg2 calculation
        trade_create = await build_trade_create(session, incoming, raw_trade.id, eod_date)

        # Insert trade
        trade = await insert_trade(session, trade_create)

        # Apply Leg 1 settlements if ACTIVE
        if trade.status == TradeStatus.ACTIVE.value:
            await apply_leg1_settlements(session, trade, eod_date, chunk_id)

        # Create future obligations
        await create_future_obligations(session, trade)

        # Mark raw trade as processed
        raw_trade.processing_status = "COMMITTED"
        await session.flush()

        return "committed"

    async def _record_rejection(
        self,
        session: AsyncSession,
        line: str,
        error: Exception,
        chunk_id: int,
    ) -> None:
        """Record a rejected trade in rejected_trades table."""
        try:
            payload = json.loads(line)
        except Exception:
            payload = {"raw": line}

        if isinstance(error, ValidationError):
            rejection_type = error.rejection_type.value
            detail = error.detail
            idempotency_key = None
            if isinstance(payload, dict):
                ext_id = payload.get("external_trade_id", "")
                td = payload.get("trade_date", "")
                idempotency_key = f"{ext_id}_{td}" if ext_id and td else None
        elif isinstance(error, InsufficientBalanceError):
            rejection_type = RejectionType.INSUFFICIENT_BALANCE.value
            detail = str(error)
            idempotency_key = None
            if isinstance(payload, dict):
                ext_id = payload.get("external_trade_id", "")
                td = payload.get("trade_date", "")
                idempotency_key = f"{ext_id}_{td}" if ext_id and td else None
        else:
            rejection_type = RejectionType.SYSTEM_ERROR.value
            detail = str(error)
            idempotency_key = None

        rejected = RejectedTrade(
            idempotency_key=idempotency_key,
            rejection_type=rejection_type,
            rejection_detail=detail,
            payload=payload,
            chunk_id=chunk_id,
        )
        session.add(rejected)
        await session.flush()


async def run_batch_load(
    file_path: str | Path,
    eod_date: date,
) -> dict:
    """
    High-level entry point for batch loading.
    Creates/updates load_report, runs processor, returns stats.
    """
    file_path = Path(file_path)
    started_at = datetime.utcnow()

    # Create load report
    async with get_session() as session:
        async with session.begin():
            # Check if report already exists for this date
            result = await session.execute(
                select(LoadReport).where(LoadReport.eod_date == eod_date)
            )
            report = result.scalar_one_or_none()
            if report is None:
                report = LoadReport(
                    eod_date=eod_date,
                    file_name=file_path.name,
                    started_at=started_at,
                    status="IN_PROGRESS",
                )
                session.add(report)
                await session.flush()
            report_id = report.report_id

    # Run processor
    processor = BatchProcessor()
    try:
        stats = await processor.process_file(file_path, eod_date, report_id)
        final_status = "COMPLETED" if stats["failed_chunks"] == 0 else "COMPLETED_WITH_ERRORS"
    except Exception as exc:
        logger.error(f"Batch load failed: {exc}", exc_info=True)
        stats = {"total_records": 0, "committed": 0, "rejected": 0, "duplicates": 0, "failed_chunks": 1}
        final_status = "FAILED"

    # Update load report
    async with get_session() as session:
        async with session.begin():
            result = await session.execute(
                select(LoadReport).where(LoadReport.report_id == report_id)
            )
            report = result.scalar_one()
            report.finished_at = datetime.utcnow()
            report.total_records = stats["total_records"]
            report.committed = stats["committed"]
            report.rejected = stats["rejected"]
            report.duplicates = stats["duplicates"]
            report.failed_chunks = stats["failed_chunks"]
            report.status = final_status

    return {**stats, "eod_date": eod_date, "status": final_status, "report_id": report_id}
