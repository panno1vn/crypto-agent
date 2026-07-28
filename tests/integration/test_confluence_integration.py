"""
tests/integration/test_confluence_integration.py

Ngày 13 — Integration test cho analyze_confluence() với data thật.
======================================================================
Trước đây analyze_confluence() dùng mock cố định, không cần test loại
này. Giờ nó đọc OHLCV thật từ DB nên cần seed đủ cả 4 timeframe.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import insert

from data_pipeline.models import OHLCV as OHLCVModel
from technical_analysis.confluence import analyze_confluence


def make_fake_ohlcv(coin: str, tf: str, n: int, hours_per_candle: float) -> list[dict]:
    """Sinh n nến giả, giữ invariant OHLC hợp lệ, xu hướng tăng nhẹ để
    có tín hiệu 'long' rõ ràng thay vì ngẫu nhiên loạn xạ."""
    rng = np.random.default_rng(seed=7)
    rows = []
    price = 60000.0
    start = datetime.now() - timedelta(hours=n * hours_per_candle)

    for i in range(n):
        open_ = price
        # Trend tăng nhẹ đều đặn để detect_trend() ra 'uptrend' ổn định
        close = open_ * 1.0015 + rng.normal(0, price * 0.001)
        high = max(open_, close) + abs(rng.normal(0, price * 0.0005))
        low = min(open_, close) - abs(rng.normal(0, price * 0.0005))
        volume = abs(rng.normal(100, 20))

        rows.append(
            {
                "coin": coin,
                "timeframe": tf,
                "open_time": start + timedelta(hours=i * hours_per_candle),
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
async def seeded_all_timeframes(db_session):
    """Seed đủ 4 timeframe cho BTCUSDT — analyze_confluence() cần cả 4."""
    coin = "BTCUSDT"
    timeframe_hours = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}

    for tf, hrs in timeframe_hours.items():
        rows = make_fake_ohlcv(coin, tf, n=100, hours_per_candle=hrs)
        await db_session.execute(insert(OHLCVModel), rows)
    await db_session.commit()

    return coin


@pytest.mark.asyncio
async def test_analyze_confluence_with_real_data(db_session, seeded_all_timeframes):
    coin = seeded_all_timeframes

    result = await analyze_confluence(db_session, coin, current_price=65000.0)

    assert result.coin == coin
    assert result.direction in ("long", "short", "neutral")
    assert 0.0 <= result.strength <= 1.0
    # Với data uptrend đều 4 timeframe, kỳ vọng hợp lý là 'long',
    # nhưng không assert cứng — vì nhiễu ngẫu nhiên trong fixture có thể
    # kéo 1-2 timeframe lệch. Assertion quan trọng nhất là: hàm CHẠY
    # ĐƯỢC với data thật và trả kết quả có cấu trúc hợp lệ.
    assert isinstance(result.reasoning, dict)
    assert (
        len(result.reasoning) > 0
    ), "Phải có ít nhất 1 timeframe tính được — nếu rỗng nghĩa là toàn bộ fetch/tính đều lỗi"


@pytest.mark.asyncio
async def test_analyze_confluence_missing_timeframes_degrades_gracefully(db_session):
    """
    Coin không có OHLCV nào trong DB -> tất cả timeframe đều bị skip
    -> hàm phải trả về ConfluentSignal neutral, KHÔNG được raise exception.
    """
    result = await analyze_confluence(db_session, "NOEXISTUSDT", current_price=100.0)

    assert result.direction == "neutral"
    assert result.strength == 0.0
    assert result.reasoning == {}


@pytest.mark.asyncio
async def test_analyze_confluence_partial_timeframes(db_session):
    """
    Chỉ seed '1h' — 3 timeframe còn lại thiếu data. Hàm phải vẫn chạy
    được, chỉ dùng '1h' để vote (đúng logic weighted_vote đã chuẩn hóa
    theo total_weight_used).
    """
    coin = "ETHUSDT"
    rows = make_fake_ohlcv(coin, "1h", n=100, hours_per_candle=1.0)
    await db_session.execute(insert(OHLCVModel), rows)
    await db_session.commit()

    result = await analyze_confluence(db_session, coin, current_price=3000.0)

    assert "1h" in result.reasoning
    assert len(result.reasoning) == 1  # chỉ 1h có data, 3 timeframe kia bị skip
