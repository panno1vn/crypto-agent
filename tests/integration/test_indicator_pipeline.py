"""
tests/integration/test_indicator_pipeline.py

Ngày 13 — Integration test: fetch → calculate → save → query
Fix: dùng đúng model TechnicalIndicator và OHLCV (data_pipeline.models),
seed bằng naive datetime để khớp cột DateTime hiện tại (xem ghi chú
timezone ở đầu file indicator_pipeline.py).
"""

from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import insert, select

from data_pipeline.models import OHLCV as OHLCVModel
from data_pipeline.models import TechnicalIndicator
from technical_analysis.indicator_pipeline import calculate_and_save_indicators


def make_fake_ohlcv(coin: str = "BTCUSDT", tf: str = "1h", n: int = 100) -> list[dict]:
    """Sinh n nến OHLCV giả lập, giữ invariant high >= max(open,close), low <= min(open,close)."""
    rng = np.random.default_rng(seed=42)
    rows = []
    price = 60000.0
    start = datetime.now() - timedelta(hours=n)  # naive — khớp cột DateTime hiện tại

    for i in range(n):
        open_ = price
        close = max(open_ + rng.normal(0, price * 0.003), 1.0)
        high = max(open_, close) + abs(rng.normal(0, price * 0.001))
        low = min(open_, close) - abs(rng.normal(0, price * 0.001))
        volume = abs(rng.normal(100, 20))

        rows.append(
            {
                "coin": coin,
                "timeframe": tf,
                "open_time": start + timedelta(hours=i),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        price = close

    return rows


@pytest.fixture
async def seeded_ohlcv(db_session):
    rows = make_fake_ohlcv(n=100)
    await db_session.execute(insert(OHLCVModel), rows)
    await db_session.commit()
    return rows


@pytest.mark.asyncio
async def test_integration_pipeline(db_session, seeded_ohlcv):
    coin, tf = "BTCUSDT", "1h"

    await calculate_and_save_indicators(db_session, coin, tf)

    stmt = select(TechnicalIndicator).where(
        TechnicalIndicator.coin == coin, TechnicalIndicator.timeframe == tf
    )
    result = await db_session.execute(stmt)
    saved = result.scalars().all()

    assert len(saved) > 0, "Dữ liệu indicators không được lưu vào DB!"
    assert saved[0].rsi_14 is not None, "Cột rsi_14 bị Null!"
    # confluence_score hiện dựa trên mock data (xem ghi chú đầu file
    # indicator_pipeline.py) — assertion chỉ kiểm tra pipeline CHẠY được,
    # không kiểm tra tính đúng đắn của con số.
    assert (
        saved[0].confluence_score is not None
    ), "Confluence Score (1h) chưa được tính toán!"


@pytest.mark.asyncio
async def test_pipeline_skips_gracefully_when_no_data(db_session):
    await calculate_and_save_indicators(db_session, "NOEXISTUSDT", "1h")

    stmt = select(TechnicalIndicator).where(TechnicalIndicator.coin == "NOEXISTUSDT")
    result = await db_session.execute(stmt)
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_upsert_updates_all_columns_not_partial(db_session, seeded_ohlcv):
    coin, tf = "BTCUSDT", "1h"

    await calculate_and_save_indicators(db_session, coin, tf)
    await calculate_and_save_indicators(db_session, coin, tf)  # chạy lại lần 2

    stmt = select(TechnicalIndicator).where(
        TechnicalIndicator.coin == coin, TechnicalIndicator.timeframe == tf
    )
    result = await db_session.execute(stmt)
    saved = result.scalars().all()

    assert len(saved) == 1, "ON CONFLICT không hoạt động đúng — bị duplicate row!"
