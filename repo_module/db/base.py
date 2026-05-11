"""
Database engine and session management.
Supports both SQLite (demo) and PostgreSQL (production).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from repo_module.config import get_db_url, is_demo

_engine = None
_session_factory = None


class Base(DeclarativeBase):
    pass


def get_engine():
    global _engine
    if _engine is None:
        url = get_db_url()
        if is_demo():
            _engine = create_async_engine(
                url,
                echo=False,
                connect_args={"check_same_thread": False},
            )
        else:
            _engine = create_async_engine(
                url,
                echo=False,
                pool_size=10,
                max_overflow=20,
            )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables (used in demo mode and tests)."""
    from repo_module.db import orm  # noqa: F401 – registers ORM models
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Install SQLite triggers after tables are created
    if "sqlite" in get_db_url():
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS trg_positions_balance_insert
                BEFORE INSERT ON positions
                FOR EACH ROW
                WHEN NEW.balance < 0
                BEGIN
                    SELECT RAISE(ABORT, 'CHECK constraint failed: chk_positive_balance');
                END
            """))
            await conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS trg_positions_balance_update
                BEFORE UPDATE ON positions
                FOR EACH ROW
                WHEN NEW.balance < 0
                BEGIN
                    SELECT RAISE(ABORT, 'CHECK constraint failed: chk_positive_balance');
                END
            """))


async def close_db():
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
