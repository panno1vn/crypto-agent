"""
tests/integration/test_embed_missing.py

2026-10-08 (backfill nợ #14): tin được lấp vào một LỖ có id nhỏ hơn watermark
Chroma của kênh. run_ingestion chỉ lấy id > watermark nên bỏ qua chúng mà vẫn
"success". run_embed_missing so theo chroma_id(kênh, msg_id), không dùng
watermark.

Postgres thật (DB crypto_agent_test, schema dựng từ ORM). Chroma là
EphemeralClient. Embedder thay bằng vector cố định: test kiểm việc CHỌN tin
nào để embed, không kiểm model (tải SBERT mất hàng chục giây).
"""

import uuid
from datetime import datetime

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from data_pipeline.models import Base
from data_pipeline.telegram.historical_scraper import DatabaseWriter, TelegramMessage
from rag import ingestion
from rag.ingestion import chroma_id, run_embed_missing, run_ingestion
from tests.integration.test_telegram_writer import _dsn

chromadb = pytest.importorskip("chromadb")

TEXT = "BTC vượt kháng cự 70k, thị trường đang rất sôi động"


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


@pytest.fixture
def collection(monkeypatch):
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    col = chromadb.EphemeralClient().create_collection(
        f"test-missing-{uuid.uuid4().hex[:8]}"
    )
    monkeypatch.setattr(ingestion, "get_collection", lambda: col)

    class FakeEmbedder:
        def encode(self, texts):
            return np.full((len(texts), 3), 0.1)

    monkeypatch.setattr(ingestion, "get_embedder", lambda: FakeEmbedder())
    return col


async def _insert(channel, ids):
    writer = DatabaseWriter(dsn=_dsn(), batch_size=100)
    await writer.connect()
    try:
        for i in ids:
            await writer.write(
                TelegramMessage(
                    id=i,
                    channel_name=channel,
                    message_text=f"{TEXT} #{i}",
                    created_at=datetime(2026, 9, 1),
                )
            )
        await writer.flush_remaining()
    finally:
        await writer.close()


def _ids(col):
    return set(col.get(include=[])["ids"])


async def test_tin_backfill_duoi_watermark_chi_embed_missing_lay_duoc(
    schema, collection
):
    await _insert("kenh_a", [1, 10])
    await run_ingestion(dsn=_dsn())
    assert _ids(collection) == {"kenh_a:1", "kenh_a:10"}

    # Backfill lấp lỗ: id 5 < watermark 10.
    await _insert("kenh_a", [5])
    await run_ingestion(dsn=_dsn())
    assert chroma_id("kenh_a", 5) not in _ids(collection)  # bug được tái hiện

    stats = await run_embed_missing(dsn=_dsn())
    assert stats["missing_by_channel"] == {"kenh_a": 1}
    assert stats["upserted"] == 1
    assert _ids(collection) == {"kenh_a:1", "kenh_a:5", "kenh_a:10"}

    # Chạy lại: không còn gì thiếu.
    again = await run_embed_missing(dsn=_dsn())
    assert again["missing_by_channel"] == {} and again["upserted"] == 0


async def test_cung_msg_id_kenh_khac_khong_tinh_la_da_embed(schema, collection):
    # kenh_b:7 thiếu dù kenh_a:7 đã có — so theo (kênh, msg_id), không theo id trần.
    await _insert("kenh_a", [7])
    await run_ingestion(dsn=_dsn())
    await _insert("kenh_b", [7])
    stats = await run_embed_missing(dsn=_dsn())
    assert stats["missing_by_channel"] == {"kenh_b": 1}
    assert _ids(collection) == {"kenh_a:7", "kenh_b:7"}
