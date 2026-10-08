"""
tests/integration/test_telegram_writer.py

Nợ #15 (2026-10-08): msg_id Telegram đánh số RIÊNG theo kênh. Khóa cũ
PRIMARY KEY (id) + ON CONFLICT (id) DO NOTHING bỏ im lặng tin kênh B trùng
id với kênh A (ước tính ~2.300 tin mất). Test chạy DatabaseWriter thật trên
DB crypto_agent_test (schema dựng từ ORM, như test_sentiment_pipeline.py).
"""

import os
from datetime import datetime

import asyncpg
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

from data_pipeline.models import Base
from data_pipeline.telegram.historical_scraper import DatabaseWriter, TelegramMessage

load_dotenv()


def _dsn() -> str:
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/crypto_agent_test"
    )


@pytest_asyncio.fixture
async def schema():
    engine = create_async_engine(
        _dsn().replace("postgresql://", "postgresql+asyncpg://")
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _msg(channel: str, msg_id: int, text: str) -> TelegramMessage:
    return TelegramMessage(
        id=msg_id,
        channel_name=channel,
        message_text=text,
        created_at=datetime(2026, 10, 8, 0, 0),
    )


async def _rows() -> list[tuple[str, int, str]]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT channel_name, id, message_text FROM telegram_messages "
            "ORDER BY channel_name"
        )
    finally:
        await conn.close()
    return [(r["channel_name"], r["id"], r["message_text"]) for r in rows]


@pytest.mark.asyncio
async def test_hai_kenh_cung_msg_id_deu_duoc_luu(schema):
    writer = DatabaseWriter(dsn=_dsn(), batch_size=100)
    await writer.connect()
    try:
        await writer.write(_msg("kenh_a", 4500, "Tin của kênh A về BTC"))
        await writer.write(_msg("kenh_b", 4500, "Tin của kênh B về ETH"))
        await writer.flush_remaining()
    finally:
        await writer.close()

    assert await _rows() == [
        ("kenh_a", 4500, "Tin của kênh A về BTC"),
        ("kenh_b", 4500, "Tin của kênh B về ETH"),
    ]


@pytest.mark.asyncio
async def test_ghi_lai_cung_kenh_cung_id_van_idempotent(schema):
    writer = DatabaseWriter(dsn=_dsn(), batch_size=100)
    await writer.connect()
    try:
        await writer.write(_msg("kenh_a", 4500, "Bản ghi đầu tiên của tin"))
        await writer.flush_remaining()
        await writer.write(_msg("kenh_a", 4500, "Bản ghi lặp lại của tin"))
        await writer.flush_remaining()
    finally:
        await writer.close()

    # DO NOTHING: bản đầu được giữ, chạy lại catch-up không nhân đôi.
    assert await _rows() == [("kenh_a", 4500, "Bản ghi đầu tiên của tin")]
