"""
tests/unit/test_trend.py

Ngày 10 — Unit tests cho Trend Detection (4 tests)
"""

import numpy as np
import pandas as pd
import pytest

from technical_analysis.trend import detect_trend


def _make_df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": prices})


def test_detect_trend_strong_uptrend():
    """Giá tăng đều, dốc rõ ràng -> uptrend / strong."""
    prices = list(np.linspace(50, 150, 80))
    df = _make_df(prices)
    signal = detect_trend(df)
    assert signal.direction == "uptrend"
    assert signal.strength == "strong"


def test_detect_trend_strong_downtrend():
    """Giá giảm đều, dốc rõ ràng -> downtrend / strong."""
    prices = list(np.linspace(150, 50, 80))
    df = _make_df(prices)
    signal = detect_trend(df)
    assert signal.direction == "downtrend"
    assert signal.strength == "strong"


def test_detect_trend_insufficient_data_raises():
    """
    Ít hơn 51 dòng (ema_long_period=50 + 1) -> phải raise ValueError,
    KHÔNG được âm thầm trả về 'sideways'.
    """
    df = _make_df([100.0] * 30)
    with pytest.raises(ValueError, match="Cần ít nhất"):
        detect_trend(df)


def test_detect_trend_nan_in_close_raises():
    """
    Đủ số dòng nhưng dữ liệu đầu chuỗi có NaN khiến EMA/RSI ra NaN
    -> phải raise ValueError rõ ràng, không rơi xuống 'sideways' im lặng.
    """
    prices = [np.nan] * 20 + list(np.linspace(90, 110, 40))
    df = _make_df(prices)
    with pytest.raises(ValueError, match="NaN"):
        detect_trend(df)
