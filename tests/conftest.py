"""
Pytest configuration and shared fixtures.
Uses SQLite in-memory for all tests.
"""
import asyncio
import os
import pytest
import pytest_asyncio

# Force demo/SQLite mode for tests
os.environ["MODE"] = "demo"
os.environ["SQLITE_PATH"] = ":memory:"

from repo_module.db.base import Base, get_engine, get_session_factory, init_db, close_db
from repo_module.db.orm import Instrument, Participant, register_sqlite_events


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db():
    """
    Set up a fresh in-memory SQLite database for each test.
    """
    # Reset engine to get fresh in-memory DB
    import repo_module.db.base as db_base
    db_base._engine = None
    db_base._session_factory = None

    engine = get_engine()
    register_sqlite_events(engine)
    await init_db()

    yield engine

    await close_db()


@pytest_asyncio.fixture(scope="function")
async def session(db):
    """Provide a database session for tests."""
    async with get_session() as s:
        yield s


@pytest_asyncio.fixture(scope="function")
async def populated_db(db):
    """Database with reference data (instruments and participants)."""
    from datetime import date
    from repo_module.db.base import get_session

    async with get_session() as session:
        async with session.begin():
            # Add instruments
            instruments = [
                Instrument(
                    instrument_id="RU000A0ZYJT2",
                    short_name="ОФЗ-26225",
                    instrument_type="BOND",
                    currency="RUB",
                    repo_eligible=True,
                    settlement_mode="T+0",
                    day_count_convention="ACT/365",
                    is_active=True,
                    valid_from=date(2020, 1, 1),
                ),
                Instrument(
                    instrument_id="RU000A101NJ6",
                    short_name="ОФЗ-26240",
                    instrument_type="BOND",
                    currency="RUB",
                    repo_eligible=True,
                    settlement_mode="T+0",
                    day_count_convention="ACT/365",
                    is_active=True,
                    valid_from=date(2021, 1, 1),
                ),
                Instrument(
                    instrument_id="RU000A117NOREPO",
                    short_name="НЕ-РЕПО-БОНД",
                    instrument_type="BOND",
                    currency="RUB",
                    repo_eligible=False,
                    settlement_mode="T+0",
                    day_count_convention="ACT/365",
                    is_active=True,
                    valid_from=date(2024, 1, 1),
                ),
            ]
            for instr in instruments:
                session.add(instr)

            # Add participants
            participants = [
                Participant(participant_id="BANK_A", name="Банк А", is_active=True),
                Participant(participant_id="BANK_B", name="Банк Б", is_active=True),
                Participant(participant_id="BANK_C", name="Банк В", is_active=True),
                Participant(participant_id="CORP_1", name="Корп 1", is_active=True),
            ]
            for p in participants:
                session.add(p)

    yield db


# Import get_session for use in fixtures
from repo_module.db.base import get_session
