# conftest.py
import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from data_pipeline.models import (  # Base thật, chứa TelegramChannel, OHLCV, TechnicalIndicator...
    Base,
)

TEST_DB_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/crypto_agent_test",
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DB_DSN)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
