"""
tests/unit/test_ohlcv_pipeline.py

Ngày 7 — Unit Tests cho OHLCV Pipeline
========================================
Covers:
  - parse_kline: timezone, giá trị hợp lệ
  - OHLCV validator: giá âm, volume == 0, volume âm
  - validate_gaps: không xóa nến, chỉ warn
"""

from datetime import datetime, timezone

import pytest

from data_pipeline.binance.ohlcv_pipeline import (OHLCV, parse_kline,
                                                  validate_gaps)


# ---------------------------------------------------------------------------
# Fixture — raw kline giả từ Binance
# ---------------------------------------------------------------------------
def make_raw_kline(
    open_time_ms: int = 1_700_000_000_000,
    open: float = 35000.0,
    high: float = 36000.0,
    low: float = 34000.0,
    close: float = 35500.0,
    volume: float = 100.0,
) -> list:
    """Tạo raw kline list theo format Binance (chỉ dùng 6 field đầu)."""
    return [open_time_ms, str(open), str(high), str(low), str(close), str(volume)]


# ---------------------------------------------------------------------------
# Group 1: parse_kline — timezone awareness (FIX 1)
# ---------------------------------------------------------------------------


def test_parse_kline_returns_utc_aware_datetime():
    """
    open_time phải là timezone-aware UTC.

    Đây là FIX quan trọng nhất: trước đây code gọi .replace(tzinfo=None)
    khiến datetime mất timezone → PostgreSQL TIMESTAMPTZ hiểu sai.
    """
    kline = make_raw_kline(open_time_ms=1_700_000_000_000)
    result = parse_kline(kline, "BTCUSDT", "1h")

    # Phải có tzinfo — không được là None
    assert result.open_time.tzinfo is not None, (
        "open_time phải là timezone-aware. "
        "Đừng gọi .replace(tzinfo=None) sau khi convert UTC!"
    )
    assert result.open_time.tzinfo == timezone.utc


def test_parse_kline_correct_values():
    """parse_kline chuyển đúng tất cả giá trị từ raw kline."""
    kline = make_raw_kline(
        open=35000.0, high=36000.0, low=34000.0, close=35500.0, volume=100.0
    )
    result = parse_kline(kline, "ETHUSDT", "4h")

    assert result.coin == "ETHUSDT"
    assert result.timeframe == "4h"
    assert result.open == 35000.0
    assert result.high == 36000.0
    assert result.low == 34000.0
    assert result.close == 35500.0
    assert result.volume == 100.0


# ---------------------------------------------------------------------------
# Group 2: OHLCV Validators
# ---------------------------------------------------------------------------


def test_ohlcv_rejects_negative_price():
    """Giá âm phải bị reject — không thể xảy ra trong thực tế."""
    with pytest.raises(ValueError, match="âm"):
        OHLCV(
            coin="BTCUSDT",
            timeframe="1h",
            open_time=datetime.now(timezone.utc),
            open=-1.0,
            high=100.0,
            low=50.0,
            close=80.0,
            volume=10.0,
        )


def test_ohlcv_accepts_zero_volume():
    """
    volume == 0 PHẢI được chấp nhận (FIX 3).

    Lý do: coin thanh khoản thấp hoặc thị trường đứng yên có thể có
    volume = 0 trong một nến. Reject sẽ tạo gap giả trong chuỗi OHLCV,
    làm RSI/MACD tính sai ở tuần 2.
    """
    candle = OHLCV(
        coin="XRPUSDT",
        timeframe="15m",
        open_time=datetime.now(timezone.utc),
        open=0.5,
        high=0.51,
        low=0.49,
        close=0.50,
        volume=0.0,
    )
    assert candle.volume == 0.0, "volume == 0 phải được chấp nhận"


def test_ohlcv_rejects_negative_volume():
    """Volume âm là dữ liệu lỗi thật sự — phải bị reject."""
    with pytest.raises(ValueError):
        OHLCV(
            coin="BTCUSDT",
            timeframe="1h",
            open_time=datetime.now(timezone.utc),
            open=35000.0,
            high=36000.0,
            low=34000.0,
            close=35500.0,
            volume=-5.0,
        )


def test_ohlcv_accepts_valid_candle():
    """Nến hợp lệ đầy đủ phải được tạo thành công."""
    candle = OHLCV(
        coin="BTCUSDT",
        timeframe="1h",
        open_time=datetime.now(timezone.utc),
        open=35000.0,
        high=36000.0,
        low=34000.0,
        close=35500.0,
        volume=150.0,
    )
    assert candle.coin == "BTCUSDT"


# ---------------------------------------------------------------------------
# Group 3: validate_gaps
# ---------------------------------------------------------------------------


def make_ohlcv_at(ts: datetime) -> OHLCV:
    """Helper tạo OHLCV với open_time tùy chọn."""
    return OHLCV(
        coin="BTCUSDT",
        timeframe="1h",
        open_time=ts,
        open=35000.0,
        high=36000.0,
        low=34000.0,
        close=35500.0,
        volume=10.0,
    )


def test_validate_gaps_returns_all_candles():
    """
    validate_gaps KHÔNG xóa nến — chỉ cảnh báo.

    Dù có gap bất thường, tất cả nến vẫn phải được trả về.
    """
    from datetime import timedelta

    base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [
        make_ohlcv_at(base),
        make_ohlcv_at(base + timedelta(hours=1)),
        make_ohlcv_at(base + timedelta(hours=10)),  # gap lớn bất thường
        make_ohlcv_at(base + timedelta(hours=11)),
    ]

    result = validate_gaps(candles, "1h")
    # Phải trả về đúng 4 nến, không bị mất nến nào
    assert len(result) == 4


def test_validate_gaps_single_candle():
    """Chỉ có 1 nến → không có gì để compare → trả về nguyên."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = [make_ohlcv_at(base)]
    result = validate_gaps(candles, "1h")
    assert len(result) == 1
